"""
billing_dashboard/services/enrichment.py

Business-enrichment layer: turns a normalized SAP row into reporting
dimensions the dashboards aggregate on.

Derived fields
--------------
* country / country_code / region          (via CountryRegionMap master + fallback)
* is_subsidiary / subsidiary_name          (master, or related-party GL fallback)
* lob                                       (via LobMap master + fallback)
* fin_year / fin_month_seq / fin_month_label / calendar_month  (Apr–Mar FY)
* value_lakhs                              (invoice value in LC ÷ 100,000)
* is_reversal / is_pending / is_excluded   (document-type business rules)
* mapping_status                           (flags rows needing manual master upkeep)

The masters are loaded once per :class:`Enricher` instance so a whole dump can
be enriched without repeated queries.
"""
import re
from decimal import Decimal

from django.conf import settings

from ..models import CountryRegionMap, ExportBillingDetails, LobMap, SoldToPartyMap

# SAP Indian GST-state regions look like "24-Gujarat", "27-Maharashtra".
_INDIAN_STATE_RE = re.compile(r"^\s*\d{1,2}\s*[-–]")
from .normalize import normalize_header

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Excluded from every dashboard aggregate (Billing_Type = F5). Kept separate
# from the domestic/neighbour exclusion so each reason stays auditable.
EXCLUDED_BILLING_TYPES = {"F5"}

# The Endo EXPORT tracker drops domestic India + SAARC neighbours. Primary rule
# is the SAP country code; kept as a settings constant so the set can be tuned
# (e.g. whether Bangladesh counts as domestic) without a code change.
DOMESTIC_CODES = set(getattr(
    settings, "BILLING_DOMESTIC_CODES",
    {"IN", "NP", "BD", "LK", "BT", "MV"},
))

# Fallback only when the SAP country code is blank — catches domestic rows that
# ship without a country code. Extends the (incomplete) .NET city list with the
# big domestic cities it misses (Vapi/Vasai/Valsad/Thane).
DOMESTIC_CITY_SUBSTRINGS = (
    "kathmandu", "dhaka", "srilanka", "sri lanka", "bhutan", "jhapa",
    "maldives", "bangladesh", "india", "nepal", "mumbai",
    "vapi", "vasai", "valsad", "thane",
)


def normalize_party_name(name):
    """Match key for Soldto-Name lookups: lowercase, collapse internal
    whitespace, strip surrounding whitespace/punctuation. Deliberately NOT
    ``normalize_header`` (which deletes all spaces/punctuation and would collide
    distinct customer names)."""
    if not name:
        return ""
    text = re.sub(r"\s+", " ", str(name).strip().lower())
    return text.strip(" .,-/")


# Resilience fallback if the Country master has not been seeded yet — the
# subsidiary examples called out in the requirement meeting.
_FALLBACK_SUBSIDIARIES = {
    "BD": ("Bangladesh", "South East Asia"),
    "BR": ("Brazil", "Latin America"),
    "CN": ("China", "Asia"),
    "DE": ("Germany", "West Europe"),
    "HU": ("Hungary", "East Europe"),
    "IT": ("Italy", "West Europe"),
    "PL": ("Poland", "East Europe"),
    "RU": ("Russia", "CIS"),
}


def financial_period(billing_date):
    """Return (fin_year, fin_month_seq, fin_month_label, calendar_month) for an
    April–March financial year. e.g. 2026-04-14 → ("2026-27", 1, "Apr", 4)."""
    if not billing_date:
        return None, 0, None, 0
    y, m = billing_date.year, billing_date.month
    if m >= 4:
        fin_year = f"{y}-{(y + 1) % 100:02d}"
        seq = m - 3
    else:
        fin_year = f"{y - 1}-{y % 100:02d}"
        seq = m + 9
    return fin_year, seq, MONTHS[m], m


class Enricher:
    def __init__(self, load=True):
        self._countries = {}   # lookup key → (country_name, region, is_subsidiary, subsidiary_name)
        self._lob_exact = {}   # (key_type, key_value_lower) → lob
        self._lob_prefix = []  # list of (prefix_lower, lob) for MATERIAL_PREFIX
        self._lob_code = {}    # material_code_lower → lob (exact — the tracker's primary LOB rule)
        self._party = {}       # soldto_name_key → (country, region, is_subsidiary, is_domestic)
        self._division = {}    # normalized country name → push division
        if load:  # skip for offline/no-DB use (e.g. verify_tracker) where maps are injected
            self._load()

    def _load(self):
        for c in CountryRegionMap.objects.filter(is_active=True):
            payload = (c.country_name, c.region, c.is_subsidiary, c.subsidiary_name or c.country_name)
            if c.country_code:
                self._countries[c.country_code.strip().upper()] = payload
            self._countries[normalize_header(c.country_name)] = payload
            if c.push_division:
                self._division[normalize_header(c.country_name)] = c.push_division
        for lob in LobMap.objects.filter(is_active=True):
            kv = (lob.key_value or "").strip().lower()
            if lob.key_type == LobMap.KeyType.MATERIAL_CODE:
                self._lob_code[kv] = lob.lob
            elif lob.key_type == LobMap.KeyType.MATERIAL_PREFIX:
                self._lob_prefix.append((kv, lob.lob))
            else:
                self._lob_exact[(lob.key_type, kv)] = lob.lob
        # Longest prefix first so "MECR" beats "MC".
        self._lob_prefix.sort(key=lambda t: len(t[0]), reverse=True)
        for p in SoldToPartyMap.objects.filter(is_active=True):
            self._party[p.soldto_name_key] = (p.country, p.region, p.is_subsidiary, p.is_domestic)

    # ── country / region / subsidiary ────────────────────────────────────────
    def _resolve_country(self, row):
        """Return (country_name, code, region, is_sub, sub_name, mapped, is_domestic).

        ``region``/``is_sub``/``sub_name`` here are only fallbacks — ``enrich``
        overrides Region with Sales_Office_Description and the subsidiary flag
        with the ``%meril%`` rule (the agreed .NET-literal behaviour). What this
        resolver authoritatively supplies is the customer **country** and the
        **is_domestic** flag used to keep only true exports."""
        code = (row.get("payer_country") or row.get("sold_to_country") or
                row.get("ship_to_country") or "").strip().upper()
        name_hint = (row.get("payer_name") or row.get("sold_to_name") or "")
        # Domestic classification keys off the DESTINATION (ship-to → sold-to),
        # NOT the payer: an export shipped to e.g. Meril Vietnam but billed to
        # the India HQ has payer_country=IN yet is a genuine export.
        dest = (row.get("ship_to_country") or row.get("sold_to_country") or "").strip().upper()
        code_domestic = dest in DOMESTIC_CODES

        # (0) Soldto-Name lookup (tracker's Lookup sheet) — authoritative for
        #     country + domestic classification. Falls back to Payer Name on
        #     dumps that no longer carry a Soldto-Name column (see enrich()).
        party = self._party.get(normalize_party_name(
            row.get("sold_to_name") or row.get("payer_name")))
        if party:
            p_country, p_region, p_is_sub, p_is_dom = party
            return (p_country or (code or None), code, p_region, p_is_sub,
                    (p_country if p_is_sub else None), True, bool(p_is_dom) or code_domestic)

        found = None
        if code:
            found = self._countries.get(code)
        if not found and code:
            found = self._countries.get(normalize_header(code))
        if found:
            country_name, region, is_sub, sub_name = found
            return country_name, code, region, is_sub, (sub_name if is_sub else None), True, code_domestic

        # India-domestic detection uses ONLY the customer's own region fields
        # (payer/sold-to). It must NEVER use plant_region — that is Meril's
        # manufacturing plant location (always an Indian GST-state code, e.g.
        # "24-Gujarat"), which says nothing about where the customer is. Using
        # it here previously mislabeled dozens of real export countries
        # (Philippines, Vietnam, Greece, Sri Lanka, …) as "India".
        customer_region = (row.get("payer_region") or row.get("sold_to_region") or "").strip()
        export_type = (row.get("export_type") or "").strip()
        if dest == "IN" or (not dest and not export_type and _INDIAN_STATE_RE.match(customer_region)):
            return "India", "IN", "India (Domestic)", False, None, True, True

        # Fallback path — country not in master.
        fb = _FALLBACK_SUBSIDIARIES.get(code) if code else None
        # Related-party / affiliate GL is a strong subsidiary signal.
        gl = (row.get("revenue_gl_desc") or "").lower()
        spec = (row.get("speciality") or "").lower()
        related_party = ("related party" in gl) or ("affiliat" in spec)

        if fb:
            country_name, region = fb
            return country_name, code, region, True, country_name, False, code_domestic

        info = ISO2_INFO.get(code) if code else None
        if info:
            country_name, region = info
            is_sub = related_party
            sub_name = (name_hint or country_name) if related_party else None
            return country_name, code, region, is_sub, sub_name, False, code_domestic

        # Genuinely unknown code — keep the customer's own region text if any.
        is_sub = related_party
        sub_name = (name_hint or None) if related_party else None
        return (code or None), (code or None), (customer_region or "Unmapped"), is_sub, sub_name, False, code_domestic

    # ── LOB ──────────────────────────────────────────────────────────────────
    def _resolve_lob(self, row):
        # Exact material-code match is the tracker's primary LOB rule (Lookup
        # "Material Code → LOB" block, ~6.3k SKUs).
        mat_code = (row.get("material") or "").strip().lower()
        if mat_code:
            lob = self._lob_code.get(mat_code)
            if lob:
                return lob, True
        mg = (row.get("material_group") or "").strip()
        if mg:
            lob = self._lob_exact.get((LobMap.KeyType.MATERIAL_GROUP, mg.lower()))
            if lob:
                return lob, True
        mat = (row.get("material") or "").strip().lower()
        for prefix, lob in self._lob_prefix:
            if prefix and mat.startswith(prefix):
                return lob, True
        ph = (row.get("ph_level01_desc") or "").strip()
        if ph:
            lob = self._lob_exact.get((LobMap.KeyType.PH_LEVEL, ph.lower()))
            if lob:
                return lob, True
        # Fallback: a meaningful product-family label from the product hierarchy.
        # We deliberately do NOT fall back to the raw material-group *code*
        # (e.g. "FG-AS-MXN"), which only pollutes the LOB filter. Rows with no
        # usable label are bucketed as "Unclassified".
        fallback = (row.get("ph_level02_desc") or row.get("ph_level01_desc") or "").strip()
        return (fallback or "Unclassified"), False

    # ── main entry ─────────────────────────────────────────────────────────--
    def enrich(self, row):
        """Mutate & return the parsed row dict with all derived fields added."""
        country_name, code, region, _is_sub, _sub_name, country_mapped, is_domestic = \
            self._resolve_country(row)
        lob, _lob_mapped = self._resolve_lob(row)

        row["country"] = country_name
        row["country_code"] = code

        # Region follows the .NET rule: Region is the SAP Sales_Office_Description
        # (fall back to the resolved region only when it is blank).
        soff = (row.get("sales_office_description") or "").strip()
        row["region"] = soff or region

        # Subsidiary classification is authoritative from the tracker's Lookup
        # sheet (SoldToPartyMap): whenever the sold-to name is mapped there, the
        # party's own Subsidiary flag wins. This is how the manual tracker treats
        # entities like "GARRIC MEDICA SL" / "Amevisa SA" / "Doc Med Comercio" —
        # Meril subsidiaries that have no "meril" in the name. Only when the party
        # is NOT in the Lookup do we fall back to the .NET name-substring rule, so
        # an unmapped Meril entity still counts as a subsidiary.
        # Recent SAP dumps stopped including a Soldto-Name column at all (only
        # Payer Name survives) — without this fallback every row's sold-to key
        # is blank, no Lookup match is possible, and the "meril" substring check
        # always fails, so the whole subsidiary total silently drops to zero.
        sold_to = (row.get("sold_to_name") or row.get("payer_name") or "")
        party = self._party.get(normalize_party_name(sold_to))
        if party is not None:
            is_subsidiary = bool(party[2])  # (country, region, is_subsidiary, is_domestic)
        else:
            is_subsidiary = "meril" in sold_to.lower()
        row["is_subsidiary"] = is_subsidiary
        row["subsidiary_name"] = sold_to if is_subsidiary else None

        row["lob"] = lob
        row["push_division"] = self._division.get(normalize_header(country_name)) if country_name else None

        fy, seq, label, cal = financial_period(row.get("billing_date"))
        row["fin_year"] = fy
        row["fin_month_seq"] = seq
        row["fin_month_label"] = label
        row["calendar_month"] = cal

        value_lc = row.get("invoice_value_lc") or Decimal("0")
        row["value_lakhs"] = (value_lc / Decimal("100000")).quantize(Decimal("0.0001"))

        btype = (row.get("billing_type") or "").strip().upper()
        fi = (row.get("fi_status") or "").lower()
        row["is_excluded"] = btype in EXCLUDED_BILLING_TYPES
        # Export refinement: drop domestic India + SAARC neighbours. The
        # destination country (ship-to → sold-to) is the primary signal; the
        # Soldto-Name lookup adds name-based domestic customers; a city-substring
        # check only kicks in when the destination country is blank.
        dest = (row.get("ship_to_country") or row.get("sold_to_country") or "").strip().upper()
        if not is_domestic and not dest:
            city = (row.get("sold_to_city") or "").lower()
            is_domestic = any(sub in city for sub in DOMESTIC_CITY_SUBSTRINGS)
        row["is_domestic"] = bool(is_domestic)
        row["is_reversal"] = (btype == "S1") or (value_lc < 0)
        # Proforma (PFI) or anything not yet posted in FI counts as pending.
        row["is_pending"] = btype.startswith("PFI") or (bool(fi) and "posted" not in fi)

        # Mapping status for the Exceptions view. Country not being in the master
        # is the real maintenance signal (a new customer/country to add). LOB is
        # best-effort, so it only flags an exception when NO label could be derived
        # at all — a sparse LOB master must not turn every row into an "exception".
        no_lob = not (lob or "").strip()
        if not country_mapped and no_lob:
            row["mapping_status"] = ExportBillingDetails.MappingStatus.UNMAPPED_BOTH
        elif not country_mapped:
            row["mapping_status"] = ExportBillingDetails.MappingStatus.UNMAPPED_COUNTRY
        elif no_lob:
            row["mapping_status"] = ExportBillingDetails.MappingStatus.UNMAPPED_LOB
        else:
            row["mapping_status"] = ExportBillingDetails.MappingStatus.OK
        return row


# ISO-2 → (country name, region) for countries not (yet) in the Country
# master. Keeps dashboards accurate & readable for markets the admin hasn't
# formally added yet; those rows still surface in Exceptions for maintenance.
ISO2_INFO = {
    # West Europe
    "DE": ("Germany", "West Europe"), "IT": ("Italy", "West Europe"),
    "FR": ("France", "West Europe"), "ES": ("Spain", "West Europe"),
    "GB": ("United Kingdom", "West Europe"), "NL": ("Netherlands", "West Europe"),
    "BE": ("Belgium", "West Europe"), "PT": ("Portugal", "West Europe"),
    "GR": ("Greece", "West Europe"), "SE": ("Sweden", "West Europe"),
    "DK": ("Denmark", "West Europe"), "AT": ("Austria", "West Europe"),
    "CH": ("Switzerland", "West Europe"), "IE": ("Ireland", "West Europe"),
    "FI": ("Finland", "West Europe"), "NO": ("Norway", "West Europe"),
    "MT": ("Malta", "West Europe"), "LU": ("Luxembourg", "West Europe"),
    "IS": ("Iceland", "West Europe"), "CY": ("Cyprus", "West Europe"),
    # East Europe
    "PL": ("Poland", "East Europe"), "HU": ("Hungary", "East Europe"),
    "RO": ("Romania", "East Europe"), "BG": ("Bulgaria", "East Europe"),
    "CZ": ("Czech Republic", "East Europe"), "SK": ("Slovakia", "East Europe"),
    "HR": ("Croatia", "East Europe"), "SI": ("Slovenia", "East Europe"),
    "LT": ("Lithuania", "East Europe"), "LV": ("Latvia", "East Europe"),
    "EE": ("Estonia", "East Europe"), "BA": ("Bosnia and Herzegovina", "East Europe"),
    "RS": ("Serbia", "East Europe"), "MD": ("Moldova", "East Europe"),
    "ME": ("Montenegro", "East Europe"), "MK": ("North Macedonia", "East Europe"),
    "AL": ("Albania", "East Europe"),
    # CIS
    "RU": ("Russia", "CIS"), "UA": ("Ukraine", "CIS"), "BY": ("Belarus", "CIS"),
    "KZ": ("Kazakhstan", "CIS"), "UZ": ("Uzbekistan", "CIS"),
    "AM": ("Armenia", "CIS"), "GE": ("Georgia", "CIS"), "AZ": ("Azerbaijan", "CIS"),
    "KG": ("Kyrgyzstan", "CIS"), "TJ": ("Tajikistan", "CIS"), "TM": ("Turkmenistan", "CIS"),
    # Middle East
    "TR": ("Turkey", "Middle East"), "AE": ("UAE", "Middle East"),
    "SA": ("Saudi Arabia", "Middle East"), "EG": ("Egypt", "Middle East"),
    "JO": ("Jordan", "Middle East"), "LB": ("Lebanon", "Middle East"),
    "IL": ("Israel", "Middle East"), "QA": ("Qatar", "Middle East"),
    "KW": ("Kuwait", "Middle East"), "OM": ("Oman", "Middle East"),
    "BH": ("Bahrain", "Middle East"), "IQ": ("Iraq", "Middle East"),
    "IR": ("Iran", "Middle East"), "YE": ("Yemen", "Middle East"),
    "SY": ("Syria", "Middle East"), "PS": ("Palestine", "Middle East"),
    "AF": ("Afghanistan", "Middle East"),
    # Africa
    "ZA": ("South Africa", "Africa"), "NG": ("Nigeria", "Africa"),
    "KE": ("Kenya", "Africa"), "MA": ("Morocco", "Africa"),
    "DZ": ("Algeria", "Africa"), "GH": ("Ghana", "Africa"),
    "RW": ("Rwanda", "Africa"), "UG": ("Uganda", "Africa"),
    "ZM": ("Zambia", "Africa"), "CM": ("Cameroon", "Africa"),
    "CI": ("Côte d'Ivoire", "Africa"), "BF": ("Burkina Faso", "Africa"),
    "TZ": ("Tanzania", "Africa"), "ET": ("Ethiopia", "Africa"),
    "SN": ("Senegal", "Africa"), "MZ": ("Mozambique", "Africa"),
    "BW": ("Botswana", "Africa"), "NA": ("Namibia", "Africa"),
    "TN": ("Tunisia", "Africa"), "LY": ("Libya", "Africa"),
    "SD": ("Sudan", "Africa"), "ZW": ("Zimbabwe", "Africa"),
    "MW": ("Malawi", "Africa"), "GA": ("Gabon", "Africa"), "CD": ("DR Congo", "Africa"),
    # Latin America
    "BR": ("Brazil", "Latin America"), "MX": ("Mexico", "Latin America"),
    "AR": ("Argentina", "Latin America"), "CO": ("Colombia", "Latin America"),
    "CL": ("Chile", "Latin America"), "PE": ("Peru", "Latin America"),
    "EC": ("Ecuador", "Latin America"), "UY": ("Uruguay", "Latin America"),
    "PA": ("Panama", "Latin America"), "CR": ("Costa Rica", "Latin America"),
    "DO": ("Dominican Republic", "Latin America"), "HN": ("Honduras", "Latin America"),
    "JM": ("Jamaica", "Latin America"), "GT": ("Guatemala", "Latin America"),
    "BO": ("Bolivia", "Latin America"), "PY": ("Paraguay", "Latin America"),
    "SV": ("El Salvador", "Latin America"), "NI": ("Nicaragua", "Latin America"),
    "TT": ("Trinidad and Tobago", "Latin America"), "VE": ("Venezuela", "Latin America"),
    "CU": ("Cuba", "Latin America"),
    # North America
    "US": ("USA", "North America"), "CA": ("Canada", "North America"),
    # South East Asia
    "SG": ("Singapore", "South East Asia"), "MY": ("Malaysia", "South East Asia"),
    "TH": ("Thailand", "South East Asia"), "ID": ("Indonesia", "South East Asia"),
    "VN": ("Vietnam", "South East Asia"), "PH": ("Philippines", "South East Asia"),
    "MM": ("Myanmar", "South East Asia"), "KH": ("Cambodia", "South East Asia"),
    "LA": ("Laos", "South East Asia"), "BD": ("Bangladesh", "South East Asia"),
    # Asia (South / East / Central)
    "CN": ("China", "Asia"), "JP": ("Japan", "Asia"), "KR": ("South Korea", "Asia"),
    "TW": ("Taiwan", "Asia"), "HK": ("Hong Kong", "Asia"), "MO": ("Macau", "Asia"),
    "LK": ("Sri Lanka", "Asia"), "NP": ("Nepal", "Asia"), "PK": ("Pakistan", "Asia"),
    "MV": ("Maldives", "Asia"), "BT": ("Bhutan", "Asia"), "MN": ("Mongolia", "Asia"),
    # Oceania
    "AU": ("Australia", "Oceania"), "NZ": ("New Zealand", "Oceania"),
    "FJ": ("Fiji", "Oceania"), "PG": ("Papua New Guinea", "Oceania"),
    # Data-quality placeholder seen in some SAP dumps
    "UN": ("Unspecified", "Unmapped"),
}


def country_name_from_code(code):
    if not code:
        return None
    info = ISO2_INFO.get(code.strip().upper())
    return info[0] if info else None
