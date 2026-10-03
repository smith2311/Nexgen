import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from social_core.backends.oauth import BaseOAuth2

logger = logging.getLogger(__name__)


class UsernameOrEmailBackend(ModelBackend):
    """Authenticate local/dev users by username or email, case-insensitively."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        login = username or kwargs.get(UserModel.USERNAME_FIELD) or kwargs.get("email")

        if not login or password is None:
            return None

        login = str(login).strip()
        users = list(
            UserModel._default_manager.filter(
                Q(username__iexact=login) | Q(email__iexact=login)
            ).order_by("pk")
        )

        if not users:
            UserModel().set_password(password)
            return None

        for user in users:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user

        return None


class ZohoOAuth2(BaseOAuth2):
    """Custom Zoho OAuth2 backend - Fixed for unique users"""

    name = "zoho"
    ID_KEY = "ZUID"  # Fallback, but we use email primarily

    AUTHORIZATION_URL = "https://accounts.zoho.in/oauth/v2/auth"
    ACCESS_TOKEN_URL = "https://accounts.zoho.in/oauth/v2/token"
    ACCESS_TOKEN_METHOD = "POST"
    REDIRECT_STATE = False
    STATE_PARAMETER = True
    SCOPE_SEPARATOR = ","

    def get_redirect_uri(self, state=None):
        from django.conf import settings
        uri = getattr(settings, "ZOHO_REDIRECT_URI", None)
        if uri:
            return uri
        return super().get_redirect_uri(state=state)

    def user_data(self, access_token, *args, **kwargs):
        """Try multiple Zoho endpoints"""
        logger.info("🔍 Zoho user_data called")

        # Primary: OAuth user info
        try:
            data = self.get_json(
                "https://accounts.zoho.in/oauth/user/info", headers={"Authorization": f"Zoho-oauthtoken {access_token}"}
            )
            if data:
                logger.info(f"✅ OAuth endpoint: {data}")
                return data
        except Exception:
            pass

        # Fallback: Profile API
        try:
            data = self.get_json(
                "https://profile.zoho.in/api/v1/user/self/profile", headers={"Authorization": f"Bearer {access_token}"}
            )
            if data:
                logger.info(f"✅ Profile endpoint: {data}")
                return data
        except Exception:
            pass

        logger.error("❌ No user data from Zoho")
        return {}

    def get_user_id(self, details, response):
        """🚀 KEY FIX: Use EMAIL as unique ID (not ZUID)"""
        logger.info("🆔 get_user_id called")
        logger.info(f"Response: {response}")
        logger.info(response["profile"]["primary_email"])
        logger.info(response)

        # PRIORITY 1: Email (UNIQUE per user)
        email = (
            response.get("Email")
            or response.get("email")
            or details.get("email")
            or response["profile"]["primary_email"]
        )
        if email:
            logger.info(f"✅ UNIQUE ID: {email}")
            return email  # ← This creates SEPARATE users per email!

        # PRIORITY 2: ZUID (if email missing)
        zuid = response.get("ZUID") or response.get("user_id")
        if zuid:
            logger.warning(f"⚠️  Using ZUID (no email): {zuid}")
            return zuid

        # ERROR
        logger.error("❌ No ID found!")
        raise ValueError("No email or ZUID in Zoho response")

    def get_user_details(self, response):
        """Extract user details"""
        logger.info("👤 get_user_details")

        email = response.get("Email") or response.get("email") or response["profile"]["primary_email"] or ""

        # Clean username from email
        if email:
            username = email.split("@")[0].lower().replace(".", "_").replace("-", "_")
        else:
            username = "zoho_user"

        details = {
            "username": username,
            "email": email,
            "first_name": response.get("First_Name", "")
            or response.get("first_name", "")
            or response["profile"]["first_name"],
            "last_name": response.get("Last_Name", "")
            or response.get("last_name", "")
            or response["profile"]["last_name"],
        }

        logger.info(f"✅ Details: {details}")
        return details
