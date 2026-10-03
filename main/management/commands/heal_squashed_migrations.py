"""Make a database that predates the migration squashes safe to migrate.

Both ``merilv`` and ``vendorlink`` were squashed to a single migration each,
and the replaced files were deleted. Django resolves a squash by looking up
every name in its ``replaces`` list:

* **all** of them recorded  -> the squash is marked applied. Nothing to do.
* **none** of them recorded -> a fresh database, the squash runs. Nothing to do.
* **some** of them recorded -> Django cannot decide, and with the replaced
  files gone it cannot fall back to running them either. ``migrate`` fails,
  and on a container whose start-up chain begins with ``migrate`` that means
  the app never boots.

Production is in exactly that middle state: it has merilv ``0001``-``0007``
but never received ``0009`` (the courier-code drop, numbered ``0008`` on the
development branch that wrote the squash).

Additionally, the ``manas`` and ``smith`` branches diverged their migration
numbering for merilv 0010-0013. A production database that ran ``manas``
migrations has different names at those slots than the code now expects. The
rename step below reconciles those records so ``migrate`` does not try to
replay schema changes the database already has.

Idempotent. Run it before ``migrate`` on every start; it exits immediately
when there is nothing to do, which is the normal case.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader

#: app -> (squashed migration name, the names it replaces)
SQUASHES = {
    "merilv": (
        "0001_merilv_initial",
        [
            "0001_initial",
            "0002_couriermaster_mode_of_transit_and_more",
            "0003_alter_entity_ai21_is_material_and_more",
            "0004_dispatched_serial_ledger",
            "0005_scanlog_delivery_no",
            "0006_close_fully_shipped_sessions",
            "0007_couriermaster_courier_code",
            "0009_remove_couriermaster_courier_code",
        ],
    ),
    "vendorlink": (
        "0001_vendorlink_initial",
        [
            "0001_squashed_0009_gptdocument_gptdocumentchunk_and_more",
            "0002_capability_master",
            "0003_seed_capabilities",
            "0004_remove_capability_booleans",
            "0005_award_no_longer_asks_the_vendor",
            "0006_vendor_state_and_gptmessage_source",
            "0007_seed_role_groups",
        ],
    ),
}

#: Migrations that exist in the production DB under ``manas`` names but are
#: now at different numbers on this branch. Each tuple is (old_name, new_name).
#: The rename makes Django see them as already-applied instead of trying to
#: replay the same schema change under a new filename.
#:
#: A plain UPDATE of the name is not enough: the new numbering slots a
#: migration the database never ran *before* each renamed one (0010 before
#: 0011, 0012 before 0013). Recording 0011 without 0010 is an inconsistent
#: history and ``migrate`` refuses to start. So the old row is dropped, the
#: missing parent is applied for real (both are permission seeds), and only
#: then is the renamed migration recorded with ``--fake``.
#:
#: manas 0011_permission_overrides has NO smith equivalent (MerilvPermission
#: model was not carried forward). Its DB record stays as a harmless phantom.
RENAMES = {
    "merilv": [
        ("0010_focus_belongs_to_the_operator", "0011_focus_belongs_to_the_operator"),
        ("0012_user_entity_access", "0013_user_entity_access"),
    ],
}

#: Schema the replaced migrations would have produced but a partially-migrated
#: database has not reached yet. Only ``merilv.0009`` is in this position: it
#: drops a column ``0007`` added, so a database stopped at ``0007`` still has
#: it. Faking the squash without this would leave the column behind forever.
CATCH_UP_SQL = [
    ("merilv_couriermaster", "courier_code",
     "ALTER TABLE merilv_couriermaster DROP COLUMN IF EXISTS courier_code"),
]


class Command(BaseCommand):
    help = "Reconcile django_migrations with the squashed migrations (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        healed = False

        with connection.cursor() as cursor:
            if "django_migrations" not in connection.introspection.table_names(cursor):
                self.stdout.write(
                    "  No django_migrations table — a brand new database. "
                    "Nothing to reconcile; migrate will create it."
                )
                return

            for app, (squashed, replaced) in SQUASHES.items():
                cursor.execute(
                    "SELECT name FROM django_migrations WHERE app = %s", [app]
                )
                recorded = {row[0] for row in cursor.fetchall()}

                present = [name for name in replaced if name in recorded]
                if not present and squashed not in recorded:
                    self.stdout.write(
                        f"  {app}: no history — a fresh database, migrate will run the squash."
                    )
                    continue
                if not present and squashed in recorded:
                    self.stdout.write(
                        f"  {app}: squash {squashed} ran on a fresh DB — "
                        "no replaced rows to reconcile."
                    )
                    continue
                if len(present) == len(replaced):
                    self.stdout.write(
                        f"  {app}: all {len(replaced)} replaced migrations recorded — "
                        "Django will mark the squash applied by itself."
                    )
                    continue

                missing = [name for name in replaced if name not in recorded]
                self.stdout.write(self.style.WARNING(
                    f"  {app}: PARTIAL — {len(present)}/{len(replaced)} recorded. "
                    f"Missing: {', '.join(missing)}"
                ))
                healed = True
                if dry:
                    continue

                with transaction.atomic():
                    self._catch_up_schema(cursor, app)
                    cursor.executemany(
                        "INSERT INTO django_migrations (app, name, applied) "
                        "VALUES (%s, %s, NOW())",
                        [(app, name) for name in missing],
                    )
                self.stdout.write(self.style.SUCCESS(
                    f"  {app}: healed — recorded {len(missing)} missing migration(s); "
                    f"{squashed} now reads as applied."
                ))

            renamed = []
            for app, pairs in RENAMES.items():
                cursor.execute(
                    "SELECT name FROM django_migrations WHERE app = %s", [app]
                )
                recorded = {row[0] for row in cursor.fetchall()}

                for old_name, new_name in pairs:
                    if old_name in recorded and new_name not in recorded:
                        self.stdout.write(self.style.WARNING(
                            f"  {app}: renaming {old_name} → {new_name}"
                        ))
                        renamed.append((app, old_name, new_name))
                    elif new_name in recorded:
                        self.stdout.write(
                            f"  {app}: {new_name} already recorded — no rename needed."
                        )

        if renamed:
            healed = True
            if not dry:
                self._apply_renames(renamed)

        if healed and not dry:
            self.stdout.write(self.style.SUCCESS("Migration history reconciled."))
        elif not healed:
            self.stdout.write("Nothing to reconcile.")

    def _apply_renames(self, renamed):
        """Re-record each renamed migration under its new name, in order.

        One transaction for the lot: if a parent fails to apply, the old rows
        come back and the next start retries from the same place, instead of
        leaving a database whose renamed migrations are recorded under neither
        name (``migrate`` would then replay their schema changes and crash).
        """
        with transaction.atomic():
            with connection.cursor() as cursor:
                for app, old_name, _ in renamed:
                    cursor.execute(
                        "DELETE FROM django_migrations WHERE app = %s AND name = %s",
                        [app, old_name],
                    )
            for app, _, new_name in renamed:
                loader = MigrationLoader(connection)
                for parent in loader.graph.node_map[(app, new_name)].parents:
                    if parent.key not in loader.applied_migrations:
                        self.stdout.write(f"    applying {parent.key[0]}.{parent.key[1]}")
                        call_command("migrate", parent.key[0], parent.key[1],
                                     interactive=False, verbosity=0)
                self.stdout.write(f"    recording {app}.{new_name} (fake)")
                call_command("migrate", app, new_name, fake=True,
                             interactive=False, verbosity=0)

    def _catch_up_schema(self, cursor, app):
        """Apply DDL from replaced migrations this database never received."""
        for table, column, sql in CATCH_UP_SQL:
            if not table.startswith(app + "_"):
                continue
            cursor.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                [table, column],
            )
            if cursor.fetchone():
                cursor.execute(sql)
                self.stdout.write(f"    dropped stale column {table}.{column}")
