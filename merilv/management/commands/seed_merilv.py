"""Seed the Meril-V business units and carton profiles.

Idempotent, so it is safe to run on every deploy. Existing records are left
alone — once operations staff have tuned an entity's parsing rules we must not
stamp over them.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from merilv.models import BoxProfile, Entity
from merilv.services.entities import SEED_BOXES, SEED_ENTITIES


class Command(BaseCommand):
    help = "Create the default Meril-V entities and carton profiles if they do not exist."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Overwrite existing entity and carton configuration with the defaults.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        reset = options["reset"]
        created_entities = updated_entities = 0

        for spec in SEED_ENTITIES:
            code = spec["code"]
            if reset:
                Entity.objects.update_or_create(code=code, defaults=spec)
                updated_entities += 1
            else:
                _, created = Entity.objects.get_or_create(code=code, defaults=spec)
                created_entities += created

        created_boxes = 0
        for spec in SEED_BOXES:
            code = spec["code"]
            if reset:
                BoxProfile.objects.update_or_create(code=code, defaults=spec)
            else:
                _, created = BoxProfile.objects.get_or_create(code=code, defaults=spec)
                created_boxes += created

        if reset:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reset {updated_entities} entities and {len(SEED_BOXES)} carton profiles."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created {created_entities} entities and {created_boxes} carton profiles "
                    "(existing records untouched)."
                )
            )
