"""Seed realistic demo transactions so the Reports screen has something to show.

Row 10.1's complaint was literally "Unable to check without data" — the Reports
view (``merilv/views.py:reports``) is a live aggregate over ``FinishedDelivery``
and ``OutboundScan``; on a freshly deployed or freshly demoed instance both
tables are empty and every tile on the page reads zero. There is nothing to
fix in the report itself — it needs rows.

This is demo/review data only, never real customer activity, so it is
deliberately NOT wired into ``transfer_merilv_data`` or the deploy pipeline:
run it by hand when someone needs the Reports screen to have something in it.

Idempotent by construction: every row this command writes carries a
recognisable marker (``delivery_no`` starting with ``DEMO-``, or the session
name "Demo Seed Activity"), so re-running with ``--clear`` removes exactly
what a previous run added and nothing else.

Usage (from inside the running container):
    python manage.py seed_merilv_demo_activity
    python manage.py seed_merilv_demo_activity --days 60
    python manage.py seed_merilv_demo_activity --clear
"""

from __future__ import annotations

import random

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.utils import timezone

from merilv.models import Entity, FinishedDelivery, OutboundScan, OutboundSession
from merilv.roles import PICKER_ROLE
from merilv.services.entities import SEED_ENTITIES

DEMO_DELIVERY_PREFIX = "DEMO-"
DEMO_SESSION_NAME = "Demo Seed Activity"

MATERIALS = ["MAT-1001", "MAT-1002", "MAT-1003", "MAT-1004", "MAT-1005"]
BATCHES = ["B24A", "B24B", "B24C"]
CUSTOMERS = [
    ("CUST-01", "Apollo Hospitals"),
    ("CUST-02", "Fortis Healthcare"),
    ("CUST-03", "Manipal Hospitals"),
    ("CUST-04", "Max Healthcare"),
]
DEMO_PICKER_USERNAMES = ["demo.picker1", "demo.picker2", "demo.picker3"]


class Command(BaseCommand):
    help = "Seed (or clear) demo deliveries/scans so the Reports screen has data to review."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="How many days of history to spread activity across.")
        parser.add_argument(
            "--per-day", type=int, default=6, help="Roughly how many finished deliveries per day."
        )
        parser.add_argument("--clear", action="store_true", help="Remove previously seeded demo rows and exit.")
        parser.add_argument("--seed", type=int, default=None, help="Random seed, for a reproducible demo dataset.")

    def handle(self, *args, **options):
        if options["clear"]:
            self._clear()
            return

        if options["seed"] is not None:
            random.seed(options["seed"])

        entities = list(Entity.objects.filter(code__in=[spec["code"] for spec in SEED_ENTITIES]))
        if not entities:
            self.stdout.write("No entities found — running seed_merilv first.")
            call_command("seed_merilv")
            entities = list(Entity.objects.filter(code__in=[spec["code"] for spec in SEED_ENTITIES]))
        if not entities:
            self.stderr.write(self.style.ERROR("seed_merilv did not produce any entities — aborting."))
            return

        pickers = self._demo_pickers()
        days = max(1, options["days"])
        per_day = max(1, options["per_day"])
        now = timezone.now()

        deliveries_created = 0
        scans_created = 0
        for entity in entities:
            session = OutboundSession.objects.filter(entity=entity, name=DEMO_SESSION_NAME).first()
            if session is None:
                session = OutboundSession.objects.create(
                    entity=entity,
                    name=DEMO_SESSION_NAME,
                    status=OutboundSession.STATUS_COMPLETED,
                    operator=random.choice(pickers),
                )

            # Next running number per batch, for the WHOLE session. A demo
            # session spans many deliveries and picks its batch at random, so a
            # counter reset per delivery would hand out BATCH+001 several times
            # over — and (session, serial_identity) is unique in the database.
            next_ordinal: dict[str, int] = {}
            for scan in OutboundScan.objects.filter(session=session).values_list("batch", "serial_only"):
                batch_code, existing = scan
                suffix = existing[len(batch_code):] if existing.startswith(batch_code) else ""
                if suffix.isdigit():
                    next_ordinal[batch_code] = max(next_ordinal.get(batch_code, 0), int(suffix))

            for day_offset in range(days):
                day_deliveries = max(1, per_day + random.randint(-2, 2))
                for i in range(day_deliveries):
                    when = now - timezone.timedelta(
                        days=day_offset, hours=random.randint(1, 20), minutes=random.randint(0, 59)
                    )
                    customer_code, customer_name = random.choice(CUSTOMERS)
                    picker = random.choice(pickers)
                    line_count = random.randint(1, 4)
                    scanned_count = line_count

                    # update_or_create, not create: the delivery number is
                    # derived from the entity and the loop counters, so a
                    # second run without --clear regenerates the same numbers
                    # in the same session. That used to insert duplicate rows;
                    # since FinishedDelivery gained its one-finish-per-delivery
                    # constraint it raises IntegrityError instead. Refreshing
                    # the existing row is what "idempotent by construction" in
                    # the module docstring was always meant to mean.
                    delivery, was_created = FinishedDelivery.objects.update_or_create(
                        session=session,
                        delivery_no=f"{DEMO_DELIVERY_PREFIX}{entity.code}-{day_offset:03d}-{i:02d}",
                        defaults={
                            "entity": entity,
                            "customer_code": customer_code,
                            "customer_name": customer_name,
                            "plant": entity.code,
                            "courier_partner": "Demo Logistics",
                            "courier_number": f"DL{random.randint(100000, 999999)}",
                            "status": FinishedDelivery.STATUS_COMPLETE,
                            "box_count": random.randint(1, 3),
                            "line_count": line_count,
                            "scanned_count": scanned_count,
                            "picker": picker,
                        },
                    )
                    FinishedDelivery.objects.filter(pk=delivery.pk).update(created_at=when)
                    deliveries_created += was_created

                    # Its scans were written on the first run. Adding another
                    # set now would inflate the scan count on every re-run,
                    # which is the same drift in a different table.
                    if not was_created:
                        continue

                    # Serials run per (session, batch) rather than being drawn
                    # at random. Two reasons: (session, serial_identity) is
                    # unique in the database now, so a random collision would
                    # abort the seed; and demo rows should look like real ones,
                    # which means BATCH + a three-digit running number.
                    for _ in range(scanned_count):
                        material = random.choice(MATERIALS)
                        batch = random.choice(BATCHES)
                        ordinal = next_ordinal.get(batch, 0) + 1
                        next_ordinal[batch] = ordinal
                        serial = f"{batch}{ordinal:03d}"
                        scan = OutboundScan.objects.create(
                            session=session,
                            serial_identity=serial,
                            # The stored serial is the whole assigned value,
                            # batch included — the same thing the scanner
                            # writes and the SAP sheet's Serial column carries.
                            serial_only=serial,
                            material=material,
                            batch=batch,
                            raw_scan=f"(240){material}(10){batch}(21){ordinal:03d}",
                            scanned_by=picker,
                        )
                        OutboundScan.objects.filter(pk=scan.pk).update(created_at=when)
                        scans_created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {deliveries_created} demo deliveries and {scans_created} demo scans "
            f"across {len(entities)} entit{'y' if len(entities) == 1 else 'ies'} and {days} day(s)."
        ))

    def _demo_pickers(self):
        User = get_user_model()
        group, _ = Group.objects.get_or_create(name=PICKER_ROLE)
        pickers = []
        for index, username in enumerate(DEMO_PICKER_USERNAMES, start=1):
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": f"Demo Picker {index}",
                    "email": f"{username}@example.invalid",
                },
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
            user.groups.add(group)
            pickers.append(user)
        return pickers

    def _clear(self):
        deliveries = FinishedDelivery.objects.filter(delivery_no__startswith=DEMO_DELIVERY_PREFIX)
        sessions = OutboundSession.objects.filter(name=DEMO_SESSION_NAME)
        scans = OutboundScan.objects.filter(session__in=sessions)

        deliveries_count = deliveries.count()
        scans_count = scans.count()
        deliveries.delete()
        scans.delete()
        sessions.delete()

        User = get_user_model()
        User.objects.filter(username__in=DEMO_PICKER_USERNAMES).delete()

        self.stdout.write(self.style.SUCCESS(
            f"Removed {deliveries_count} demo delivery/deliveries and {scans_count} demo scan(s)."
        ))
