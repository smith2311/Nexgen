import logging
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / ".env")

DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

#: The value the development default falls back to. Named so the check below
#: can recognise it even if someone copies it into a .env by mistake.
_INSECURE_SECRET_KEY = "unsafe-default-secret"

SECRET_KEY = os.environ.get("SECRET_KEY", _INSECURE_SECRET_KEY)

# Refuse to start a non-debug process without a real key.
#
# The fallback above is a convenience for development. In production it is an
# account-takeover vector that fails SILENTLY: a deployment missing the variable
# boots normally and serves traffic, signing sessions, password-reset tokens and
# CSRF tokens with a key published in this repository. Nothing in the logs would
# say so.
#
# `manage.py check --deploy` only warns when a key looks weak by its own
# heuristics, and it cannot know this particular string is public — so the guard
# has to live here. Failing at import is the right outcome: a misconfigured
# deploy should never reach the point of accepting a request.
if not DEBUG and SECRET_KEY == _INSECURE_SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "SECRET_KEY is unset, so it fell back to the development default. "
        "Refusing to start with DEBUG=False: sessions, password-reset tokens "
        "and CSRF tokens would be signed with a key that is public in this "
        "repository. Set SECRET_KEY in the environment "
        '(python -c "import secrets; print(secrets.token_urlsafe(64))").'
    )
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Trust HTTPS origins derived from ALLOWED_HOSTS so CSRF works behind the nginx SSL
# proxy. nginx terminates TLS on 8443, and Django matches the Origin header including
# its port — so trust both the bare and :8443 forms. localhost is included because the
# OAuth callback now returns to https://localhost:8443/.
_TRUSTED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip() not in ("", "*")
]
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in _TRUSTED_HOSTS]
CSRF_TRUSTED_ORIGINS += [f"https://{h}:8443" for h in _TRUSTED_HOSTS]
INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "social_django",
    "main",
    "channels",
    "corsheaders",
    "rs2t",
    "product_scanning",
    "meritrack",
    "shipment_notifications",
    "ai_sales_agent_dashboard",
    "logistics_performance",
    "vat_reconciliation",
    "ui",
    "warehouse_inspection",
    "material_rework",
    "vendorlink",
    "merilv",
    "billing_dashboard",
    "visa_management",
    "pr_tracker",
    "subsidiary_logistic",
    "grocery_stock",
    "reconciliation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "social_django.middleware.SocialAuthExceptionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "main.middleware.NoCacheAuthMiddleware",  # Cache busting for auth pages
    "main.middleware.ForcePasswordChangeMiddleware",  # Vendor first-login password change
    "logistics_performance.middleware.CurrentUserMiddleware",  # auto-stamp created_by/updated_by
]

# CORS (for WebSocket / frontend calls)
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# Session security
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
SESSION_COOKIE_SAMESITE = "Lax"

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # VendorLink's Geo Command Centre keeps its template beside its own
        # data, routes and assets in vendorlink/UI/ rather than in the shared
        # templates tree, so the whole feature is one folder. APP_DIRS only
        # looks in <app>/templates/, which that path is not, so it is named
        # here explicitly.
        "DIRS": [BASE_DIR / "templates", BASE_DIR / "vendorlink" / "UI" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "social_django.context_processors.backends",
                "social_django.context_processors.login_redirect",
                "core.context_processors.app_settings",
                "core.context_processors.nav_access",
                "core.context_processors.lp_divisions",
                "core.context_processors.lp_masters",
                "core.context_processors.feedback_fab",
                "ui.context_processors.ui_assets",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "django_db"),
        "USER": os.environ.get("POSTGRES_USER", "django_user"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "django_password"),
        "HOST": os.environ.get("DB_HOST", "host.containers.internal"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
    },
}

# Add the AI Sales Agent dedicated MySQL DB only when fully configured
_ai_mysql_host = os.environ.get("AI_AGENT_MYSQL_HOST")
if _ai_mysql_host:
    DATABASES["ai_sales_agent_dashboard"] = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("AI_AGENT_MYSQL_DB"),
        "USER": os.environ.get("AI_AGENT_MYSQL_USER"),
        "PASSWORD": os.environ.get("AI_AGENT_MYSQL_PASSWORD"),
        "HOST": _ai_mysql_host,
        "PORT": os.environ.get("AI_AGENT_MYSQL_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4"},
        "CONN_MAX_AGE": 600,
    }

DATABASE_ROUTERS = ["core.db_routers.AppDatabaseRouter"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "static",  # Your source static folder
    # Same reason as the template DIRS above: the Geo Command Centre's CSS, JS
    # and border data live with the rest of that feature, and the app-
    # directories finder cannot see a static/ folder nested inside a package.
    BASE_DIR / "vendorlink" / "UI" / "static",
]
# Use basic static file storage instead of compressed manifest (easier for Docker)
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Authentication URLs
LOGIN_URL = "/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"
SOCIAL_AUTH_LOGIN_ERROR_URL = "/"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# ✅ Use ONLY social-auth's built-in Zoho backend (NO custom backend.py or pipeline.py)
AUTHENTICATION_BACKENDS = [
    "main.backends.ZohoOAuth2",  # Custom Zoho backend
    "main.backends.UsernameOrEmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

SOCIAL_AUTH_PIPELINE = (
    "social_core.pipeline.social_auth.social_details",
    "social_core.pipeline.social_auth.social_uid",
    "social_core.pipeline.social_auth.auth_allowed",
    "social_core.pipeline.social_auth.social_user",
    # If admin pre-created a user with this email, link to that account
    # instead of creating a duplicate one (preserves group memberships).
    "main.pipeline.associate_by_email",
    "social_core.pipeline.user.get_username",
    "social_core.pipeline.user.create_user",
    "social_core.pipeline.social_auth.associate_user",
    "social_core.pipeline.social_auth.load_extra_data",
    "social_core.pipeline.user.user_details",
)

# ── VendorLink currency conversion ──────────────────────────────────────────
# Vendors quote in their own currency; Meril reads every VendorLink screen in
# INR. See vendorlink/fx.py, which stores each fetched rate with its timestamp
# so a converted figure can always be shown with the rate behind it.
#
# The provider is named separately because an API key does not identify its
# service — the endpoint path and the response shape differ between all of
# them, and guessing wrong yields plausible-looking wrong rates.
#
# Blank key is fully supported: the default provider's open endpoint needs no
# key, so conversion works before anyone signs up. If no rate can be had at
# all, screens show the vendor's own currency rather than a guessed number.
CURRENCY_CONVERSION_API_KEY = os.getenv("CURRENCY_CONVERSION_API_KEY", "").strip()
CURRENCY_CONVERSION_PROVIDER = os.getenv("CURRENCY_CONVERSION_PROVIDER", "open-er-api").strip()

# File uploads
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800

# Django's default (1000) is fine for an ordinary form but breaks the built-in
# admin's User change page: with ~1000 Permission rows across this many apps,
# the user_permissions filter_horizontal widget alone can submit close to that
# many POST fields, and it only takes a few more (groups, csrf, the rest of
# the form) to tip over the limit — which surfaces as a bare 400
# TooManyFieldsSent with no indication of why. Sized with real headroom rather
# than nudged just past today's Permission count, since every new app/model
# adds ~4 more permissions automatically.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000

# ============================================================================
# ZOHO OAUTH2 CONFIGURATION (Using built-in social-auth backend)
# ============================================================================

# Zoho OAuth credentials
SOCIAL_AUTH_ZOHO_KEY = os.environ.get("ZOHO_CLIENT_ID")
SOCIAL_AUTH_ZOHO_SECRET = os.environ.get("ZOHO_CLIENT_SECRET")

# ✅ OAuth scopes - use ZohoCRM scopes that provide user info
SOCIAL_AUTH_ZOHO_SCOPE = [
    # 'profile.userphoto.READ',
    "profile.userinfo.READ",
]
# ✅ CRITICAL: Handle duplicate accounts
SOCIAL_AUTH_ALWAYS_UPDATE_USER = True
SOCIAL_AUTH_USERNAME_IS_FULL_EMAIL = False
SOCIAL_AUTH_PROTECTED_USER_FIELDS = ["email"]

# ✅ Don't raise errors
SOCIAL_AUTH_RAISE_EXCEPTIONS = False

# ✅ Logging
#
# The directory has to exist before dictConfig runs: logging.FileHandler opens
# its file eagerly, so a missing logs/ is not a degraded log — it raises
# ValueError("Unable to configure handler 'vat_file'") out of django.setup()
# and the process never starts. That is exactly what .dockerignore's `logs/`
# rule causes, since COPY then never creates /app/logs in the image; the smoke
# job's container failed to boot on it. Creating it here rather than relying on
# a tracked .gitkeep or a Dockerfile mkdir keeps it true however the app is
# started.
(BASE_DIR / "logs").mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "vat": {"format": "%(asctime)s %(levelname)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
        "vat_file": {
            "class": "logging.FileHandler",
            "filename": str(BASE_DIR / "logs" / "vat_pipeline.log"),
            "encoding": "utf-8",
            "formatter": "vat",
        },
    },
    "loggers": {
        "main.backends": {
            "handlers": ["console"],
            "level": "INFO",
        },
        # VAT pipeline → console (container logs) + logs/vat_pipeline.log (host)
        "vat_reconciliation": {
            "handlers": ["console", "vat_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


# ✅ Domain whitelist for email verification (ZOHO backend setting)
SOCIAL_AUTH_ZOHO_WHITELISTED_DOMAINS = [
    "merillife.com",
    "meril.com",
    "meril.in",
    "merillife.in",
]

# ✅ Require email from Zoho
SOCIAL_AUTH_ZOHO_REQUIRE_EMAIL = True

# ✅ Auto-create users with these fields
SOCIAL_AUTH_USER_FIELDS = ["username", "email", "first_name", "last_name"]

SOCIAL_AUTH_USER_DATA_URL = "https://accounts.zoho.in/oauth/user/info"

ZOHO_REDIRECT_URI = os.environ.get("ZOHO_REDIRECT_URI", "")

# Pin the OAuth callback to the configured URI. Without this, social-auth derives
# the redirect_uri from whatever host the browser used (so hitting the app by LAN
# IP sent Zoho back to that IP). Zoho validates this value server-side, so the
# exact same URL must also be registered in the Zoho API console.
if ZOHO_REDIRECT_URI:
    SOCIAL_AUTH_ZOHO_REDIRECT_URI = ZOHO_REDIRECT_URI

CLOUDFLARE_TURNSTILE_SITE_KEY = os.environ.get("CLOUDFLARE_TURNSTILE_SITE_KEY", "")
CLOUDFLARE_TURNSTILE_SECRET_KEY = os.environ.get("CLOUDFLARE_TURNSTILE_SECRET_KEY", "")

# Security (Production)
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Django 4.2+ defaults this to "same-origin", but browsers reject it over plain
# HTTP on non-localhost origins — which is the development setup, hence the
# suppression.
#
# Gated on DEBUG rather than set unconditionally, as it was: production runs
# behind TLS, where the objection does not apply and the header is free. COOP is
# what stops a cross-origin opener keeping a handle on the window, so dropping it
# in production was giving up a real protection to solve a development problem.
if DEBUG:
    SECURE_CROSS_ORIGIN_OPENER_POLICY = None

# Required for django.template.context_processors.debug to expose `debug=True` in templates
if DEBUG:
    INTERNAL_IPS = ["127.0.0.1", "::1", "localhost"]

# Matomo analytics — an explicit switch rather than a DEBUG derivative.
# templates/_matomo.html used to gate on `{% if not debug %}`, but Django only
# sets that template variable when DEBUG is on AND the request IP is in
# INTERNAL_IPS. Behind nginx every request arrives from 127.0.0.1, which is in
# the list above, so on any DEBUG deployment the guard matched real browser
# traffic and suppressed tracking site-wide. Gating on DEBUG directly has the
# same problem wherever DEBUG is left on. Set MATOMO_ENABLED=False in the
# environment to keep a local or throwaway instance out of the analytics.
MATOMO_ENABLED = os.environ.get("MATOMO_ENABLED", "True") == "True"

# ...and the host must be a production entry point as well, because the switch
# above defaults to on and so does nothing until someone remembers to set it —
# every dev container and fresh clone reported into the live site by default.
# The host is the one thing a non-production instance cannot accidentally share
# with production, which DEBUG very much can: this deployment runs
# DJANGO_DEBUG=True in production, so DEBUG is True everywhere and says nothing
# about which environment is which.
#
# Production is nexgen.e-meril.in and nothing else. Everything else that serves
# this codebase — gpt.e-meril.in, the 10.10.30.160 LAN address, localhost — is a
# non-production instance and is now silently untracked, which is what stopped
# their traffic from landing in the live Matomo site. Override with
# MATOMO_HOSTS=a,b when the deployment moves or gains a domain.
MATOMO_HOSTS = {
    host.strip().lower()
    for host in os.environ.get("MATOMO_HOSTS", "nexgen.e-meril.in").split(",")
    if host.strip()
}

# Optional Zoho diagnostics. Keep this out of normal settings import so Windows
# services and CI shells never fail before Django can even start.
if DEBUG and os.environ.get("ZOHO_DEBUG_CONFIG", "False") == "True":
    logging.getLogger(__name__).info(
        "ZOHO OAUTH2 CONFIGURATION | backend=%s | client_id=%s | "
        "client_secret=%s | scopes=%s | whitelisted_domains=%s | "
        "require_email=%s | callback=%s | authorization=%s | token=%s | user_data=%s",
        "social_core.backends.zoho.ZohoOAuth2",
        bool(SOCIAL_AUTH_ZOHO_KEY),
        bool(SOCIAL_AUTH_ZOHO_SECRET),
        SOCIAL_AUTH_ZOHO_SCOPE,
        SOCIAL_AUTH_ZOHO_WHITELISTED_DOMAINS,
        SOCIAL_AUTH_ZOHO_REQUIRE_EMAIL,
        ZOHO_REDIRECT_URI or "(derived from request host)",
        "https://accounts.zoho.in/oauth/v2/auth",
        "https://accounts.zoho.in/oauth/v2/token",
        SOCIAL_AUTH_USER_DATA_URL,
    )


ASGI_APPLICATION = "core.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

AUDIO_QUEUE_SIZE = int(os.environ.get("AUDIO_QUEUE_SIZE", 500))
MAX_AUDIO_WORKERS = int(os.environ.get("MAX_AUDIO_WORKERS", 4))

# Optional. The rs2t translator no longer uses it (local Whisper + self-hosted
# Ollama); the GS1 parsers in meritrack/product_scanning treat it as an
# optional enrichment step and degrade gracefully without it.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "3"))

# ─── rs2t translator: self-hosted Ollama (translation) ─────────────────────
# Ollama runs on this host; django_app uses network_mode: host, so localhost
# reaches it directly. No API key of any kind is involved.
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_TIMEOUT    = float(os.getenv("OLLAMA_TIMEOUT", "20"))
# Keep the model resident between chunks so the fallback path never pays a
# cold model load (~70s for qwen3:8b).
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
# Post-session summary. Runs once when a session is saved, so it can afford a
# longer budget than the per-chunk translation path.
OLLAMA_SUMMARY_TIMEOUT    = float(os.getenv("OLLAMA_SUMMARY_TIMEOUT", "45"))
OLLAMA_SUMMARY_MAX_TOKENS = int(os.getenv("OLLAMA_SUMMARY_MAX_TOKENS", "400"))
OLLAMA_SUMMARY_MAX_LINES  = int(os.getenv("OLLAMA_SUMMARY_MAX_LINES", "60"))
# Warmup runs once per connection and must outlast a cold model load (~70s for
# qwen3:8b), otherwise the opening sentences of a session go out untranslated.
OLLAMA_WARMUP_TIMEOUT     = float(os.getenv("OLLAMA_WARMUP_TIMEOUT", "120"))

# ─── rs2t translator: transcript unit buffering ────────────────────────────
# Audio arrives in fixed windows, but translating each window alone yields
# disconnected, over-literal output. Windows are buffered into a "unit" — a
# complete enough thought — before being translated with the previous unit as
# context.
RS2T_MIN_UNIT_WORDS = int(os.getenv("RS2T_MIN_UNIT_WORDS", "4"))
RS2T_MAX_UNIT_WORDS = int(os.getenv("RS2T_MAX_UNIT_WORDS", "40"))
# Flush a trailing fragment this long after the last window, so the end of a
# sentence is never stranded in the buffer when the speaker stops.
RS2T_UNIT_IDLE_FLUSH_SECONDS = float(os.getenv("RS2T_UNIT_IDLE_FLUSH_SECONDS", "1.2"))
# How long a window waits for its turn to be emitted before assuming the window
# ahead of it died. Must exceed the slowest single transcription.
RS2T_ORDER_WAIT_SECONDS = float(os.getenv("RS2T_ORDER_WAIT_SECONDS", "25"))
# Glossary terms injected into the Whisper and translation prompts. Whisper's
# initial_prompt shares the model's 224-token window, so this is capped.
RS2T_GLOSSARY_PROMPT_TERMS = int(os.getenv("RS2T_GLOSSARY_PROMPT_TERMS", "40"))
# Require a logged-in user on the /ws/rs2t/ socket. The page is @login_required
# but the socket was not, so it could be driven anonymously. Sessions are
# private per user, so this should stay on.
RS2T_REQUIRE_AUTH = os.getenv("RS2T_REQUIRE_AUTH", "True") == "True"

# ─── rs2t translator: local Whisper (speech-to-text) ───────────────────────
# faster-whisper / CTranslate2, running in-process. Weights are cached under
# WHISPER_MODEL_DIR (bind-mounted, so they survive image rebuilds) and are
# downloaded on first use.
WHISPER_MODEL_SIZE   = os.getenv("WHISPER_MODEL_SIZE", "small")
WHISPER_DEVICE       = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_MODEL_DIR    = os.getenv("WHISPER_MODEL_DIR", str(BASE_DIR / "media" / "whisper_models"))
WHISPER_CPU_THREADS  = int(os.getenv("WHISPER_CPU_THREADS", "16"))
WHISPER_BEAM_SIZE    = int(os.getenv("WHISPER_BEAM_SIZE", "1"))  # 1 = greedy, lowest latency

# ─── VAT Reconciliation module ─────────────────────────────────────────────
# OCR provider: "ai_extractor" (AI Data Extractor V3 — OCR + translation +
# extraction in one call) or "ocrspace" (OCR.space + translation webhook).
VAT_OCR_PROVIDER       = os.getenv("VAT_OCR_PROVIDER", "ai_extractor")
# Credentials come from .env only — no fallback. A default here is a secret
# committed to the repository, readable by anyone with clone access and
# preserved in history forever.
VAT_OCR_SPACE_API_KEY  = os.getenv("VAT_OCR_SPACE_API_KEY", "")
VAT_OCR_SPACE_URL      = os.getenv("VAT_OCR_SPACE_URL", "https://api.ocr.space/parse/image")
VAT_OCR_LANGUAGE       = os.getenv("VAT_OCR_LANGUAGE", "eng")   # default OCR language

# AI Data Extractor API (JWT-secured; V3 endpoint does OCR + translation + extraction).
VAT_AI_EXTRACTOR_URL       = os.getenv("VAT_AI_EXTRACTOR_URL", "http://10.10.30.160:5001")
VAT_AI_EXTRACTOR_USERNAME  = os.getenv("VAT_AI_EXTRACTOR_USERNAME", "")
VAT_AI_EXTRACTOR_PASSWORD  = os.getenv("VAT_AI_EXTRACTOR_PASSWORD", "")

# Translation via the internal webhook
VAT_TRANSLATE_PROVIDER     = os.getenv("VAT_TRANSLATE_PROVIDER", "webhook")
VAT_TRANSLATE_WEBHOOK_URL  = os.getenv("VAT_TRANSLATE_WEBHOOK_URL", "https://ai.e-meril.in/webhook/translate")

# Field extraction strategy (parse English text back into structured fields)
VAT_EXTRACT_PROVIDER   = os.getenv("VAT_EXTRACT_PROVIDER", "rules")

# FX rate source for currency normalization (no key required).
VAT_FX_PROVIDER        = os.getenv("VAT_FX_PROVIDER", "open.er-api.com")

# ─── Email (SMTP) ──────────────────────────────────────────────────────────
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.zoho.in")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "False") == "True"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", os.getenv("WAREHOUSE_INSPECTION_EMAIL_TIMEOUT", "20")))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "noreply@merillife.com")
# When DEBUG is True, per-checkpoint internal-department CC emails (normally
# WarehouseInspectionResponse.internal_department_email) are redirected here
# instead, so dev testing doesn't email real department owners.
WAREHOUSE_INSPECTION_DEBUG_INTERNAL_EMAILS = [
    email.strip()
    for email in os.getenv(
        "WAREHOUSE_INSPECTION_DEBUG_INTERNAL_EMAILS",
        "smith.kansara@merillife.com",
    ).replace(";", ",").split(",")
    if email.strip()
]
# Always CC'd on every warehouse inspection email (submission, summary,
# reminder, daily report), in addition to whatever the real/debug recipients
# already are - for watching everything while testing, independent of DEBUG.
WAREHOUSE_INSPECTION_TESTING_CC = [
    email.strip()
    for email in os.getenv("WAREHOUSE_INSPECTION_TESTING_CC", "").replace(";", ",").split(",")
    if email.strip()
]
WAREHOUSE_INSPECTION_DEBUG_FLOOR_EMAILS = os.getenv(
    "WAREHOUSE_INSPECTION_DEBUG_FLOOR_EMAILS",
    (
        "Ground Floor=vipul.ramani@merillife.com;"
        "1st Floor=himanshu.singh@merillife.com;"
        "2nd Floor=kailash.koli@merillife.com;"
        "3rd Floor=manas.jain@merillife.com"
    ),
)
# Shared secret for the /warehouse-inspection/api/run-daily-emails/ endpoint
# that n8n (or any external scheduler) posts to at 09:00. Blank disables the
# endpoint entirely - it returns 403 rather than running with no auth.
WAREHOUSE_INSPECTION_RUN_SECRET = os.getenv("WAREHOUSE_INSPECTION_RUN_SECRET", "")

WAREHOUSE_INSPECTION_SEND_RESPONSIBLE_EMAILS = os.getenv(
    "WAREHOUSE_INSPECTION_SEND_RESPONSIBLE_EMAILS",
    "True",
).lower() in ("true", "1", "yes", "on")

# Public base URL used in email links
SITE_URL = os.getenv("SITE_URL", "http://nexgen.e-meril.in")
SHIPMENT_NOTIFICATION_BASE_URL = os.getenv("SHIPMENT_NOTIFICATION_BASE_URL", SITE_URL)
SHIPMENT_DASHBOARD_TOKEN_TTL_DAYS = int(os.getenv("SHIPMENT_DASHBOARD_TOKEN_TTL_DAYS", "30"))
SHIPMENT_WEBHOOK_SECRET = os.getenv("SHIPMENT_WEBHOOK_SECRET", "")
SHIPMENT_POLL_SECRET = os.getenv("SHIPMENT_POLL_SECRET", SHIPMENT_WEBHOOK_SECRET)
ENGATI_API_ENDPOINT = os.getenv("ENGATI_API_ENDPOINT", "")
ENGATI_BASIC_TOKEN = os.getenv("ENGATI_BASIC_TOKEN", "")
SHIPMENT_TRACKER_API_URL = os.getenv("SHIPMENT_TRACKER_API_URL", "")
SHIPMENT_TRACKER_API_KEY = os.getenv("SHIPMENT_TRACKER_API_KEY", "")
SHIPMENT_TRACKER_USERNAME = os.getenv("SHIPMENT_TRACKER_USERNAME", "")
SHIPMENT_TRACKER_PASSWORD = os.getenv("SHIPMENT_TRACKER_PASSWORD", "")
SHIPMENT_TRACKER_DAYS_BACK = int(os.getenv("SHIPMENT_TRACKER_DAYS_BACK", "1"))

# ─── Meril-V outbound scanner diagnostics ───────────────────────────────────
# Logs why each outbound scan matched or was rejected. A rejection reads as
# "the scanner is broken" from the floor, and wrong-batch, unresolved-material
# and exhausted-line are indistinguishable from the outside. Off by default —
# it is per-scan and noisy — and read once in
# merilv.services.entities.get_outbound_settings, which is the module allowed
# to touch Django from the engine layer.
DEBUG_SCANNER_MATCH = os.getenv("DEBUG_SCANNER_MATCH", "False") == "True"

# VendorLink bid evaluation reads OPENAI_API_KEY directly from the environment.

# ─── VendorLink late-joiner bid window ──────────────────────────────────────
# How far back, in hours, a newly onboarded vendor may reach for requests that
# were raised before they existed on the platform.
#
# The case this exists for is same-day: an RFI goes out in the morning, the
# vendor is registered that afternoon, and until now that request was invisible
# to them forever — the requester had to raise it again. 24 hours covers the
# working day without quietly opening months of history to someone who joined
# today.
#
# A request the vendor was explicitly emailed is always reachable regardless of
# this value; the window only governs requests they were never sent. 0 disables
# the reach-back entirely (they see only what was raised after they joined, plus
# what they were sent). See vendorlink/vendoraccess.py.
VENDORLINK_LATE_BID_GRACE_HOURS = int(os.getenv("VENDORLINK_LATE_BID_GRACE_HOURS", "24"))

# ─── VendorLink AI Bid Evaluator (OpenAI) ───────────────────────────────────
# Uses OPENAI_API_KEY (defined above). Model must support structured outputs
# (json_schema response_format) — gpt-4o-2024-08-06 or newer.
#
# From manas's branch. Nothing on this side reads it yet — the evaluator here
# takes its model from its own call site — but it is kept rather than dropped
# so the setting is in place when that code arrives, and so the two branches
# do not disagree about whether it exists.
OPENAI_VENDORLINK_MODEL = os.getenv("OPENAI_VENDORLINK_MODEL", "gpt-4o-2024-08-06")

# Test mode: Only send notifications to test phone numbers
SHIPMENT_NOTIFICATIONS_TEST_MODE = os.getenv("SHIPMENT_NOTIFICATIONS_TEST_MODE", "false").lower() in (
    "true",
    "1",
    "yes",
    "on",
)

# Test phone numbers
TEST_PHONE_1 = os.getenv("TEST_PHONE_1", "")
TEST_PHONE_2 = os.getenv("TEST_PHONE_2", "")
TEST_PHONE_3 = os.getenv("TEST_PHONE_3", "")
TEST_PHONE_4 = os.getenv("TEST_PHONE_4", "")

# Test / override notification recipient (set in .env for dev)
TEST_NOTIFICATION_EMAIL = os.getenv("TEST_NOTIFICATION_EMAIL", "manas.jain@merillife.com")

# ─── Visa Management ───────────────────────────────────────────────────────
# Visa mail goes out under the SCM digital support mailbox rather than the
# site-wide noreply address, so replies reach a monitored inbox. `or` (not a
# getenv default) so an env var present-but-empty still falls back.
VISA_FROM_EMAIL = os.getenv("VISA_FROM_EMAIL") or "scm.digitalsupport@merillife.com"

VISA_DEPT_EMAILS = [
    e.strip()
    for e in os.getenv("VISA_DEPT_EMAILS", "").replace(";", ",").split(",")
    if e.strip()
]

# ─── Celery (background tasks) ─────────────────────────────────────────────
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
# Run tasks inline (no worker/broker) when True — handy for local/dev.
CELERY_TASK_ALWAYS_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "False") == "True"

# Billing dump upload: process in a Celery worker instead of the request
# thread. Off by default so uploads keep working where no worker/redis runs
# (falls back to synchronous, still with the reader/batch speedups).
BILLING_ASYNC_UPLOAD = os.getenv("BILLING_ASYNC_UPLOAD", "False") == "True"
