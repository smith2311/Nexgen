#!/bin/sh
# Deploy an already-built image tag to production over SSH.
#
# Which tag ships: $CI_COMMIT_TAG on a tag pipeline (`git tag v5.3.0 && git push
# origin v5.3.0`), else $CI_COMMIT_SHA. Prod's APP_TAG is set to whichever it
# was, so `nexgen-deploy current-tag` — and therefore the rollback target — reads
# as a version when releases are tagged and as a SHA when they are not.
#
# Runs ON THE RUNNER. Every privileged action on prod goes through one fixed,
# root-owned wrapper — /usr/local/bin/nexgen-deploy — rather than an arbitrary
# remote shell. Prod hosts ~28 containers; a blanket sudo grant would expose all
# of them to whoever holds the CI key. See scripts/prod/nexgen-deploy.
#
# Required CI/CD variables:
#   PROD_HOST              hostname or IP of the production server
#   PROD_USER              ssh user
#   PROD_SSH_KEY           File-type variable holding the private key
#
# Optional:
#   PROD_PORT              ssh port                 (default 22)
#   PROD_USE_SUDO          1 when containers run as root (default 1)
#   PROD_HEALTH_TIMEOUT    seconds to wait for health    (default 180)
#   PROD_URL               public URL, verified after deploy (warn-only)
#
# Registry credentials are deliberately NOT here: prod runs `podman login` once
# and keeps them locally, so no registry secret ever passes through CI.
set -eu

for var in PROD_HOST PROD_USER PROD_SSH_KEY CI_COMMIT_SHA; do
    eval "val=\${$var:-}"
    if [ -z "$val" ]; then
        echo "✗ required variable $var is not set" >&2
        echo "  Add it under Settings → CI/CD → Variables." >&2
        exit 1
    fi
done

PROD_PORT="${PROD_PORT:-22}"
PROD_USE_SUDO="${PROD_USE_SUDO:-1}"
PROD_HEALTH_TIMEOUT="${PROD_HEALTH_TIMEOUT:-180}"
WRAPPER="/usr/local/bin/nexgen-deploy"
# A version tag when the pipeline was started by one, otherwise the commit SHA.
# Both name the same image — publish-version aliases rather than rebuilds — so
# this only decides how legibly prod's APP_TAG and the rollback target read.
NEW_TAG="${CI_COMMIT_TAG:-$CI_COMMIT_SHA}"

[ "$PROD_USE_SUDO" = "1" ] && SUDO="sudo -n " || SUDO=""

KEY=$(mktemp)
chmod 600 "$KEY"
cat "$PROD_SSH_KEY" > "$KEY"
# A File-type CI variable frequently arrives without a trailing newline, and
# some OpenSSH builds reject a key whose final armor line is not newline
# terminated. Append one if it is missing.
[ -n "$(tail -c1 "$KEY")" ] && printf '\n' >> "$KEY"

# Repair armor that got glued to the body during copy/paste — pasting a key
# into a web form very commonly loses the newline after -----BEGIN, producing
# "-----BEGIN OPENSSH PRIVATE KEY-----b3BlbnNz..." on one line, which openssl
# rejects with an unhelpful "error in libcrypto". Splitting it back out is
# safe: a key that is already well formed is left unchanged.
if ! ssh-keygen -l -f "$KEY" >/dev/null 2>&1; then
    sed -i -e 's|\(-----BEGIN [A-Z ]*PRIVATE KEY-----\)|\1\n|' \
           -e 's|\(-----END [A-Z ]*PRIVATE KEY-----\)|\n\1|' "$KEY"
    sed -i '/^[[:space:]]*$/d' "$KEY"
    if ssh-keygen -l -f "$KEY" >/dev/null 2>&1; then
        echo "! PROD_SSH_KEY had its armor lines glued to the key body; repaired"
        echo "  in memory for this run. Worth fixing the variable so each armor"
        echo "  line sits on its own line."
    fi
fi
trap 'rm -f "$KEY"' EXIT INT TERM

# Only ever invokes the wrapper, never a general shell.
prod() {
    ssh -i "$KEY" \
        -p "$PROD_PORT" \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=15 \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        "${PROD_USER}@${PROD_HOST}" "${SUDO}${WRAPPER} $*"
}

# Remote output can carry banners, MOTDs or sudo lectures. Take the last line
# only — a single stray line previously made the health code unparseable and
# would have rolled back a healthy deploy.
last_line() { tail -1 | tr -d '\r'; }

echo "── deploying tag ${NEW_TAG} to ${PROD_HOST} ──────────────────"

# ── Validate the key locally before SSH turns it into "error in libcrypto" ──
# A File-type CI variable pasted without its armor lines, or masked (GitLab
# masking can corrupt multi-line values), fails with a message that says
# nothing about the actual cause.
if ! grep -q -- "-----BEGIN .*PRIVATE KEY-----" "$KEY"; then
    echo "✗ PROD_SSH_KEY does not look like a private key." >&2
    echo "  It must contain the armor lines, not just the base64 body:" >&2
    echo "      -----BEGIN OPENSSH PRIVATE KEY-----" >&2
    echo "      ...base64..." >&2
    echo "      -----END OPENSSH PRIVATE KEY-----" >&2
    echo "  Also make sure 'Mask variable' is UNTICKED — masking can corrupt" >&2
    echo "  a multi-line value." >&2
    exit 1
fi

# ── Prove we can reach prod before doing anything else ──────────────────────
# Without this, a broken connection surfaces later as a misleading message
# ("no APP_TAG found on prod") that blames prod's config instead of the link.
if ! ssh_err=$(prod current-tag 2>&1 >/dev/null); then
    echo "✗ cannot run ${WRAPPER} on ${PROD_USER}@${PROD_HOST}:${PROD_PORT}" >&2
    echo "── ssh said ─────────────────────────────────────────────────" >&2
    echo "${ssh_err:-(no output)}" >&2
    # Shape of the key only — the armor lines are not secret, the body is.
    echo "── PROD_SSH_KEY as received ─────────────────────────────────" >&2
    echo "  lines: $(wc -l < "$KEY")" >&2
    echo "  first: $(head -1 "$KEY")" >&2
    echo "  last : $(tail -1 "$KEY")" >&2
    # A fingerprint is not secret, and it settles "is this even the right key?"
    # outright: compare it against `ssh-keygen -l -f ~USER/.ssh/authorized_keys`
    # on the far end. A mismatch means the variable holds a different key than
    # the one authorised there.
    echo "  fingerprint: $(ssh-keygen -l -f "$KEY" 2>/dev/null || echo '(unreadable — key is malformed)')" >&2
    echo "  ↑ this must appear in: ssh-keygen -l -f ~${PROD_USER}/.ssh/authorized_keys" >&2
    echo "─────────────────────────────────────────────────────────────" >&2
    echo "  Check, in order:" >&2
    echo "   1. PROD_SSH_KEY holds the whole key file, armor lines included" >&2
    echo "   2. 'Mask variable' is unticked on PROD_SSH_KEY" >&2
    echo "   3. the matching public key is in ~${PROD_USER}/.ssh/authorized_keys" >&2
    echo "   4. /etc/sudoers.d/nexgen-deploy grants NOPASSWD for ${WRAPPER}" >&2
    echo "   5. ${WRAPPER} exists and is executable on prod" >&2
    exit 1
fi

# ── Record the running tag so a bad deploy can be reversed ──────────────────
PREV_TAG=$(prod current-tag 2>/dev/null | last_line || true)
if [ -n "$PREV_TAG" ]; then
    echo "  currently running: $PREV_TAG  (rollback target)"
else
    echo "  ! no APP_TAG found on prod — rollback will be unavailable"
fi

# ── Pull first; production keeps serving the old image if this fails ─────────
echo "── pulling image on prod ─────────────────────────────────────"
prod pull "$NEW_TAG"

# ── Swap and restart ─────────────────────────────────────────────────────────
echo "── restarting with new tag ───────────────────────────────────"
prod restart "$NEW_TAG"

# ── Health check, run ON prod so it tests the app not the network path ──────
echo "── health check (up to ${PROD_HEALTH_TIMEOUT}s) ──────────────────────"
healthy=0
elapsed=0
while [ "$elapsed" -lt "$PROD_HEALTH_TIMEOUT" ]; do
    code=$(prod health 2>/dev/null | last_line) || code=""
    case "$code" in ''|*[!0-9]*) code="000" ;; esac
    if [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then
        echo "✓ production healthy (HTTP $code) after ${elapsed}s"
        healthy=1
        break
    fi
    sleep 5
    elapsed=$((elapsed + 5))
    [ $((elapsed % 30)) -eq 0 ] && echo "  ... ${elapsed}s (last status: $code)"
done

# ── Roll back on failure ─────────────────────────────────────────────────────
if [ "$healthy" -ne 1 ]; then
    echo "✗ production did not become healthy — rolling back" >&2
    prod logs >&2 2>&1 || true

    if [ -n "$PREV_TAG" ]; then
        echo "→ restoring ${PREV_TAG}" >&2
        if prod restart "$PREV_TAG"; then
            echo "✓ rolled back to $PREV_TAG" >&2
        else
            echo "✗ ROLLBACK ALSO FAILED — production needs manual attention" >&2
        fi
    else
        echo "✗ no previous tag recorded — cannot roll back automatically" >&2
    fi
    exit 1
fi

# ── Pin prod's checkout to what just shipped (hygiene, NOT a gate) ──────────
# The image serves the traffic; prod's checkout only supplies the compose file,
# .env and the media/logs mount points. No deploy step ever updated it, so it
# sat at whenever a human last pulled — an app deleted in git stayed on disk
# there indefinitely, which reads as "the deploy didn't remove the code" and,
# worse, would resurrect that app the moment anyone fell back to
# docker-compose.yml, which bind-mounts this directory over /app.
#
# Runs AFTER the health gate on purpose. Syncing first would swap the compose
# file underneath the deploy, and a bad compose change is exactly what the
# rollback above cannot undo — it only moves APP_TAG. The cost is that a
# compose-file change lands one deploy late: this deploy started the container
# from the on-disk compose file, and the synced version takes effect at the next
# restart. That is still strictly better than never syncing at all.
echo "── syncing prod checkout to ${NEW_TAG} ───────────────────────"
prod sync-source "$NEW_TAG" || echo "! checkout not synced — cosmetic, not a deploy failure"

# ── Public reachability check (verification, NOT a rollback trigger) ────────
# The gate above already proved the app itself is healthy on prod. This checks
# the path real users take — proxy, DNS, TLS. A failure here means the app is
# fine but something in front of it is not, and rolling back would not fix
# that, so it warns rather than reverting a good deploy.
if [ -n "${PROD_URL:-}" ]; then
    echo "── verifying public URL ──────────────────────────────────────"
    pub=""
    for _ in 1 2 3; do
        pub=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$PROD_URL" 2>/dev/null) || pub=""
        case "$pub" in ''|*[!0-9]*) pub="000" ;; esac
        [ "$pub" -ge 200 ] && [ "$pub" -lt 400 ] && break
        sleep 5
    done
    if [ "$pub" -ge 200 ] && [ "$pub" -lt 400 ]; then
        echo "✓ $PROD_URL responded HTTP $pub"
    else
        echo "! $PROD_URL returned ${pub} — the app is healthy on the host, so"
        echo "  this points at the proxy/TLS layer rather than the deploy."
        echo "  NOT rolling back; the new version is live and serving locally."
    fi
fi

echo
echo "✓ deployed ${NEW_TAG} to ${PROD_HOST}"
