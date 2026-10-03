#!/bin/sh
# Startup for the CI smoke container. Mirrors the production `command:` in
# docker-compose.yml, but waits for the throwaway Postgres to accept
# connections first (nothing else orders the two containers).
#
# Lives in a file rather than inline in docker-compose.ci.yml so the shell
# quoting is readable; the Dockerfile's `COPY . /app` puts it in the image.
set -e

echo "→ waiting for postgres at ${DB_HOST}:${DB_PORT} ..."
python - <<'PY'
import os, socket, sys, time

host = os.environ.get("DB_HOST", "db")
port = int(os.environ.get("DB_PORT", "5432"))

# Every failure retries, including the name not resolving yet.
#
# connect_ex suppresses CONNECTION errors but not RESOLUTION ones, so an
# unresolvable host raises socket.gaierror straight out of the call. This loop
# used to let that escape: the container died on attempt 0 with a traceback and
# exit 1, about a second after starting, instead of waiting the two minutes it
# was written to wait. That is exactly the wrong way round -- `db` is a network
# alias served by the container DNS, which is the part MOST likely not to be
# answering in the first moments after the network is created, and riding that
# out is the entire purpose of this loop.
#
# It cost three rounds of blind diagnosis on a runner whose `podman logs`
# returns nothing (journald driver, no journal read access for that user), so
# the traceback that would have named it was invisible. See the log driver
# pinned in docker-compose.ci.yml.
last = "not attempted"
for attempt in range(60):
    sock = socket.socket()
    sock.settimeout(2)
    try:
        if sock.connect_ex((host, port)) == 0:
            print(f"  postgres reachable after {attempt * 2}s")
            sys.exit(0)
        last = "connection refused"
    except OSError as exc:
        # socket.gaierror subclasses OSError -- DNS not up yet, or no DNS.
        last = f"{type(exc).__name__}: {exc}"
    finally:
        sock.close()
    if attempt and attempt % 10 == 0:
        print(f"  still waiting after {attempt * 2}s ({last})", flush=True)
    time.sleep(2)

print(f"  postgres never came up at {host}:{port} -- last error: {last}", file=sys.stderr)
sys.exit(1)
PY

echo "→ migrate"
python manage.py heal_squashed_migrations
python manage.py migrate --noinput

echo "→ seed_agents"
python manage.py seed_agents

echo "→ collectstatic"
# --clear to match production. static_volume there outlives deploys, so without
# it collectstatic leaves stale assets behind and can skip files whose target is
# not older than the source. CI has no such volume, but running the same command
# means the flag is exercised here rather than only in production.
python manage.py collectstatic --noinput --clear

echo "→ starting daphne on :8020"
exec daphne -b 0.0.0.0 -p 8020 core.asgi:application
