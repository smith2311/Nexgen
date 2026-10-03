#!/usr/bin/env python3
"""
Turn raw linter output into the two things CI can actually use.

  gl-code-quality-report.json   A GitLab Code Quality report. GitLab renders
                                these inline on the changed lines of a merge
                                request diff and in the pipeline's Code Quality
                                tab, so nobody has to open a job log to find out
                                what the warning was.
  summary.json / summary.txt    A digest: pass/fail and a count per tool, plus
                                the worst findings spelled out.
                                scripts/ci_notify_quality.py mails the digest;
                                scripts/ci-quality.sh prints it.

Stdlib only, so this runs in a bare python:3.13-slim container with no pip step.

black and isort are not run by this pipeline and so are absent here. This
codebase aligns assignment columns on purpose in 719 places; black strips that
alignment from 260 files, so the two cannot both be satisfied and the codebase
picked alignment (see the E221/E272 rationale in .flake8). Reporting a tool that
does not run would be worse than omitting it — a permanent red FAIL nobody can
ever clear teaches people to ignore the digest.

Failure is derived from the artifacts, not from exit codes: a tool failed iff it
produced findings. That keeps the contract with ci-quality.sh to one direction —
the shell script does not have to smuggle four exit codes across a container
boundary.

  python ci_quality_report.py --in <raw-dir> --out <report-dir>
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter

# bandit's own severity vocabulary → GitLab's. GitLab accepts info, minor,
# major, critical, blocker.
BANDIT_SEVERITY = {"HIGH": "critical", "MEDIUM": "major", "LOW": "minor"}

# How many worst-case findings the digest spells out in full. The rest are left
# to the Code Quality tab — a digest that lists a thousand findings is a log.
DIGEST_LIMIT = 15


def read(path):
    """File content, or '' when the tool never ran."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def normalise(path):
    """Linter paths → repo-relative, which is what GitLab matches against.

    flake8 and bandit report ./app/views.py. The /src/ prefix is stripped too
    because a tool handed an absolute working directory reports /src/app/views.py,
    and GitLab matches neither form against the diff.
    """
    path = path.strip()
    for prefix in ("/src/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return path.lstrip("/")


def flake8_severity(code):
    """flake8 codes → GitLab severities, so real bugs outrank whitespace."""
    if code.startswith(("E9", "F82")):
        # Syntax errors and undefined names. These break at runtime.
        return "blocker"
    if code.startswith(("F4", "F5", "F6", "F7", "F8", "C9")):
        # Unused imports/variables, f-string mistakes, complexity.
        return "major"
    return "minor"


class Fingerprints:
    """Stable, unique ids for GitLab to track a finding across pipelines.

    GitLab rejects a report with duplicate fingerprints, and the same code can
    legitimately fire twice on one line (bandit's B308 and B703 both flag one
    mark_safe call). The occurrence counter disambiguates without making the id
    depend on where the finding sits in the file, so it survives edits above it.
    """

    def __init__(self):
        self._seen = Counter()

    def of(self, *parts):
        key = "|".join(parts)
        self._seen[key] += 1
        salted = f"{key}|{self._seen[key]}"
        return hashlib.sha256(salted.encode()).hexdigest()[:32]


FLAKE8_LINE = re.compile(r"^(?P<path>.+?):(?P<row>\d+):(?P<col>\d+): (?P<code>[A-Z]+\d+) (?P<text>.*)$")


def parse_flake8(raw):
    out = []
    for line in raw.splitlines():
        m = FLAKE8_LINE.match(line.strip())
        if m:
            out.append(
                {
                    "path": normalise(m.group("path")),
                    "line": int(m.group("row")),
                    "code": m.group("code"),
                    "text": m.group("text").strip(),
                }
            )
    return out


def parse_bandit(raw):
    """bandit -f json. Returns (findings, scan_errors)."""
    if not raw.strip():
        return [], []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], [f"bandit produced unparseable JSON: {exc}"]
    findings = []
    for r in data.get("results", []):
        findings.append(
            {
                "path": normalise(r.get("filename", "")),
                "line": r.get("line_number", 1),
                "code": r.get("test_id", "B000"),
                "name": r.get("test_name", ""),
                "text": (r.get("issue_text") or "").strip(),
                "severity": (r.get("issue_severity") or "MEDIUM").upper(),
                "confidence": (r.get("issue_confidence") or "").upper(),
                "more_info": r.get("more_info", ""),
            }
        )
    errors = [str(e) for e in data.get("errors", [])]
    return findings, errors


def build_codeclimate(flake8, bandit):
    fp = Fingerprints()
    report = []
    for f in flake8:
        report.append(
            {
                "description": f"[flake8 {f['code']}] {f['text']}",
                "check_name": f["code"],
                "fingerprint": fp.of("flake8", f["path"], f["code"], f["text"]),
                "severity": flake8_severity(f["code"]),
                "location": {"path": f["path"], "lines": {"begin": f["line"]}},
            }
        )
    for b in bandit:
        detail = f"[bandit {b['code']}] {b['text']}"
        if b["more_info"]:
            detail = f"{detail} ({b['more_info']})"
        report.append(
            {
                "description": detail,
                "check_name": b["code"],
                "fingerprint": fp.of("bandit", b["path"], b["code"], b["text"]),
                "severity": BANDIT_SEVERITY.get(b["severity"], "major"),
                "location": {"path": b["path"], "lines": {"begin": b["line"]}},
            }
        )
    return report


def build_summary(flake8, bandit, bandit_errors):
    by_code = Counter(f["code"] for f in flake8)
    bandit_by_sev = Counter(b["severity"] for b in bandit)

    # The findings a human should look at first: anything bandit calls HIGH, then
    # anything flake8 considers a real bug rather than a style nit.
    worst = [
        {
            "tool": "bandit",
            "severity": b["severity"],
            "code": b["code"],
            "path": b["path"],
            "line": b["line"],
            "text": b["text"],
        }
        for b in bandit
        if b["severity"] == "HIGH"
    ]
    worst += [
        {
            "tool": "flake8",
            "severity": "BLOCKER" if flake8_severity(f["code"]) == "blocker" else "MAJOR",
            "code": f["code"],
            "path": f["path"],
            "line": f["line"],
            "text": f["text"],
        }
        for f in flake8
        if flake8_severity(f["code"]) in ("blocker", "major")
    ]

    tools = {
        "flake8": {
            "status": "fail" if flake8 else "pass",
            "count": len(flake8),
            "detail": ", ".join(f"{code} x{n}" for code, n in by_code.most_common(6)) or "clean",
            "artifact": "flake8.txt",
        },
        "bandit": {
            "status": "fail" if bandit else "pass",
            "count": len(bandit),
            "detail": ", ".join(f"{sev.lower()} x{n}" for sev, n in bandit_by_sev.most_common()) or "clean",
            "artifact": "bandit.json",
        },
    }

    return {
        "project": os.environ.get("CI_PROJECT_PATH", ""),
        "branch": os.environ.get("CI_COMMIT_REF_NAME", ""),
        "commit": os.environ.get("CI_COMMIT_SHORT_SHA", ""),
        "commit_title": os.environ.get("CI_COMMIT_TITLE", ""),
        "author": os.environ.get("GITLAB_USER_NAME", ""),
        "author_email": os.environ.get("GITLAB_USER_EMAIL", ""),
        "pipeline_url": os.environ.get("CI_PIPELINE_URL", ""),
        "job_url": os.environ.get("CI_JOB_URL", ""),
        "tools": tools,
        "failed": [name for name, t in tools.items() if t["status"] == "fail"],
        "total_findings": len(flake8) + len(bandit),
        "reported_findings": len(flake8) + len(bandit),
        "top_flake8": by_code.most_common(10),
        "bandit_high": bandit_by_sev.get("HIGH", 0),
        "worst": worst[:DIGEST_LIMIT],
        "worst_total": len(worst),
        "scan_errors": bandit_errors,
    }


def render_text(s):
    """The digest as plain text — printed to the job log and mailed as-is."""
    L = []
    L.append("── code quality summary ──────────────────────────────────────")
    if s["commit"]:
        L.append(f"  {s['project']} @ {s['branch']} ({s['commit']})")
    L.append("")
    L.append(f"  {'tool':<8} {'status':<7} {'count':>6}  detail")
    L.append(f"  {'-' * 8} {'-' * 7} {'-' * 6}  {'-' * 40}")
    for name, t in s["tools"].items():
        mark = "FAIL" if t["status"] == "fail" else "ok"
        L.append(f"  {name:<8} {mark:<7} {t['count']:>6}  {t['detail']}")
    L.append("")

    if s["worst"]:
        L.append(f"  Worth acting on first ({s['worst_total']} total, showing {len(s['worst'])}):")
        for w in s["worst"]:
            L.append(f"    [{w['severity']:<7}] {w['code']:<6} {w['path']}:{w['line']}")
            L.append(f"              {w['text']}")
        L.append("")

    if s["scan_errors"]:
        L.append("  Scanner errors (a tool could not finish):")
        for e in s["scan_errors"]:
            L.append(f"    ! {e}")
        L.append("")

    if s["failed"]:
        L.append(f"  ✗ failed: {' '.join(s['failed'])}")
        L.append(f"  {s['reported_findings']} line-anchored finding(s) are in the Code Quality tab")
        L.append("  and inline on the merge request diff — no need to read this log.")
        L.append("  Raw output: flake8.txt / bandit.json in the job artifacts.")
    else:
        L.append("  ✓ all quality checks passed")
    L.append("──────────────────────────────────────────────────────────────")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, help="directory holding the raw linter output")
    ap.add_argument("--out", dest="dst", required=True, help="directory to write the reports into")
    args = ap.parse_args()

    flake8 = parse_flake8(read(os.path.join(args.src, "flake8.txt")))
    bandit, bandit_errors = parse_bandit(read(os.path.join(args.src, "bandit.json")))

    os.makedirs(args.dst, exist_ok=True)

    codeclimate = build_codeclimate(flake8, bandit)
    with open(os.path.join(args.dst, "gl-code-quality-report.json"), "w", encoding="utf-8") as fh:
        json.dump(codeclimate, fh, indent=1)

    summary = build_summary(flake8, bandit, bandit_errors)
    with open(os.path.join(args.dst, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    text = render_text(summary)
    with open(os.path.join(args.dst, "summary.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    sys.stdout.write(text)

    # Non-zero iff something was found, so ci-quality.sh can just propagate it.
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
