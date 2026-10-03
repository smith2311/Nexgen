"""Frontend integrity check — runs INSIDE the running container.

Renders key pages as a logged-in superuser and asserts two things:

  1. the page renders without a server error, and
  2. every static asset it references was actually collected.

The second check is the point. A stale or half-populated staticfiles volume
serves HTML perfectly while every stylesheet and script 404s — which is exactly
what "the frontend looks distorted" means in practice. An HTTP health check
cannot see it: the page returns 200 either way.

Uses Django's test client with force_login, so there is no CSRF dance and no
dependency on a browser. STATICFILES_STORAGE is unhashed, so a URL of
/static/js/x.js maps straight to STATIC_ROOT/js/x.js.

Pages can be overridden with CI_FRONTEND_PAGES (comma-separated paths).
"""

import os
import re
import sys

import django

# Run from the project root regardless of how we were invoked. Called as
# `python /app/scripts/ci_frontend_check.py`, sys.path[0] is /app/scripts and
# `core` is not importable.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402

DEFAULT_PAGES = [
    "/",
    "/product_scanning/dashboard/",
    "/meritrack/",
    "/billing/",
    "/feedback/",
]

# href="..." / src="..." pointing at STATIC_URL, single or double quoted.
ASSET_RE = re.compile(
    r"""(?:href|src)\s*=\s*["'](?P<url>%s[^"'?#]+)""" % re.escape(settings.STATIC_URL),
    re.IGNORECASE,
)


def pages():
    raw = os.environ.get("CI_FRONTEND_PAGES", "")
    return [p.strip() for p in raw.split(",") if p.strip()] or DEFAULT_PAGES


def asset_path(url):
    """Map a /static/... URL onto its collected file under STATIC_ROOT."""
    rel = url[len(settings.STATIC_URL):].lstrip("/")
    return os.path.join(str(settings.STATIC_ROOT), rel)


def main():
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="ci-frontend-check",
        defaults={"email": "ci@example.invalid"},
    )
    user.is_superuser = True
    user.is_staff = True
    user.is_active = True
    user.save()

    # SERVER_NAME: the test client defaults to Host "testserver", which is only
    # added to ALLOWED_HOSTS by manage.py test's setup_test_environment(). In a
    # plain script Django validates it and answers 400 DisallowedHost, so every
    # page "fails" for a reason that has nothing to do with the frontend.
    client = Client(SERVER_NAME="localhost")
    client.force_login(user)

    server_errors = []   # pages that 5xx — always a failure
    soft = []            # 4xx: may just need data or permissions
    missing = {}         # asset url -> pages referencing it
    checked_assets = set()
    rendered = 0         # pages that actually returned 2xx/3xx

    for path in pages():
        try:
            resp = client.get(path, follow=False, secure=True)
        except Exception as exc:  # a view raising is as bad as a 500
            server_errors.append((path, "raised %s: %s" % (type(exc).__name__, exc)))
            continue

        code = resp.status_code
        if code >= 500:
            server_errors.append((path, "HTTP %d" % code))
            continue
        if 400 <= code < 500:
            soft.append((path, "HTTP %d" % code))
            continue

        rendered += 1
        print("  %-38s HTTP %d" % (path, code))

        if not resp.get("Content-Type", "").startswith("text/html"):
            continue

        html = resp.content.decode("utf-8", errors="replace")
        for m in ASSET_RE.finditer(html):
            url = m.group("url")
            if url in checked_assets:
                continue
            checked_assets.add(url)
            if not os.path.isfile(asset_path(url)):
                missing.setdefault(url, []).append(path)

    print()
    print("  pages checked : %d" % len(pages()))
    print("  assets checked: %d" % len(checked_assets))

    ok = True

    if server_errors:
        ok = False
        print("\n✗ pages returning a server error:", file=sys.stderr)
        for path, why in server_errors:
            print("    %s — %s" % (path, why), file=sys.stderr)

    if missing:
        ok = False
        print("\n✗ referenced static assets that were never collected:", file=sys.stderr)
        for url, refs in sorted(missing.items()):
            print("    %s   (referenced by %s)" % (url, ", ".join(refs)), file=sys.stderr)
        print("\n  These 404 in the browser, so the page renders unstyled or", file=sys.stderr)
        print("  without its scripts. Usually a missing file under static/, or a", file=sys.stderr)
        print("  stale staticfiles volume that collectstatic did not refresh.", file=sys.stderr)

    if soft:
        # Not fatal: an empty CI database legitimately makes some views 403/404.
        print("\n! pages returning 4xx (not failing the build):")
        for path, why in soft:
            print("    %s — %s" % (path, why))

    # A check that verified nothing must not report success. Without this, every
    # page returning 4xx (which is what a Host/ALLOWED_HOSTS mismatch produces)
    # collapsed the soft-warning path into a green tick while zero assets were
    # inspected.
    if rendered == 0:
        ok = False
        print("\n✗ no page rendered successfully — nothing was verified.", file=sys.stderr)
        print("  Every page 4xx'd, which usually means the request Host is not in", file=sys.stderr)
        print("  ALLOWED_HOSTS rather than anything wrong with the frontend.", file=sys.stderr)
    elif not checked_assets:
        ok = False
        print("\n✗ pages rendered but referenced no static assets at all.", file=sys.stderr)
        print("  Expected at least one CSS/JS reference; the HTML may be an error", file=sys.stderr)
        print("  page, or STATIC_URL (%s) does not match the templates." % settings.STATIC_URL,
              file=sys.stderr)

    if ok:
        print("\n✓ frontend intact — %d page(s) rendered, %d asset(s) all resolve"
              % (rendered, len(checked_assets)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
