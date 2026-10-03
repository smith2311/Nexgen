#!/bin/sh
# Smoke test: build this checkout, bring the stack up, and prove the app
# actually serves HTTP. Exits non-zero if it doesn't.
#
# Runs the same way locally and in CI:
#   ./scripts/ci-smoke.sh                       # auto-detects podman/docker
#   COMPOSE_CMD="podman-compose" ./scripts/ci-smoke.sh
#
# Tunables:
#   CI_APP_PORT      host port to publish            (default 8021)
#   CI_HEALTH_HOST   host to curl                    (default localhost;
#                                                     "docker" under dind)
#   CI_HEALTH_PATH   path to curl                    (default /)
#   CI_BOOT_TIMEOUT  seconds to wait for a response  (default 240)
#   CI_KEEP_UP       set to 1 to skip teardown (debugging)
set -eu

COMPOSE_FILE="docker-compose.ci.yml"
CI_APP_PORT="${CI_APP_PORT:-8021}"
CI_HEALTH_HOST="${CI_HEALTH_HOST:-localhost}"
CI_HEALTH_PATH="${CI_HEALTH_PATH:-/}"
CI_BOOT_TIMEOUT="${CI_BOOT_TIMEOUT:-240}"
PROJECT="${COMPOSE_PROJECT_NAME:-nexgen-ci-$$}"

export CI_APP_PORT

# ── Pick a compose implementation ────────────────────────────────────────────
if [ -n "${COMPOSE_CMD:-}" ]; then
    :
elif docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v podman-compose >/dev/null 2>&1; then
    COMPOSE_CMD="podman-compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    echo "✗ no compose implementation found (need docker compose or podman-compose)" >&2
    exit 1
fi

compose() { $COMPOSE_CMD -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }

# `compose ps -q <service>` is NOT portable — podman-compose 1.0.6 rejects the
# service argument outright ("unrecognized arguments: web"), which would make an
# early-exit check report a healthy container as dead and fail every build.
# Both podman-compose and docker compose do label containers with the standard
# com.docker.compose.{project,service} keys, so query the runtime directly.
case "$COMPOSE_CMD" in
    *podman*) RUNTIME="${RUNTIME:-podman}" ;;
    *)        RUNTIME="${RUNTIME:-docker}" ;;
esac

web_running() {
    [ -n "$($RUNTIME ps \
        --filter "label=com.docker.compose.project=$PROJECT" \
        --filter "label=com.docker.compose.service=web" \
        --format '{{.Names}}' 2>/dev/null)" ]
}

# Re-run a dead web container with its output wired to this job's stdout.
#
# Every dump above reads through `compose logs`, which is precisely what fails
# on this runner: the db dump comes back empty too, and Postgres always writes
# a startup banner. Once the log driver has swallowed the output there is one
# way left to find out why the app died — execute it again and let it write
# straight to the log. `start --attach` reuses the container's existing config,
# so this is the first attempt replayed rather than an approximation of it, and
# the throwaway CI database makes repeating the migrations free.
#
# Still worth having now that docker-compose.ci.yml pins a file-based driver:
# it survives the next runner that defaults to journald, and a container that
# dies before its log driver is ever attached.
#
# Only a container in `exited` state is replayed. One that is merely slow is
# still running, and `start --attach` on it would print a runtime error where
# the useful output should be.
replay_web() {
    web_c=$($RUNTIME ps -a \
        --filter "label=com.docker.compose.project=$PROJECT" \
        --filter "label=com.docker.compose.service=web" \
        --format '{{.Names}}' 2>/dev/null | head -1)
    [ -n "$web_c" ] || return 0
    [ "$($RUNTIME inspect "$web_c" --format '{{.State.Status}}' 2>/dev/null)" = "exited" ] || return 0

    echo "── replaying web in the foreground ──────────────────────────" >&2
    # Capped, so a container that hangs rather than crashing cannot stall the
    # job until the runner's own timeout. Failure is ignored: the replay is
    # diagnostic, and the verdict is the caller's exit 1.
    if command -v timeout >/dev/null 2>&1; then
        timeout 180 $RUNTIME start --attach "$web_c" >&2 2>&1 || true
    else
        $RUNTIME start --attach "$web_c" >&2 2>&1 || true
    fi
}

# `compose down` does NOT remove the network podman-compose created for the
# project — verified on 1.0.6: two runs here left nexgen-ci-local_default and
# nexgen-ci-smithfix_default behind, and nineteen older ones had accumulated
# with no containers attached.
#
# That leak is fatal on a long-lived runner rather than merely untidy. Podman
# carves networks out of 10.89.0.0/16 in /24s, so the pool is 256. The project
# name is unique per pipeline (nexgen-ci-$CI_PIPELINE_ID), so every smoke run
# burns one slot for good. Once the pool is exhausted, creating the network
# fails, the containers are created but never started, and `podman logs` prints
# nothing at all because they never ran — which reads as "the app is broken"
# rather than "the runner is full".
#
# Only networks with nothing attached are removed, so a concurrent run is never
# touched (smoke is serialised by resource_group anyway). Errors are ignored:
# tidying up must never be the reason a build fails.
prune_ci_networks() {
    [ "$RUNTIME" = "podman" ] || return 0
    for net in $($RUNTIME network ls --format '{{.Name}}' 2>/dev/null | grep '^nexgen-ci-' || true); do
        if [ -z "$($RUNTIME ps -a --filter "network=$net" --format '{{.Names}}' 2>/dev/null)" ]; then
            $RUNTIME network rm "$net" >/dev/null 2>&1 || true
        fi
    done
}

echo "── smoke test ───────────────────────────────────────────────"
echo "  compose : $COMPOSE_CMD"
echo "  project : $PROJECT"
echo "  health  : http://${CI_HEALTH_HOST}:${CI_APP_PORT}${CI_HEALTH_PATH}"
echo

teardown() {
    status=$?
    if [ "${CI_KEEP_UP:-0}" = "1" ]; then
        echo "CI_KEEP_UP=1 — leaving '$PROJECT' running"
    else
        echo "── tearing down ─────────────────────────────────────────────"
        compose down -v --remove-orphans >/dev/null 2>&1 || true
        # ...and the network `down` leaves behind. See prune_ci_networks.
        prune_ci_networks
    fi
    exit $status
}
trap teardown EXIT INT TERM

# Reclaim whatever earlier runs leaked before asking for another subnet. This
# is what lets a runner that has already exhausted the pool recover on its own.
prune_ci_networks

# ── Build ────────────────────────────────────────────────────────────────────
echo "── building image ───────────────────────────────────────────"
compose build

# ── Up ───────────────────────────────────────────────────────────────────────
echo "── starting stack ───────────────────────────────────────────"
compose up -d

# ── Wait for the app to serve ────────────────────────────────────────────────
# The container runs migrate + seed_agents + collectstatic before daphne binds,
# so first response can take a couple of minutes on a cold build.
# The web container must appear shortly after `up`. podman-compose exits 0 even
# when a service fails to start outright (e.g. an image name that will not
# resolve), so without this check a stack that never came up would burn the
# entire boot timeout before reporting anything.
CAN_PROBE=0
waited=0
while [ "$waited" -lt 30 ]; do
    if web_running; then
        CAN_PROBE=1
        break
    fi
    sleep 3
    waited=$((waited + 3))
done

if [ "$CAN_PROBE" -ne 1 ]; then
    echo "✗ web container never started — the stack failed to come up" >&2

    # Container STATE first, before the logs.
    #
    # `compose logs` is the wrong question when a container never ran: it
    # prints nothing, which reads as "the app said nothing" when the truth is
    # "the app was never executed". Two failures on this runner were diagnosed
    # blind for exactly that reason — both containers were created, neither
    # appeared in `podman ps`, and both log dumps were empty, which is the same
    # output a healthy-but-silent app would produce.
    #
    # ExitCode, Error and OOMKilled distinguish the cases that actually matter:
    # a non-zero exit with a message (the command failed), exit 0 with nothing
    # (it ran and returned), OOMKilled (the box is out of memory), or no
    # container at all (creation itself failed). The host's own view of the
    # published port is here too, since a rootless port conflict kills the
    # container without ever writing to its log.
    echo "── container state ──────────────────────────────────────────" >&2
    $RUNTIME ps -a \
        --filter "label=com.docker.compose.project=$PROJECT" \
        --format '  {{.Names}}  status={{.Status}}  image={{.Image}}' >&2 2>&1 || true
    for c in $($RUNTIME ps -aq --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null); do
        $RUNTIME inspect "$c" --format \
            '  {{.Name}}  state={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} err={{.State.Error}}' \
            >&2 2>&1 || true
    done

    echo "── host port ${CI_APP_PORT} ───────────────────────────────────────────" >&2
    if command -v ss >/dev/null 2>&1; then
        ss -tlnp 2>/dev/null | grep ":${CI_APP_PORT}" >&2 || echo "  (nothing listening — no conflict)" >&2
    else
        echo "  (ss unavailable)" >&2
    fi

    echo "── web logs ─────────────────────────────────────────────────" >&2
    compose logs --tail 60 web >&2 2>&1 || true
    echo "── db logs ──────────────────────────────────────────────────" >&2
    compose logs --tail 30 db >&2 2>&1 || true
    replay_web
    exit 1
fi

echo "── waiting for HTTP (timeout ${CI_BOOT_TIMEOUT}s) ────────────"
url="http://${CI_HEALTH_HOST}:${CI_APP_PORT}${CI_HEALTH_PATH}"
elapsed=0
code=""
healthy=0

while [ "$elapsed" -lt "$CI_BOOT_TIMEOUT" ]; do
    # NB: on a refused connection curl BOTH prints "000" and exits non-zero,
    # so `|| echo 000` would concatenate to "000000" — which passes an
    # `-lt 500` test as the integer zero and greens the build on a dead app.
    # Capture the output, then normalise separately.
    # core/settings.py sets SECURE_SSL_REDIRECT=True whenever DEBUG is False, so
    # a plain http:// probe gets a 301 from middleware before any view runs —
    # that only proves Django started, not that it can render. Settings also set
    # SECURE_PROXY_SSL_HEADER, so this header makes the request count as secure
    # and we get the real login page (200) instead.
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        -H 'X-Forwarded-Proto: https' "$url" 2>/dev/null) || code=""
    case "$code" in
        ''|*[!0-9]*) code="000" ;;
    esac

    # Require 2xx/3xx. 4xx is NOT success: Django answers 400 for
    # DisallowedHost and 404 for a broken URL conf, both of which mean a
    # misconfigured app that is nonetheless "responding". Found on prod, where
    # a 400 from ALLOWED_HOSTS would have passed a "< 500" check.
    if [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then
        echo "✓ app responded HTTP $code after ${elapsed}s"
        healthy=1
        break
    fi

    # If the web container already died, stop waiting out the full timeout.
    # Skipped when the runtime probe is unusable, so a probe failure can never
    # masquerade as an application failure.
    if [ "$CAN_PROBE" = "1" ] && ! web_running; then
        echo "✗ web container exited during startup" >&2
        code="000"
        break
    fi

    sleep 5
    elapsed=$((elapsed + 5))
    [ $((elapsed % 30)) -eq 0 ] && echo "  ... ${elapsed}s (last status: $code)"
done

if [ "$healthy" -ne 1 ]; then
    echo
    echo "✗ app did not serve a healthy response (last status: ${code:-none})" >&2
    echo "── web logs ─────────────────────────────────────────────────" >&2
    compose logs --tail 120 web >&2 2>&1 || true
    echo "── db logs ──────────────────────────────────────────────────" >&2
    compose logs --tail 30 db >&2 2>&1 || true
    replay_web
    exit 1
fi

# ── Extra assertion: Django's own system check inside the running container ──
echo "── django system check ──────────────────────────────────────"
if ! compose exec -T web python manage.py check; then
    echo "✗ manage.py check failed" >&2
    compose logs --tail 60 web >&2 2>&1 || true
    exit 1
fi

# ── Missing migrations ───────────────────────────────────────────────────────
# Enforced: verified clean on 9caf5f26 before this was made a hard gate. A model
# change committed without its migration fails the build here.
echo "── missing migrations ────────────────────────────────────────"
if ! compose exec -T web python manage.py makemigrations --check --dry-run; then
    echo "✗ models differ from committed migrations — run makemigrations" >&2
    exit 1
fi
echo "✓ no missing migrations"

# ── Test suite ───────────────────────────────────────────────────────────────
# Runs against the real Postgres already standing here, so no separate database
# or second boot is needed. Django creates and drops its own test_ci_db.
#
# DJANGO_DEBUG=True matters: settings turn on SECURE_SSL_REDIRECT whenever DEBUG
# is False, which makes the test client receive 301 for every request and fails
# 16 of these tests for a reason that has nothing to do with the code.
echo "── test suite ────────────────────────────────────────────────"
if ! compose exec -T web sh -c 'DJANGO_DEBUG=True python manage.py test --noinput -v1'; then
    echo "✗ test suite failed" >&2
    exit 1
fi

# ── Frontend integrity ───────────────────────────────────────────────────────
# Renders key pages and asserts every static asset they reference was actually
# collected. The HTTP check above cannot catch this: a page with every
# stylesheet 404ing still returns 200, which is what "the UI looks distorted"
# actually is.
echo "── frontend integrity ────────────────────────────────────────"
if ! compose exec -T web python /app/scripts/ci_frontend_check.py; then
    echo "✗ frontend integrity check failed" >&2
    exit 1
fi

echo
echo "✓ smoke test passed — the stack builds, boots, and serves"
