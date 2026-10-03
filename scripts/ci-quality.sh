#!/bin/sh
# Code quality checks. Runs every linter, reports all of them, then fails if any
# failed — so a flake8 nit cannot hide a bandit finding.
#
# Everything runs inside a throwaway container: the runner host's Python is
# never touched and the versions are pinned to what requirements.txt declares.
#
#   ./scripts/ci-quality.sh
#
# WHAT THIS WRITES  (all under ./quality-reports, published as job artifacts)
#
#   gl-code-quality-report.json  GitLab Code Quality report. GitLab renders it
#                                inline on the changed lines of a merge request
#                                and in the pipeline's Code Quality tab. This is
#                                the point of the whole exercise: findings you
#                                can see without opening a job log.
#   summary.txt / summary.json   The digest. Printed below, and mailed to the
#                                pusher by scripts/ci_notify_quality.py.
#   flake8.txt / bandit.json     Raw tool output.
#
# The console output is deliberately the DIGEST ONLY: a job log nobody reads to
# the end is a job log that hides its own findings.
#
# WHY NO black / isort
# They used to run here and reported 260 and 99 files. Neither count could ever
# reach zero without reformatting the whole repository, because this codebase
# aligns assignment columns on purpose in 719 places and black strips exactly
# that. A check that is permanently red is not a check — it is noise that
# trains people to ignore the digest, including the bandit findings underneath
# it. The alignment is the house style (see E221/E272 in .flake8), so the
# formatter is the thing that goes.
#
# The CI job is allow_failure:true for now. This codebase has never had these
# enforced, so the first runs are a report, not a gate. Flip allow_failure off
# in .gitlab-ci.yml once the backlog is clear.
set -eu

IMAGE="docker.io/library/python:3.13-slim"
SRC="${CI_PROJECT_DIR:-$PWD}"
OUT="$SRC/quality-reports"

command -v podman >/dev/null || { echo "✗ podman not installed" >&2; exit 1; }

# Start clean: a stale report from a previous run would be published as this
# run's artifact and mailed as this commit's findings.
rm -rf "$OUT"
mkdir -p "$OUT"

echo "── code quality ──────────────────────────────────────────────"
echo "  image:   $IMAGE"
echo "  reports: ${OUT#"$SRC"/}"
echo

# Two mounts, not one: the source stays read-only so a linter cannot rewrite the
# checkout, while the report directory — which lives inside it — is mounted
# separately as writable. `|| true` on every tool because `set -e` is on and a
# findings-means-non-zero linter must not abort the run; the converter decides
# pass/fail from the artifacts afterwards.
podman run --rm \
    -v "$SRC":/src:ro \
    -v "$OUT":/out:z \
    -w /src \
    -e CI_PROJECT_PATH -e CI_COMMIT_REF_NAME -e CI_COMMIT_SHORT_SHA \
    -e CI_COMMIT_TITLE -e CI_PIPELINE_URL -e CI_JOB_URL \
    -e GITLAB_USER_NAME -e GITLAB_USER_EMAIL \
    "$IMAGE" \
    sh -euc '
        pip install --quiet --disable-pip-version-check \
            flake8==7.0.0 bandit 2>/dev/null

        echo "  running flake8, bandit …"

        # Explicit format so the converter never has to guess. .flake8 supplies
        # the exclusions and the ignore list.
        flake8 --format="%(path)s:%(row)d:%(col)d: %(code)s %(text)s" . \
            > /out/flake8.txt 2>&1 || true

        # Only report what is worth acting on: high/medium severity AND
        # high/medium confidence. Without both filters this is mostly noise
        # about asserts and subprocess calls in management commands.
        bandit -r . -q -f json -o /out/bandit.json \
            --exclude ./static,./staticfiles,./media,./.git,./migrations,./quality-reports \
            --severity-level medium \
            --confidence-level medium || true

        # Stdlib only, so no second pip step. Prints the digest and exits
        # non-zero iff a tool found something.
        python /src/scripts/ci_quality_report.py --in /out --out /out
    '
