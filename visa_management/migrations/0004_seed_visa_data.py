
from django.db import migrations

from visa_management.management.commands.seed_visa_data import (
    COMPANIES,
    COUNTRIES,
    CUSTOM_LETTER_BODIES,
    GROUPS,
    STANDARD_LETTER_BODY,
)


def seed_visa_data(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    VisaCompany = apps.get_model("visa_management", "VisaCompany")
    VisaCountry = apps.get_model("visa_management", "VisaCountry")
    VisaLetterTemplate = apps.get_model("visa_management", "VisaLetterTemplate")

    for name in GROUPS:
        Group.objects.get_or_create(name=name)

    for data in COMPANIES:
        VisaCompany.objects.get_or_create(
            code=data["code"],
            defaults={k: v for k, v in data.items() if k != "code"},
        )

    for data in COUNTRIES:
        VisaCountry.objects.get_or_create(
            code=data["code"],
            defaults={k: v for k, v in data.items() if k != "code"},
        )

    # Only (company, country) pairs backed by a real previously-sent letter get a
    # specific template; every other pair falls back to the default seeded below.
    for (company_code, country_code), variants in CUSTOM_LETTER_BODIES.items():
        company = VisaCompany.objects.filter(code=company_code).first()
        country = VisaCountry.objects.filter(code=country_code).first()
        if company is None or country is None:
            # A code in CUSTOM_LETTER_BODIES with no matching company/country row
            # is a typo in the payload, not a reason to abort the whole deploy.
            continue

        for variant in variants:
            VisaLetterTemplate.objects.get_or_create(
                company=company,
                country=country,
                entry_type="both",
                jurisdiction=variant.get("jurisdiction", ""),
                defaults={
                    "subject_line": variant["subject_line"],
                    "letter_body": variant["letter_body"],
                    "is_default": False,
                    "is_active": True,
                },
            )

    VisaLetterTemplate.objects.get_or_create(
        company=None,
        country=None,
        is_default=True,
        entry_type="both",
        defaults={
            "subject_line": "Request for Business Visa",
            "letter_body": STANDARD_LETTER_BODY,
            "is_active": True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("visa_management", "0003_visalettertemplate_jurisdiction_and_more"),
        # Groups are created here, so auth's tables must already exist.
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        # Reverse is a no-op on purpose: rolling this back must not delete
        # companies, countries or letters that live applications point at
        # (VisaApplication.company/country are on_delete=PROTECT).
        migrations.RunPython(seed_visa_data, migrations.RunPython.noop),
    ]
