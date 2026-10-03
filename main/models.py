from django.contrib.auth.models import User
from django.db import models


class UserProfile(models.Model):
    THEME_CHOICES = [
        ("light", "Light"),
        ("dark", "Dark"),
        ("auto", "System Default"),
    ]
    LANGUAGE_CHOICES = [
        ("en", "English"),
        ("hi", "Hindi"),
    ]
    TIMEZONE_CHOICES = [
        ("Asia/Kolkata", "India Standard Time (IST, UTC+5:30)"),
        ("Asia/Dubai", "Gulf Standard Time (GST, UTC+4)"),
        ("Asia/Singapore", "Singapore Time (SGT, UTC+8)"),
        ("Asia/Tokyo", "Japan Standard Time (JST, UTC+9)"),
        ("Asia/Shanghai", "China Standard Time (CST, UTC+8)"),
        ("Asia/Riyadh", "Arabia Standard Time (AST, UTC+3)"),
        ("Europe/London", "GMT / British Time (UTC±0)"),
        ("Europe/Paris", "Central European Time (CET, UTC+1)"),
        ("Europe/Berlin", "Central European Time (CET, UTC+1)"),
        ("Europe/Moscow", "Moscow Standard Time (MSK, UTC+3)"),
        ("America/New_York", "Eastern Time (ET, UTC-5/-4)"),
        ("America/Chicago", "Central Time (CT, UTC-6/-5)"),
        ("America/Denver", "Mountain Time (MT, UTC-7/-6)"),
        ("America/Los_Angeles", "Pacific Time (PT, UTC-8/-7)"),
        ("America/Sao_Paulo", "Brasília Time (BRT, UTC-3)"),
        ("Australia/Sydney", "Australian Eastern Time (AEST, UTC+10/+11)"),
        ("UTC", "Coordinated Universal Time (UTC±0)"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    # Personal info
    phone = models.CharField(max_length=20, blank=True)
    department = models.CharField(max_length=100, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=100, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)

    # Preferences
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default="light")
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default="en")
    timezone = models.CharField(max_length=50, choices=TIMEZONE_CHOICES, default="Asia/Kolkata")

    # Notifications
    notif_email = models.BooleanField(default=True)
    notif_browser = models.BooleanField(default=True)
    notif_shipment = models.BooleanField(default=True)
    notif_orders = models.BooleanField(default=True)
    notif_inventory = models.BooleanField(default=False)

    # Security
    two_factor_enabled = models.BooleanField(default=False)
    # Set when VendorLink provisions a vendor account, so the vendor is asked to
    # replace the generated password on first sign-in. Read and cleared by
    # replace the generated password on first sign-in. Read and cleared by
    # VendorLink's own views and checked globally by ForcePasswordChangeMiddleware.
    force_password_change = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} Profile"

    def get_avatar_initials(self):
        name = self.user.get_full_name()
        if name:
            parts = name.split()
            if len(parts) >= 2:
                return f"{parts[0][0]}{parts[-1][0]}".upper()
            return name[0].upper()
        return self.user.username[0].upper()
