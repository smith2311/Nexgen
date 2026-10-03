"""OpenAI-backed 3PL bid evaluator for VendorLink.

Evaluations run in-process through the OpenAI client using ``TEST_OPENAI_API_KEY``
from the environment. The page polls the stored evaluation status while the
background worker completes the request.

The evaluator follows one decision rule — recommend the cheapest vendor
that can ALSO deliver every service the request requires — and the same
result shape: explicit *points* (short bullet reasons) both for the overall
decision and for each ranked vendor, so buyers can see exactly why a vendor
was picked for the request that was raised. :func:`apply_evaluation_result`
is the one place that shape gets written onto a ``VendorEvaluation`` row.

Entry points:
    run_evaluation_async(vendor_request)   # in-process OpenAI call
    ask_meril_gpt(question)                # free-text Q&A across ALL requests
"""

import json
import logging
import os
import re
import threading
from collections import defaultdict

from . import bidcompare
from .capabilities import capability_rows

logger = logging.getLogger(__name__)

#: gpt-4o-mini across VendorLink. It supports the two things this module
#: actually needs — strict json_schema structured output for the evaluator
#: and the report narrative, and vision for image attachments in Meril GPT
#: — at a fraction of the cost per call, and these are short, well-scoped
#: extractions rather than open-ended reasoning.
DEFAULT_MODEL = "gpt-4o-mini"

# ── OpenAI client ───────────────────────────────────────────────────────────


def _get_client():
    """Return an OpenAI client, or None when the key / package is unavailable."""
    key = os.getenv("TEST_OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed — bid evaluation unavailable")
        return None
    return OpenAI(api_key=key)


#: One style rule for everything the model writes in prose.
#:
#: Appended to all three prompts rather than pasted into each: the bid
#: evaluator, the MerilGPT chat and the report key points are read by the
#: same people, and three separate copies of a style rule drift apart the
#: first time one is edited. reports.py imports this one.
PLAIN_ENGLISH = """WRITE IN SIMPLE, EASY ENGLISH
Everything you write is read by supply-chain, warehouse and procurement staff
across several countries, many of whom do not speak English as a first
language. Write so that any of them can read it once and understand it.

- Short sentences. One idea each. Under about 20 words.
- Everyday words. Say "cheapest" not "most cost-effective"; "cannot do" not
  "lacks the capability to"; "we should pick" not "it is recommended that";
  "missing" not "non-compliant with the requirement".
- Active voice, and name who does what. "EuroCold cannot store at 2-8 °C",
  not "temperature capability is not evidenced".
- No business filler: leverage, synergy, robust, holistic, going forward,
  in order to, as per, kindly note, it may be noted that.
- Spell out an abbreviation the first time unless it is one the reader uses
  every day (RFI, SAP, GDP are fine; EXW, DDP, 3PL-4PL are not).
- Keep the numbers exact. Simple English means simpler words, never vaguer
  facts: always give the figure and its currency ("EUR 28,500", not "around
  twenty-eight thousand").
- If something is genuinely uncertain, say so in plain words: "we do not know
  yet", not "this remains subject to confirmation".
"""


# ── The decision brain ──────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are the 3PL vendor bid evaluator for Meril Life Sciences. You are given ONE procurement request and every vendor's price bid(s) for it, and you must decide which single vendor to recommend.

THE DECISION RULE (this is the whole job):
Recommend the vendor with the BEST (lowest) price that can ALSO deliver EVERY service the request requires, on ALL terms. Price alone never wins — a cheaper vendor that cannot deliver all required services must NOT be recommended over a slightly costlier vendor that can.

INPUT SHAPE:
- `requirements` = the request's warehouse space, storage temperature, monthly/inbound/outbound volumes, product_verticals and scope_of_work (scope_of_work may be HTML — read the text).
- `service_catalogue` = the full list of services a vendor can be evaluated on.
- Each vendor has: `services` (a {service name: true/false} matrix), `services_provided`, `services_missing`, `score` (facility/support/compliance/delivery/overall ratings 1-5, is_blacklisted, blacklist_reason, notes — score may be {} meaning unrated), `best_bid_total`, `best_bid_currency`, `best_bid_is_staff_selected`, and every `bids` revision.

STEP 1 — INFER REQUIRED SERVICES:
From `requirements`, `product_verticals` and `scope_of_work`, infer which services from `service_catalogue` this request REQUIRES. Be evidence-based:
- A stated storage temperature / cold-chain / °C range → "Temperature Capability" required.
- Cross-border, imports/exports, or a foreign country context → "Customs Clearance" and often "Bonded Warehouse".
- Pharma / medical / regulated product verticals → "GDP Certification", "ISO Certification", "Regulatory Support".
- Distribution / delivery / high outbound volume in scope → "Last-Mile Distribution".
- Explicit systems/integration language (SAP, Salesforce, EDI) → "SFDC Integration" / "EDI Capability".
Only mark a service required when the request genuinely implies it. If the request is vague and implies no specific service, treat the required set as empty and say so.

STEP 2 — DETERMINE ELIGIBILITY (per vendor):
A vendor is ELIGIBLE to be recommended ONLY if BOTH:
  (a) score.is_blacklisted is NOT true, AND
  (b) it provides EVERY required service from Step 1 (none of the required services appear in its services_missing).
A vendor that is blacklisted, or is missing even one required service, is INELIGIBLE — it can still appear in the ranking (so the buyer sees it), but it can NEVER be the recommended_vendor.
If score.overall_rating < 2 (and a score exists), the vendor stays eligible but is low-confidence — note it.

STEP 3 — CHECK ALL TERMS:
- CURRENCY: bids may be in different currencies. NEVER treat raw totals in different currencies as directly comparable. If eligible vendors quote different currencies, say so explicitly in the overall reasoning and rank cautiously; prefer same-currency comparison unless one is overwhelmingly cheaper.
- If best_bid_is_staff_selected is true for a vendor, that bid was hand-picked by staff as the one to use — respect it as that vendor's price.
- Factor compliance_rating and delivery_reliability as tie-breakers between eligible vendors of similar price.

STEP 4 — PICK THE WINNER:
Among ELIGIBLE vendors only, recommend the one with the LOWEST best_bid_total (same-currency). Break near-ties (within ~5%) using overall_rating, then compliance_rating.
If NO vendor is eligible (every vendor is blacklisted or missing a required service), set recommended_vendor to the eligible-closest vendor that covers the MOST required services at the best price, and make it unmistakable in the reasoning that no vendor covers 100% of required services.

OUTPUT RULES:
- `required_services`: the exact list you inferred in Step 1 (may be empty).
- `ranked_vendors`: EVERY vendor, ordered best (index 0) to worst. Eligible vendors rank above ineligible ones; among eligible, cheapest first.
- Each vendor's `eligible` boolean and `missing_services` list must be filled honestly.
- Each vendor's `reasoning` (1-2 sentences) MUST state whether it can deliver all required services and its price standing — e.g. "Delivers all 4 required services; lowest eligible price at INR 64,159." or "Cheapest bid but INELIGIBLE — missing Bonded Warehouse & Customs Clearance."
- `recommended_vendor` MUST equal ranked_vendors[0].name UNLESS index 0 is ineligible, in which case it equals the first eligible vendor's name.
- `reasoning` (overall, 2-4 sentences): name the required services you inferred, the winner and why, and any currency/blacklist/eligibility caveats.

POINTS (why THIS vendor for THIS request):
- `decision_points`: 3 to 6 short, concrete bullet points justifying the recommended vendor AGAINST THIS SPECIFIC REQUEST. Each point must be one line, factual, and cite the number or service that drives it — e.g. "Lowest eligible quote: INR 64,159 — 12% below the next eligible vendor", "Covers all 4 required services including Temperature Capability (2–8 °C storage requested)", "Compliance rating 5/5 — highest among eligible vendors". Do NOT write generic filler like "good vendor" or "competitive pricing".
- Each ranked vendor's `points`: 2 to 4 one-line bullets summarising that vendor's specific strengths and disqualifiers for this request — e.g. "Missing Bonded Warehouse — required for cross-border scope", "Storage cost INR 40,000 is the cheapest single line item".
- Points are read as a bullet list in the UI. No numbering, no markdown, no trailing periods required.

Return ONLY the structured JSON. No markdown, no preamble."""

SYSTEM_PROMPT += PLAIN_ENGLISH


OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["recommended_vendor", "reasoning", "required_services",
                 "decision_points", "ranked_vendors"],
    "additionalProperties": False,
    "properties": {
        "recommended_vendor": {
            "type": "string",
            "description": "Name of the recommended vendor: the cheapest ELIGIBLE (non-blacklisted, provides all required services) vendor.",
        },
        "reasoning": {
            "type": "string",
            "description": "2-4 sentence overall explanation: inferred required services, the winner and why, plus currency/blacklist/eligibility caveats.",
        },
        "required_services": {
            "type": "array",
            "description": "Services inferred as REQUIRED by this request (subset of service_catalogue). May be empty.",
            "items": {"type": "string"},
        },
        "decision_points": {
            "type": "array",
            "description": "3-6 short one-line bullet points justifying the recommended vendor for THIS request, each citing a concrete number or service.",
            "items": {"type": "string"},
        },
        "ranked_vendors": {
            "type": "array",
            "description": "Every vendor ranked best (index 0) to worst. Eligible above ineligible; among eligible cheapest first.",
            "items": {
                "type": "object",
                "required": ["name", "total_bid", "currency", "eligible",
                             "missing_services", "reasoning", "points"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "total_bid": {"type": "number"},
                    "currency": {"type": "string"},
                    "eligible": {
                        "type": "boolean",
                        "description": "True only if NOT blacklisted AND provides every required service.",
                    },
                    "missing_services": {
                        "type": "array",
                        "description": "Required services this vendor cannot deliver (empty if it covers all).",
                        "items": {"type": "string"},
                    },
                    "reasoning": {"type": "string"},
                    "points": {
                        "type": "array",
                        "description": "2-4 one-line bullets on this vendor's specific strengths / disqualifiers for this request.",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


# ── Payload builder (shared with the JSON API view) ─────────────────────────
def _bid_total(bid):
    fields = ["storage_cost", "inbound_cost", "outbound_cost", "pick_cost",
              "packing_cost", "last_mile_charges", "manpower_charges"]
    return float(sum(float(getattr(bid, f) or 0) for f in fields))


def build_payload(vr):
    """Assemble the request + per-vendor bid/score/capability data for the model."""
    from .models import VendorBid

    # Read the capability master once. It is the payload's `service_catalogue`
    # AND the per-vendor services loop below, and those two must be the same
    # list or the model is told a vendor lacks a service that was never asked
    # about.
    catalogue = capability_rows()

    all_bids = list(
        VendorBid.objects.filter(vendor_request=vr)
        .select_related("submitted_by", "vendor_registration__vendor")
        # Capabilities are a join now, so without this the services loop is one
        # query per bidding vendor.
        .prefetch_related("vendor_registration__vendor__capabilities")
    )

    groups = defaultdict(list)
    for b in all_bids:
        groups[bidcompare.vendor_key_for(b)].append(b)

    vendors_data = []
    for _uid, bids in groups.items():
        selected = next((b for b in bids if b.is_selected), None)
        best = selected or min(bids, key=_bid_total)
        reg = best.vendor_registration

        score_data = {}
        if reg and reg.vendor_id:
            try:
                s = reg.vendor.score
                score_data = {
                    "facility_rating": s.facility_rating,
                    "support_rating": s.support_rating,
                    "compliance_rating": s.compliance_rating,
                    "delivery_reliability": s.delivery_reliability,
                    "overall_rating": s.overall_rating,
                    "is_blacklisted": s.is_blacklisted,
                    "blacklist_reason": s.blacklist_reason,
                    "notes": s.notes,
                }
            except Exception:
                pass

        caps, services = {}, {}
        services_provided, services_missing = [], []
        if reg and reg.vendor_id:
            v = reg.vendor
            for field, label, _icon in catalogue:
                # has_capability, not getattr: the nine booleans are gone, so
                # getattr would answer False for every vendor and the model
                # would be told nobody can do anything.
                val = v.has_capability(field)
                caps[field] = val
                services[label] = val
                (services_provided if val else services_missing).append(label)

        vendors_data.append({
            "vendor_name": reg.vendor_name if reg else (
                best.submitted_by.get_full_name() or best.submitted_by.username
            ),
            "registration_id": reg.pk if reg else None,
            "capabilities": caps,
            "services": services,
            "services_provided": services_provided,
            "services_missing": services_missing,
            "services_provided_count": len(services_provided),
            "services_total": len(catalogue),
            "score": score_data,
            "bids": [
                {
                    "bid_number": b.bid_number,
                    "currency": b.currency,
                    "storage_cost": float(b.storage_cost or 0),
                    "inbound_cost": float(b.inbound_cost or 0),
                    "outbound_cost": float(b.outbound_cost or 0),
                    "pick_cost": float(b.pick_cost or 0),
                    "packing_cost": float(b.packing_cost or 0),
                    "last_mile_charges": float(b.last_mile_charges or 0),
                    "manpower_charges": float(b.manpower_charges or 0),
                    "total_cost": _bid_total(b),
                    "notes": b.notes,
                    "submitted_at": b.submitted_at.isoformat(),
                    "staff_selected": b.is_selected,
                }
                for b in sorted(bids, key=_bid_total)
            ],
            "best_bid_total": _bid_total(best),
            "best_bid_currency": best.currency,
            "best_bid_is_staff_selected": bool(selected),
        })

    vendors_data.sort(key=lambda v: v["best_bid_total"])

    return {
        "request_id": vr.pk,
        # The reference is how staff actually name a request -- they ask Meril
        # GPT to "analyse RFI-2026-000004". Without it in the payload the model
        # had no way to resolve that string to a request and could only guess,
        # so a question phrased by reference number was unanswerable.
        "reference": vr.reference_id or "",
        "subject": vr.subject,
        "country": vr.country,
        "requirements": {
            "warehouse_space": vr.warehouse_space_range,
            "storage_temp": vr.storage_temp_range,
            "monthly_shipment": vr.monthly_shipment_range,
            "inbound": vr.inbound_range,
            "outbound": vr.outbound_range,
            "product_verticals": vr.product_verticals,
            "scope_of_work": vr.scope_of_work,
        },
        "service_catalogue": [label for _f, label, _i in catalogue],
        "bid_window_status": vr.bid_window_status,
        "vendors": vendors_data,
    }


# ── Evaluation ──────────────────────────────────────────────────────────────
def evaluate(vr):
    """Call OpenAI and return the parsed result dict. Raises on failure."""
    client = _get_client()
    if client is None:
        raise RuntimeError(
            "AI client is not configured — set TEST_OPENAI_API_KEY and install the openai package."
        )

    payload = build_payload(vr)
    if not payload["vendors"]:
        raise RuntimeError("No bids have been submitted for this request yet.")

    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Evaluate this Meril 3PL procurement request and its vendor bids. "
                    "Apply the decision rule and return the structured ranking with "
                    "concrete points.\n\n"
                    + json.dumps(payload, indent=2, default=str)
                ),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "vendor_bid_evaluation",
                "strict": True,
                "schema": OUTPUT_SCHEMA,
            },
        },
    )

    content = response.choices[0].message.content
    result = json.loads(content)
    return result


def apply_evaluation_result(evaluation, result):
    """Write a completed evaluation result onto its VendorEvaluation row.

    The one place that decides what a "result" looks like on the model, so a
    field renamed or added here is persisted consistently.
    """
    from django.utils import timezone

    from .models import VendorEvaluation

    evaluation.status = VendorEvaluation.STATUS_COMPLETED
    evaluation.recommended_vendor_name = (result.get("recommended_vendor") or "")[:200]
    evaluation.reasoning = result.get("reasoning") or ""
    evaluation.ranked_vendors_json = json.dumps(result.get("ranked_vendors") or [])
    evaluation.required_services_json = json.dumps(result.get("required_services") or [])
    evaluation.decision_points_json = json.dumps(result.get("decision_points") or [])
    evaluation.model_used = ""
    evaluation.error_message = ""
    evaluation.completed_at = timezone.now()
    evaluation.save()


def mark_evaluation_failed(evaluation, message):
    from django.utils import timezone

    from .models import VendorEvaluation

    evaluation.status = VendorEvaluation.STATUS_FAILED
    evaluation.error_message = str(message)[:2000]
    evaluation.completed_at = timezone.now()
    evaluation.save()


def run_evaluation(vr_pk):
    """Evaluate a request in-process via OpenAI and persist the outcome."""
    from .models import VendorEvaluation, VendorRequest

    try:
        evaluation = VendorEvaluation.objects.get(vendor_request_id=vr_pk)
    except VendorEvaluation.DoesNotExist:
        logger.error("VendorEvaluation for request %s vanished before evaluation", vr_pk)
        return

    try:
        vr = VendorRequest.objects.get(pk=vr_pk)
        result = evaluate(vr)
    except Exception as exc:
        logger.exception("Bid evaluation failed for request %s", vr_pk)
        mark_evaluation_failed(evaluation, exc)
        return

    apply_evaluation_result(evaluation, result)


def run_evaluation_async(vr_pk):
    """Kick off run_evaluation in a daemon thread so the request returns at once."""
    def _worker():
        try:
            run_evaluation(vr_pk)
        finally:
            from django.db import connection
            connection.close()

    threading.Thread(target=_worker, daemon=True).start()


# ── Meril GPT — free-text Q&A across every request ──────────────────────────
MERIL_GPT_SYSTEM_PROMPT = """You are Meril GPT, the assistant embedded in Meril Life Sciences' VendorLink platform. Global SCM staff talk to you here. Most of the time they ask about 3PL procurement requests and vendor bids — but not always, and answering the question they actually asked matters more than staying on that topic.

THREE SOURCES, AND KNOWING WHICH ONE YOU ARE IN
Every turn names its source at the top of the user message as `Answer source:`. Honour it.

1. `portal` — the question is about VendorLink's own data: requests, bids, vendors, awards, prices, who to pick. Answer using ONLY the JSON payload supplied in this conversation. Never invent a vendor, a price or a request that is not in it. If the payload does not contain the answer, say so plainly.

2. `document` — the question is about a file the user attached. The document is the subject and the authority. Answer about what is in it, in its own terms, whatever it happens to be about. Do NOT reach for the VendorLink payload, do not frame the answer around vendor selection, and do not manufacture a connection to procurement that the user did not ask for. A document about laboratory protocols, a policy, a contract, a manual or a research paper is simply answered as that. If the payload was withheld from this turn, it is because the question did not need it — that is not a gap you should apologise for or work around.

3. `general` — the question is about neither: how something works, what a term means, help writing or understanding something, a calculation, a piece of domain background. Answer it directly and usefully from your own knowledge, the way a knowledgeable colleague would. Say when you are answering from general knowledge rather than from anything in the portal. Never bend it into a VendorLink answer.

4. `both` — the user is genuinely relating a document to the portal's data, for example "how does this quotation compare with the bids we already have". Use both, and be explicit about which figure came from where.

THE RULE THAT MATTERS MOST
Do not force a relationship between an attached file and VendorLink's data. Someone uploading a document is not implicitly asking how it affects vendor selection. Answer the document. If you think there is a genuine, useful connection, you may add one short line at the end offering it — never as the body of the answer.

WHEN YOU ARE ANSWERING FROM PORTAL DATA
Data shape: a list of requests, each with a `reference` (the RFI number staff quote, e.g. RFI-2026-000004 -- match on this when a question names one), `requirements` (space/temp/volumes/scope), `bid_window_status`, `service_catalogue`, and `vendors` — each vendor has `services`/`services_missing`, `score` (ratings 1-5, `is_blacklisted`, `notes`), and `bids` with `total_cost`/`currency`/`staff_selected`.

You can be asked to:
- Analyse quotes for a specific request or vendor.
- Suggest the best vendor — apply the same rule the structured evaluator uses: the cheapest vendor that ALSO delivers every service a request needs, on ALL terms. A cheaper vendor missing a required service or carrying `is_blacklisted: true` is never "the best" — say so explicitly and name who actually qualifies.
- Summarise bid trends across all requests — e.g. price spread per country, which vendors appear most/win most, currency mix, requests with no bids yet.

- Always name currencies — never compare totals in different currencies as if equal.
- If a request has zero bids, say so plainly rather than guessing.
- Cite concrete numbers and vendor names from the data, not vague claims.

IN EVERY MODE
- Be concise: short paragraphs and bullet points, no filler like "great question" or "as an AI".
- `answer` is plain text — no markdown headers or tables; bullets as "- " lines are fine.
  Structure belongs in `report`, not in the prose.
"""


# ── Meril GPT structured answer ─────────────────────────────────────────────
# The chat used to return prose only, so a question like "prepare a commercial
# report" came back as a wall of text the reader had to re-tabulate by hand.
# The answer is now a strict JSON object: `answer` is always the prose reply,
# and `report` is filled in only when the question actually calls for one, so
# an ordinary question is not padded out with an empty chart.
#
# Strict structured outputs (the same mechanism evaluate() uses) guarantee the
# shape, which is what lets the panel render KPI tiles, a chart and a table
# without defensive parsing on every field.
GPT_ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "report"],
    "properties": {
        "answer": {
            "type": "string",
            "description": "The conversational reply. Always present, even when a report is attached.",
        },
        "report": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["title", "summary", "kpis", "chart", "table", "highlights"],
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string", "description": "2-4 sentences a manager could read alone."},
                "kpis": {
                    "type": "array",
                    "description": ("Up to 4 headline figures. Empty when none are meaningful. Anything that does not fit belongs in `table`, never dropped."),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["label", "value", "note"],
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string", "description": "Include the currency or unit."},
                            "note": {"type": ["string", "null"]},
                        },
                    },
                },
                "chart": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["type", "unit", "labels", "series"],
                    "properties": {
                        "type": {"type": "string", "enum": ["bar", "line", "doughnut"]},
                        "unit": {"type": "string", "description": "Currency or unit for the axis. Never mix currencies in one chart."},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "series": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name", "data"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "data": {"type": "array", "items": {"type": "number"}},
                                },
                            },
                        },
                    },
                },
                "table": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["columns", "rows"],
                    "properties": {
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "rows": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "highlights": {
                    "type": "array",
                    "description": "Short bullets: risks, caveats, what to do next.",
                    "items": {"type": "string"},
                },
            },
        },
    },
}

REPORT_GUIDANCE = """

WHEN TO ATTACH A REPORT
Set `report` to a full object — never null — when the user asks for a report,
a summary, a comparison, an analysis, trends, or "which vendor should we
pick", and when an attached file carries figures the reader would want laid
out as tiles, a chart or a table. Otherwise set `report` to null and answer in
`answer` alone; a one-line factual question does not need a chart.

ALWAYS attach a report — whether or not the user asked for one — when an
attachment is a priced document: a quotation, rate card, price list, tariff or
cost estimate. Someone who uploads a quotation and types nothing still needs
its numbers laid out; a prose paragraph about a six-line rate card is the
answer they have to re-type into a spreadsheet themselves.

A report is about the numbers that exist, never numbers you produced to fill
the shape. An attached document with no figures in it — a policy, a manual, a
contract, a protocol — gets `report: null` and a written answer. Inventing
KPIs for it, or borrowing them from the VendorLink payload to decorate an
answer about something else, is worse than having no report at all.

ASKED FOR A TABLE OR A CHART
If the user asks for a table, a comparison "in tabular format", a breakdown by
row, or anything they plan to read as a grid, you MUST populate `report.table`
with it. If they ask for a chart, a graph, a plot, a trend line or a
visualisation, you MUST populate `report.chart`. Both are rendered in the chat
for the user; a table described in prose inside `answer` is not a table, and
it is the single most common thing this assistant gets wrong.

`answer` in that case should be one or two sentences introducing the table or
chart, not a repetition of it. Do not spell the rows out in prose as well —
the user is looking at them.

The request stands for a follow-up too. "Now show that as a table" refers to
whatever was just discussed in this conversation: build the table from that,
and do not answer as though it were a fresh question.

A REPORT BUILT FROM AN ATTACHED DOCUMENT
The rule above — populate `table` when the user asks for one — is not enough
when the source is a document. Nobody uploading a quotation asks for "a table";
they ask "what does this say", and a curated four-tile summary of a priced
document is a costing error waiting to happen.

So: when the attachment is a quotation, rate card, price list, tariff, estimate
or any other pricing sheet, `report.table` MUST carry EVERY priced line item in
it, not a selection. One row per item, with its own rate, unit and basis as the
document states them. A document with six priced items produces six rows.

This is the failure it exists to stop. A 3PL estimate carrying fixed rental,
fixed management, variable storage, value-added services and two delivery rates
(national and island) came back as four tiles and nothing else: the island rate
and the VAS rate were dropped without a word, and so were the remarks that the
price excluded 10% VAT, held for one year, and rose annually with minimum wage.
Every figure reported was correct. The report was still wrong, because a reader
adding the numbers up got a total the document does not support.

Two habits follow from that:
- Report the units and the basis, not just the number. "1,700 KRW per pallet
  per day" and "1,700 KRW" are different claims; only one of them can be
  costed.
- If the document is too long to carry every line item, say which sections you
  covered rather than presenting a partial extraction as a summary.

BUILDING THE REPORT
- `kpis`: at most 4, each a figure a manager would quote. Put the currency in
  the value ("EUR 28,500"), never a bare number.
  If the source holds more priced line items than fit here, the surplus goes
  into `table` — and preferably every line item does. Four is a limit on what
  is promoted to a tile, never a licence to leave a priced item out of the
  report altogether. Silently dropping one is the worst failure this report can
  have: the reader cannot tell a figure you left out from a figure the source
  never had, so the total they compute is wrong and looks complete.
- `chart`: only when the numbers are genuinely comparable. Charting totals
  quoted in different currencies is misleading, so pick one currency, say so
  in `unit`, and leave the others out of the chart (mention them in
  `highlights`). Use "bar" to compare vendors, "line" for something over time,
  "doughnut" for shares of a whole. Null when nothing is worth plotting.
- `table`: the supporting rows. Every cell a string, already formatted —
  include the currency in the cell ("EUR 28,500"), because a column of bare
  numbers in two currencies is worse than no table. Keep the column count to
  what fits a chat panel; six is comfortable, ten is not.
- `highlights`: the caveats — missing services, blacklisted vendors, mixed
  currencies, bids with no costs. This is where you say what the numbers do
  not.
  When the report is built from an attached document, this is also where its
  own remarks go — every note that changes what the quoted price actually
  costs. VAT treatment ("exclusive of VAT 10%"), the validity period, an
  annual escalation clause, payment terms, overtime or holiday loadings, the
  named site the quote assumes, and anything billed separately to the client.
  A rate quoted without them reads as the final price and is not.
- `summary`: readable on its own, without the chart.
"""

# Appended rather than inlined above: the report rules are about the shape of
# the reply, and keeping them next to GPT_ANSWER_SCHEMA means the schema and
# the instructions describing it cannot drift apart.
MERIL_GPT_SYSTEM_PROMPT += REPORT_GUIDANCE
MERIL_GPT_SYSTEM_PROMPT += PLAIN_ENGLISH


def build_all_requests_payload():
    """Every VendorRequest's bid payload, for cross-request Q&A."""
    from .models import VendorRequest

    return [build_payload(vr) for vr in VendorRequest.objects.order_by("-created_at")]


# ── Which source should answer this? ────────────────────────────────────────
#
# Reported as "I upload a PDF and it answers about VendorLink". Three things
# caused it, and this is the third:
#
#   1. The system prompt ordered the model to answer "using ONLY the JSON data
#      provided" — so an unrelated document had to be bent into a vendor answer
#      to satisfy the instruction.
#   2. An upload with no question was given the default task "summarise what
#      they mean for our vendor selection", which is that complaint written
#      into the code.
#   3. Every single turn carried the full request-and-bid payload, which on a
#      busy instance is tens of thousands of tokens sitting next to a three-page
#      PDF. Attention follows volume; the payload won.
#
# The first two are prompt fixes. This is the third: decide what the question
# is actually about *before* building the prompt, and leave the payload out
# when it has no business being there.

#: Words that can only mean "read our records" — nothing else.
#:
#: This list used to include "cheapest", "vendor", "quote", "supplier",
#: "procurement", "tender" and "3pl", and that is what failed: "what's the
#: cheapest way to get pharma cold-chain from Frankfurt to Jeju?" is a question
#: about the world, and it took this path straight to `portal` without the
#: router ever seeing it. Every one of those words appears in questions our
#: data cannot answer, because our data is *about* logistics — the vocabulary
#: of the domain is not the vocabulary of the database.
#:
#: What is left either names a record type that exists only here (an RFI
#: reference, a bid, an award, a scorecard) or says "ours" out loud. Anything
#: less certain goes to the router and costs one small call, which is the
#: right trade: the saving was never worth answering the wrong question.
PORTAL_VOCABULARY = re.compile(
    r"\b("
    r"rfi-?\d|rfq-?\d|"
    r"bids?\b|bidding|bidder|"
    r"award(ed|s)?\b|shortlist(ed)?|blacklist(ed)?|scorecard|"
    r"request (id|reference|number)|"
    r"our (vendors?|bids?|requests?|rates?|suppliers?|quotes?|awards?)|"
    r"(on|for) rfi|in the portal|on file|registered vendors?"
    r")\b",
    re.IGNORECASE,
)

ANSWER_SOURCES = ("portal", "document", "general", "both")

INTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "reason"],
    "properties": {
        "source": {
            "type": "string",
            "enum": list(ANSWER_SOURCES),
            "description": (
                "portal = about VendorLink's own requests/vendors/bids. "
                "document = about the attached file's own subject matter. "
                "general = general knowledge, explanation or help, about neither. "
                "both = explicitly relating the attached file to VendorLink's data."
            ),
        },
        "reason": {"type": "string", "description": "One short clause. For logs."},
    },
}

INTENT_SYSTEM_PROMPT = """You route a question to the source that should answer it. You do not answer it.

- `portal`: the user is asking about VendorLink's own RECORDS — a named RFI, the vendors on file, the bids we have received, what we awarded, which of OUR vendors to choose. The test is whether answering requires reading our data. "Which vendor bid lowest on RFI-2026-000004", "how many requests are open in Germany", "who won the Poland tender".
- `document`: the user is asking about an attached file, whatever that file is about. This is the right answer even when the file has nothing to do with logistics or procurement — a policy, a manual, a lab protocol, a research paper, a contract, an invoice for something else entirely.
- `general`: the user wants an explanation, a definition, a calculation, background, or how something is usually done — a question about the SUBJECT, not about our records. Logistics questions are still `general` when they are about the world rather than about our data: "what's the cheapest way to move pharma cold-chain from Frankfurt to Jeju", "how does bonded warehousing work", "what does DAP mean", "how should we structure a tender". Sharing vocabulary with procurement does not make a question about our procurement records — "cheapest", "vendor", "rate" and "shipping" all appear in questions that our data cannot answer.
- `both`: the user is deliberately relating an attached file to the portal's data — comparing an attached quotation against bids on file, checking a document against a request's requirements.

Judge by what the user asked for, not by what the file happens to contain. "Summarise this" about an attached document is `document`, not `both`, even if the document mentions logistics. Default to `document` when a file is attached and the question does not clearly point elsewhere.

With nothing attached, ask yourself whether the question can only be answered by reading our records. If yes, `portal`. If a knowledgeable colleague could answer it without opening the system, `general` — that is true even when the topic is warehousing, freight or pricing, which is most of what this platform is about.
"""


def _attachment_digest(attachments, per_file=320):
    """Filenames plus a glimpse of each, so the router can see what a file is.

    A glimpse, not the document: the router only has to tell a rate card from a
    training manual, and sending whole documents twice would double the cost of
    every turn to answer a question the first 300 characters already answer.
    """
    lines = []
    for attachment in attachments or []:
        text = (getattr(attachment, "text", "") or "").strip()
        excerpt = " ".join(text[:per_file].split())
        lines.append(f"- {attachment.name}: {excerpt or '(no extractable text)'}")
    return "\n".join(lines)


#: Currency markers, and the per-unit phrasings a rate card is written in.
#: Deliberately literal: this decides whether to *ask* for a table, and a false
#: positive costs a table nobody needed while a false negative costs a priced
#: line nobody saw.
_MONEY_RE = re.compile(
    r"(?:\b(?:KRW|USD|EUR|GBP|INR|AED|JPY|CNY|SGD|AUD|CAD|CHF|THB|MYR|IDR|VND|PLN|MXN|BRL|ZAR)\b"
    r"|[₩$€£¥₹])\s*[\d,]|\b[\d,]{2,}\s*(?:KRW|USD|EUR|GBP|INR|AED|JPY|CNY|SGD|AUD)\b",
    re.IGNORECASE,
)
#: "per pallet" and "/ pallet" are the same rate written two ways, and a
#: quotation exported from a spreadsheet almost always uses the slash. The
#: first version of this only understood "per", and missed a real rate card.
_UNITS = (r"pallet|box|carton|kg|cbm|sqm|sq\.?\s?m|m2|py|day|month|year|unit|"
          r"shipment|labou?r|hour|hr|trip|container|case|pack")
_RATE_RE = re.compile(
    r"\bper\s+(?:" + _UNITS + r")\b"
    r"|/\s*(?:" + _UNITS + r")\b"
    r"|\brate\s*card\b|\bquotation\b|\bprice\s*list\b|\btariff\b|\bcost\s*estimate\b"
    r"|\bestimation\b|\bunit\s*price\b|\bper[- ]unit\b",
    re.IGNORECASE,
)


def looks_priced(attachments) -> bool:
    """Is this attachment a rate card, quotation, tariff or cost estimate?

    Decided here rather than left to the model, because the model is exactly
    what got this wrong: told in the system prompt to always report on a priced
    document, it still answered a six-line 3PL estimate in prose and returned
    ``report: null``. An instruction in the *user* turn, stated about the file
    actually in front of it, is followed; one buried in a long system prompt
    competes with everything else in there.

    The test is deliberately crude — two or more money amounts, or a per-unit
    rate phrasing in either spelling — because the cost of the two mistakes is not symmetric. A false
    positive is a table nobody asked for. A false negative is the reported bug:
    a priced line silently missing from a costing decision.
    """
    for attachment in attachments or []:
        text = getattr(attachment, "text", "") or ""
        if not text:
            continue
        if len(_MONEY_RE.findall(text)) >= 2 or _RATE_RE.search(text):
            return True
    return False


def resolve_answer_source(question, attachments=None, history=None, client=None,
                          thread_documents=False, previous_source=""):
    """Decide which source answers this turn: portal / document / general / both.

    Returns ``(source, reason)``. Never raises: if the router is unavailable or
    errors, it falls back to the safe reading of the situation rather than
    taking the whole chat down with it.

    ``thread_documents`` says whether files were attached *earlier in this
    thread*, which is different from a file on this turn and matters as much.
    See the fast-path comment below for what happens when it is ignored.
    """
    question = (question or "").strip()
    attachments = list(attachments or [])

    # Fast path: no file anywhere in the thread, and the question uses words
    # that only mean the portal's own data. The overwhelmingly common case,
    # with nothing to disambiguate, so it costs no second API call.
    #
    # `thread_documents` is in this test because leaving it out produced a
    # clean failure: with a Korean 3PL quotation uploaded earlier in the
    # thread, "what's the cheapest way to get pharma cold-chain from Frankfurt
    # to Jeju?" hit the word "cheapest", took this path to `portal` without
    # ever consulting the router, and came back as a costing report built from
    # that quotation's figures. A general question is not a portal question
    # because it happens to contain one procurement word.
    if (not attachments and not thread_documents and not previous_source
            and PORTAL_VOCABULARY.search(question)):
        return "portal", "portal vocabulary, fresh thread"

    if attachments:
        fallback = ("document", "a file is attached")
    elif thread_documents:
        fallback = ("document", "documents earlier in this thread")
    else:
        fallback = ("general", "nothing attached")

    client = client or _get_client()
    if client is None:
        return fallback

    parts = [f"Question: {question or '(none given)'}"]
    if attachments:
        parts.append("Attached file(s):\n" + _attachment_digest(attachments))
    elif thread_documents:
        # The distinction the router has to draw here is the one that failed:
        # a question *about* those files is `document`; a question that merely
        # shares vocabulary with them is `general` or `portal`.
        parts.append(
            "No file is attached to this turn, but the user attached one or "
            "more documents earlier in this conversation. A question about "
            "those documents is `document`; a question that only happens to "
            "share their subject matter is `general` or `portal`."
        )
    else:
        parts.append("No files are attached, and none earlier in this conversation.")
    if previous_source:
        # A follow-up that names no subject continues the last one. Without
        # this, "create a report with graphs included" read as a fresh
        # question about nothing and went to the portal, mid-thread, while the
        # subject on screen was an attached quotation.
        parts.append(
            f"The previous turn in this conversation was answered from: {previous_source}. "
            "Use that only to resolve what this question leaves unsaid. "
            "It CONTINUES the previous source when it names no subject of its "
            "own: a pronoun with no antecedent ('summarise this', 'now as a "
            "table', 'add a chart'), a bare instruction ('retry', 'again', "
            "'make it shorter'), or a request to reformat what was just given. "
            "It MOVES when it names a subject of its own, even mid-thread: "
            "'across every open request, suggest the best vendor' is `portal` "
            "however the thread began; 'how does that compare with what we "
            "already have' is `both`, because it asks for the previous subject "
            "AND our records; a question about a term or a method is `general`. "
            "Ask which of the two this is before answering. A thread does not "
            "own the questions asked inside it."
        )
    if history:
        recent = [
            f"{turn.get('role')}: {(turn.get('content') or '')[:200]}"
            for turn in list(history)[-2:]
            if turn.get("content")
        ]
        if recent:
            # "and now compare it to our bids" only means anything next to the
            # turn before it.
            parts.append("The two turns before this one:\n" + "\n".join(recent))

    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "answer_source", "strict": True, "schema": INTENT_SCHEMA},
            },
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        source = parsed.get("source")
        if source in ANSWER_SOURCES:
            return source, (parsed.get("reason") or "").strip()
    except Exception:
        logger.exception("MerilGPT: answer-source routing failed; falling back")

    return fallback


def ask_meril_gpt(question, attachments=None, retrieved=None, history=None,
                  previous_source=""):
    """Answer a free-text question about bids/vendors, grounded in live data.

    ``attachments`` is a list of :class:`vendorlink.gptfiles.Attachment` — the
    caller has already validated and text-extracted them. Images ride along as
    ``image_url`` content parts for the vision model; documents are inlined as
    text, because Chat Completions does not parse a PDF or a spreadsheet on its
    own.

    With attachments present the request payload becomes *context* rather than
    the subject: someone who attaches a vendor's own quotation PDF is usually
    asking how it compares to what is already on file, so the prompt says which
    is which instead of running them together.

    ``retrieved`` is the passages :mod:`vendorlink.gptrag` selected from
    documents attached earlier in the same thread. Without it a follow-up
    question -- usually the real question -- reached the model with no document
    at all, because only the filename was ever persisted; the model then
    answered from the request payload and sounded certain about it.

    ``history`` is the thread's previous turns. Every question used to be sent
    standalone, so "now show that as a table" had no referent for "that" and
    the model answered something adjacent instead of the thing just discussed.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("AI client is not configured — set TEST_OPENAI_API_KEY and install the openai package.")

    question = (question or "").strip()
    attachments = list(attachments or [])
    if not question and not attachments:
        raise ValueError("Ask a question first.")
    if not question:
        # An attachment with no question is a reasonable thing to do — give the
        # model a default task rather than refusing the upload.
        #
        # Neutral on purpose. This used to read "summarise what they mean for
        # our vendor selection", which is how a PDF about anything else came
        # back as a procurement analysis: the complaint was written into the
        # default task.
        question = "Summarise the attached file(s) and say what they are about."

    # `retrieved` is non-empty exactly when this thread has documents indexed,
    # which is the signal the router needs and cannot get from `attachments`
    # (those are only this turn's uploads).
    source, source_reason = resolve_answer_source(
        question, attachments=attachments, history=history, client=client,
        thread_documents=bool(retrieved), previous_source=previous_source,
    )
    logger.info("MerilGPT source=%s (%s)", source, source_reason)

    # The payload is fetched only when the answer is going to use it. It is
    # every request and every bid in the system — on a busy instance, tens of
    # thousands of tokens — and sending it next to a three-page PDF is how an
    # answer about the PDF ends up being about vendor selection.
    wants_portal = source in ("portal", "both")
    payload = build_all_requests_payload() if wants_portal else []
    if wants_portal and not payload and not attachments:
        raise RuntimeError("No vendor requests exist yet — raise one first.")

    from .gptfiles import as_prompt_parts

    text_block, image_parts = as_prompt_parts(attachments)

    # The source is the first thing the model reads, because every rule in the
    # system prompt is conditioned on it.
    sections = [f"Answer source: {source}"]
    if source == "document":
        sections.append(
            "The VendorLink request and bid payload has deliberately been left "
            "out of this turn: the question is about the attached file, not "
            "about the portal's data. Answer the file on its own terms. If the "
            "user later asks how it relates to our vendors or bids, that data "
            "will be supplied then."
        )
    elif source == "general":
        sections.append(
            "This question is about neither the portal's data nor any attached "
            "document. Answer it from your own knowledge and say so. Both the "
            "VendorLink payload and any document from earlier in this thread "
            "have deliberately been withheld: do not quote figures from them, "
            "and do not build a report out of them. A rate from a quotation "
            "someone uploaded earlier is not the answer to a general question, "
            "even when the subject matches. If a document in this thread would "
            "genuinely help, offer that in one closing line and let the user "
            "ask."
        )

    if payload:
        label = (
            "For reference, here is every current procurement request and its vendor bids"
            if attachments else
            "Here is every current procurement request and its vendor bids"
        )
        sections.append(f"{label}:\n\n{json.dumps(payload, indent=2, default=str)}")
    if attachments and looks_priced(attachments):
        # Stated here, not only in the system prompt: the system-prompt version
        # of this rule was present and ignored on the 3PL estimate that started
        # this — the model answered in prose and returned report: null.
        sections.append(
            "At least one attachment is a priced document — a quotation, rate "
            "card, price list, tariff or cost estimate. For this turn `report` "
            "MUST be a full object, and `report.table` MUST contain one row for "
            "EVERY priced line item in it, with its rate, its unit and its "
            "basis exactly as the document states them. Do not summarise the "
            "pricing into a few headline figures: a reader adding up a subset "
            "gets a total the document does not support, and nothing on screen "
            "tells them a line is missing. Put the document's own remarks — VAT "
            "treatment, validity period, escalation, payment terms, surcharges, "
            "anything billed separately — into `report.highlights`."
        )

    if text_block:
        sections.append(
            "The user attached the following file(s). They are the subject of "
            "the question unless the source above says otherwise — answer them "
            "in their own terms, whatever they are about, and say so if an "
            "attachment is an excerpt rather than a whole document:\n\n" + text_block
        )
    if image_parts:
        sections.append(
            f"The user also attached {len(image_parts)} image(s), supplied below. "
            "Read them directly."
        )
    # Passages from documents attached earlier in this thread — included only
    # where they belong.
    #
    # This used to be unconditional, which is how a general question about
    # cold-chain shipping came back as a costing report built from a Korean
    # quotation uploaded three turns earlier: the passages were appended to
    # every prompt and labelled "the authoritative source". Withholding the
    # vendor payload from a non-portal turn was only half the job; a document
    # the user is not asking about is no different from a payload they are not
    # asking about.
    if retrieved and source in ("document", "both"):
        sections.append(
            "The following passages come from documents the user attached to "
            "this conversation. They are the authoritative source for anything "
            "the documents cover: quote figures, rates and terms from them in "
            "preference to the request payload above, and name the file a "
            "figure came from. Where a label says the passage is an excerpt, "
            "say so rather than implying you saw the whole document. If the "
            "passages do not answer the question, say that plainly instead of "
            "filling the gap from anywhere else:\n\n" + retrieved
        )

    sections.append(f"Question: {question}")
    prompt = "\n\n".join(sections)

    # A plain string when there is nothing but text, the multipart list only
    # when an image is present — the string form is what every existing caller
    # already produced, and keeping it avoids changing behaviour for them.
    content = [{"type": "text", "text": prompt}] + image_parts if image_parts else prompt

    # Prior turns sit between the system prompt and this question, so a
    # follow-up such as "now show that as a table" resolves against what was
    # actually said. Only the text is replayed -- re-sending earlier images
    # would multiply the cost of every turn for no gain, and the documents
    # come back through `retrieved` instead.
    messages = [{"role": "system", "content": MERIL_GPT_SYSTEM_PROMPT}]
    for turn in history or []:
        role = turn.get("role")
        body = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and body:
            messages.append({"role": role, "content": body})
    messages.append({"role": "user", "content": content})

    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        temperature=0.2,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "meril_gpt_answer", "strict": True, "schema": GPT_ANSWER_SCHEMA},
        },
    )

    raw = response.choices[0].message.content or ""
    try:
        parsed = json.loads(raw)
    except ValueError:
        # Strict structured outputs should make this unreachable, but a model
        # swap or an API-side change would otherwise turn the whole panel into
        # an error. Falling back to the raw text keeps the answer readable and
        # only loses the visuals.
        logger.warning("Meril GPT returned non-JSON; falling back to raw text")
        return {"answer": raw.strip(), "report": None, "source": source}

    # `source` travels back so the chat can show which way the question was
    # routed. A reader who can see "answered from: attached document" can tell
    # a wrong answer from a wrongly-routed one, which is the difference between
    # a bug report we can act on and "the AI is broken".
    return {
        "answer": (parsed.get("answer") or "").strip(),
        "report": _clean_report(parsed.get("report")),
        "source": source,
    }


def _clean_report(report):
    """Drop a report that carries nothing worth drawing.

    The model is told to return null when a report is not warranted, but it can
    still hand back an object whose every section is empty. Rendering that
    produces a titled panel with nothing in it, which reads as a bug — so a
    report has to have at least one section with content to survive.
    """
    if not isinstance(report, dict):
        return None

    chart = report.get("chart")
    if isinstance(chart, dict):
        has_points = any(
            (s or {}).get("data") for s in (chart.get("series") or []) if isinstance(s, dict)
        )
        if not (chart.get("labels") and has_points):
            report["chart"] = None

    table = report.get("table")
    if isinstance(table, dict) and not (table.get("columns") and table.get("rows")):
        report["table"] = None

    has_content = any([
        (report.get("summary") or "").strip(),
        report.get("kpis"),
        report.get("chart"),
        report.get("table"),
        report.get("highlights"),
    ])
    return report if has_content else None
