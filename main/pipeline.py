"""
Custom social-auth pipeline steps.

Key step: associate_by_email
------------------------------
When an admin pre-creates a Django user (with groups/permissions) before that
person has ever logged in via Zoho SSO, the default pipeline would create a
second, blank account on first SSO login.

This step fires BEFORE create_user and looks up any existing User whose email
matches the Zoho-provided email.  If found, it returns that user so
create_user is skipped and the UserSocialAuth entry is linked to the
pre-existing account — preserving all group memberships assigned by admin.
"""

import logging

from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


def associate_by_email(backend, details, user=None, *args, **kwargs):
    """
    If a Django user with the same email already exists (pre-provisioned by
    admin), return it so the pipeline skips create_user and links the social
    auth entry to that account instead.

    Safe rules:
    - If a user is already attached to this pipeline run, do nothing.
    - If multiple accounts share the same email, pick the one that has NO
      existing social auth entry yet (i.e. the pre-provisioned one).
    - If all matches already have a social auth entry, let the pipeline
      continue normally (will hit create_user).
    """
    if user:
        # Already resolved (e.g. returning user), nothing to do.
        return None

    email = details.get("email", "").strip().lower()
    if not email:
        return None

    candidates = list(User.objects.filter(email__iexact=email))

    if not candidates:
        logger.info(f"[pipeline] No pre-existing user found for {email} — will create new account.")
        return None

    # Prefer a user that has no social auth entry yet (the pre-provisioned account)
    unlinked = [u for u in candidates if not u.social_auth.filter(provider=backend.name).exists()]

    if unlinked:
        chosen = unlinked[0]
        logger.info(
            f"[pipeline] Linking Zoho login to pre-existing user '{chosen.username}' "
            f"(id={chosen.id}, email={email})"
        )
        return {"user": chosen, "is_new": False}

    # All matching users are already linked — the one linked to this provider
    # will have been picked up by social_user earlier; nothing to do here.
    logger.info(f"[pipeline] All users for {email} already have a social auth entry.")
    return None
