#!/usr/bin/env python3
"""
Mail the code-quality digest to whoever pushed the commit.

WHY THIS EXISTS. GitLab will not do it. Two independent reasons:

  1. code-quality and sonarqube are allow_failure:true, so when they fail the
     pipeline is "passed with warnings", not "failed". GitLab's pipeline email
     fires on `failed`. There is no event, so no mail is sent and the pipeline
     looks green.
  2. Even a genuine failed-pipeline mail carries only the project, branch,
     commit and the NAMES of the failed jobs. It never embeds log output, so it
     could not answer "what exactly was the warning?" anyway.

And a trap for whoever changes this: a downstream `when: on_failure` job would
NOT fire either, because allow_failure:true makes the job count as success for
pipeline flow. Hence `when: always` here, with this script deciding for itself
whether there is anything worth sending.

INPUTS   quality-reports/summary.json    from scripts/ci_quality_report.py
         quality-reports/sonar-status.txt  from the sonarqube job (optional)

SMTP configuration reuses the application's own variable names (EMAIL_HOST,
EMAIL_HOST_USER, …) so the values can be copied straight out of .env into
Settings → CI/CD → Variables without translation.

This script NEVER fails the pipeline. Missing credentials, an unreachable relay
or a malformed report all print a notice and exit 0 — a broken notifier must not
be the reason a deploy stops.
"""
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate

REPORT_DIR = os.environ.get("QUALITY_REPORT_DIR", "quality-reports")

# Severity → colour for the HTML table. Deliberately readable on white; mail
# clients ignore stylesheets, so everything is inlined.
SEV_COLOUR = {
    "HIGH": "#b91c1c",
    "BLOCKER": "#b91c1c",
    "MAJOR": "#b45309",
    "MEDIUM": "#b45309",
    "LOW": "#6b7280",
}


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def env_bool(name, default):
    raw = env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def notice(msg):
    print(f"  {msg}")


def load_summary():
    path = os.path.join(REPORT_DIR, "summary.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        notice(f"no {path} — the code-quality job did not run or produced nothing")
        return None
    except json.JSONDecodeError as exc:
        notice(f"{path} is not valid JSON ({exc}) — nothing to report")
        return None


def load_sonar():
    """(status, detail). status is one of ok / unreachable / gate-failed / absent."""
    path = os.path.join(REPORT_DIR, "sonar-status.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln.rstrip() for ln in fh if ln.strip()]
    except FileNotFoundError:
        return "absent", ""
    if not lines:
        return "absent", ""
    return lines[0].strip(), "\n".join(lines[1:]).strip()


SONAR_WORDING = {
    "ok": ("passed", "Quality gate passed."),
    "gate-failed": ("FAILED", "Quality gate failed — open the Sonar project for the findings."),
    "unreachable": (
        "UNREACHABLE",
        "The SonarQube server could not be reached, so nothing was analysed. "
        "This is a server outage, not a finding about your code.",
    ),
    "absent": ("not run", "The sonarqube job did not run for this pipeline."),
}


def recipients():
    """Pusher first, then any fixed CC list. Deduplicated, order preserved."""
    out = []
    for candidate in [env("GITLAB_USER_EMAIL")] + env("QUALITY_NOTIFY_CC").replace(";", ",").split(","):
        addr = candidate.strip()
        if addr and addr not in out:
            out.append(addr)
    return out


def build_subject(summary, sonar_status):
    ref = summary.get("branch") or "?"
    sha = summary.get("commit") or "?"
    project = (summary.get("project") or "nexgen").split("/")[-1]

    bits = []
    high = summary.get("bandit_high", 0)
    if high:
        bits.append(f"{high} high-severity")
    failed = summary.get("failed") or []
    if failed:
        bits.append(f"{summary.get('reported_findings', 0)} findings")
    if sonar_status == "unreachable":
        bits.append("Sonar unreachable")
    elif sonar_status == "gate-failed":
        bits.append("Sonar gate failed")

    headline = ", ".join(bits) if bits else "clean"
    return f"[{project}] Code quality: {headline} — {ref} ({sha})"


def build_text(summary, sonar_status, sonar_detail):
    digest_path = os.path.join(REPORT_DIR, "summary.txt")
    try:
        with open(digest_path, encoding="utf-8") as fh:
            digest = fh.read()
    except FileNotFoundError:
        digest = "(summary.txt missing)\n"

    verdict, explanation = SONAR_WORDING.get(sonar_status, SONAR_WORDING["absent"])
    lines = [
        f"Commit   {summary.get('commit', '')}  {summary.get('commit_title', '')}",
        f"Branch   {summary.get('branch', '')}",
        f"Pushed   {summary.get('author', '')} <{summary.get('author_email', '')}>",
        "",
        digest,
        f"SonarQube: {verdict}",
        f"  {explanation}",
    ]
    if sonar_detail:
        lines += ["", "  " + sonar_detail.replace("\n", "\n  ")]
    lines += [
        "",
        "Findings are line-anchored in GitLab — you do not need to read the job log:",
        f"  Pipeline      {summary.get('pipeline_url', '')}",
        f"  Code Quality  {summary.get('pipeline_url', '')}/codequality_report",
        "",
        "Raw tool output (flake8.txt, bandit.json) is in the code-quality job artifacts.",
        "",
        "-- ",
        "Sent by scripts/ci_notify_quality.py because allow_failure:true jobs",
        "never trigger GitLab's own pipeline email.",
    ]
    return "\n".join(lines)


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_html(summary, sonar_status, sonar_detail):
    verdict, explanation = SONAR_WORDING.get(sonar_status, SONAR_WORDING["absent"])
    pipeline = summary.get("pipeline_url", "")

    rows = []
    for name, tool in (summary.get("tools") or {}).items():
        failed = tool.get("status") == "fail"
        colour = "#b45309" if failed else "#15803d"
        label = "FAIL" if failed else "ok"
        rows.append(
            f"<tr>"
            f'<td style="padding:6px 12px;border-bottom:1px solid #e5e7eb;font-family:monospace">{esc(name)}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #e5e7eb;color:{colour};font-weight:600">{label}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #e5e7eb;text-align:right">{esc(tool.get("count", 0))}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #e5e7eb;color:#4b5563">{esc(tool.get("detail", ""))}</td>'
            f"</tr>"
        )

    worst = []
    for w in summary.get("worst") or []:
        colour = SEV_COLOUR.get(w.get("severity", ""), "#6b7280")
        worst.append(
            f'<li style="margin-bottom:8px">'
            f'<code style="color:{colour};font-weight:600">{esc(w.get("severity"))} {esc(w.get("code"))}</code> '
            f'<code>{esc(w.get("path"))}:{esc(w.get("line"))}</code><br>'
            f'<span style="color:#4b5563">{esc(w.get("text"))}</span></li>'
        )
    worst_block = ""
    if worst:
        total = summary.get("worst_total", len(worst))
        shown = len(worst)
        more = f" (showing {shown} of {total})" if total > shown else ""
        worst_block = (
            f'<h3 style="font-size:14px;margin:24px 0 8px">Worth acting on first{more}</h3>'
            f'<ul style="padding-left:20px;margin:0">{"".join(worst)}</ul>'
        )

    sonar_colour = {"ok": "#15803d", "gate-failed": "#b91c1c", "unreachable": "#b45309"}.get(sonar_status, "#6b7280")
    sonar_extra = (
        f'<pre style="background:#f9fafb;padding:8px;overflow-x:auto;font-size:12px">{esc(sonar_detail)}</pre>'
        if sonar_detail
        else ""
    )

    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;color:#111827;max-width:760px">
  <h2 style="font-size:17px;margin:0 0 4px">Code quality report</h2>
  <p style="margin:0 0 16px;color:#6b7280">
    <code>{esc(summary.get('commit', ''))}</code> on <code>{esc(summary.get('branch', ''))}</code>
    &middot; {esc(summary.get('commit_title', ''))}<br>
    pushed by {esc(summary.get('author', ''))}
  </p>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <thead><tr style="text-align:left;background:#f3f4f6">
      <th style="padding:6px 12px">tool</th><th style="padding:6px 12px">status</th>
      <th style="padding:6px 12px;text-align:right">count</th><th style="padding:6px 12px">detail</th>
    </tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  {worst_block}
  <h3 style="font-size:14px;margin:24px 0 8px">SonarQube: <span style="color:{sonar_colour}">{esc(verdict)}</span></h3>
  <p style="margin:0 0 8px;color:#4b5563">{esc(explanation)}</p>
  {sonar_extra}
  <h3 style="font-size:14px;margin:24px 0 8px">Where to look</h3>
  <p style="margin:0;color:#4b5563">
    Findings are anchored to their lines in GitLab, so you do not need to read the job log.<br>
    <a href="{esc(pipeline)}">Pipeline</a> &middot;
    <a href="{esc(pipeline)}/codequality_report">Code Quality report</a><br>
    Full reformatting diffs ship as artifacts on the <code>code-quality</code> job.
  </p>
  <p style="margin:24px 0 0;color:#9ca3af;font-size:12px">
    Sent by <code>scripts/ci_notify_quality.py</code> because <code>allow_failure:true</code>
    jobs never trigger GitLab's own pipeline email.
  </p>
</div>"""


def send(msg, host, port, user, password, use_ssl, use_tls, timeout):
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=timeout) as s:
        s.ehlo()
        if use_tls:
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
        if user:
            s.login(user, password)
        s.send_message(msg)


def main():
    print("── code-quality notification ─────────────────────────────────")

    summary = load_summary()
    sonar_status, sonar_detail = load_sonar()

    if summary is None:
        # Sonar being down is itself worth mailing, but with no summary there is
        # no digest to build a message around. Say so and stop.
        notice("nothing to report")
        return 0

    something_wrong = bool(summary.get("failed")) or sonar_status in ("gate-failed", "unreachable")
    if not something_wrong and not env_bool("QUALITY_NOTIFY_ALWAYS", False):
        notice("everything clean — no mail sent (set QUALITY_NOTIFY_ALWAYS=true to send anyway)")
        return 0

    to = recipients()
    if not to:
        notice("no recipient: GITLAB_USER_EMAIL is empty and QUALITY_NOTIFY_CC is unset")
        return 0

    host = env("EMAIL_HOST")
    user = env("EMAIL_HOST_USER")
    password = env("EMAIL_HOST_PASSWORD")
    sender = env("DEFAULT_FROM_EMAIL") or user
    if not host or not sender:
        notice("SMTP is not configured for CI — printing the digest instead of mailing it.")
        notice("Add EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD and")
        notice("DEFAULT_FROM_EMAIL under Settings → CI/CD → Variables (mask the password).")
        print()
        print(build_text(summary, sonar_status, sonar_detail))
        return 0

    use_ssl = env_bool("EMAIL_USE_SSL", False)
    port = int(env("EMAIL_PORT") or (465 if use_ssl else 587))
    # Matches core/settings.py: TLS defaults on, and is meaningless with SSL.
    use_tls = env_bool("EMAIL_USE_TLS", True) and not use_ssl
    timeout = int(env("EMAIL_TIMEOUT") or "20")

    msg = EmailMessage()
    msg["Subject"] = build_subject(summary, sonar_status)
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    # Groups every report for one branch into a single mail thread.
    if summary.get("project") and summary.get("branch"):
        msg["References"] = f"<quality.{summary['project']}.{summary['branch']}@e-meril.in>"
    msg.set_content(build_text(summary, sonar_status, sonar_detail))
    msg.add_alternative(build_html(summary, sonar_status, sonar_detail), subtype="html")

    try:
        send(msg, host, port, user, password, use_ssl, use_tls, timeout)
    except Exception as exc:  # noqa: BLE001 — a notifier must never break a pipeline
        notice(f"could not send mail via {host}:{port} — {type(exc).__name__}: {exc}")
        notice("printing the digest instead:")
        print()
        print(build_text(summary, sonar_status, sonar_detail))
        return 0

    notice(f"sent to {', '.join(to)} via {host}:{port}")
    notice(f"subject: {msg['Subject']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
