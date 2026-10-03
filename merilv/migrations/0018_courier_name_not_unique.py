"""Drop the out-of-band unique index on lower(courier_name).

``merilv_courier_unique_name_ci`` exists in the deployed database and in no
migration and no model — it was created by hand at some point:

    CREATE UNIQUE INDEX merilv_courier_unique_name_ci
        ON merilv_couriermaster (lower(courier_name))

It makes a courier name globally unique, and that is the opposite of what this
module now needs. Courier Master holds one row per carrier PER PLANT AND
ENTITY, each with its own partner code and vehicle — Maruti Air Couriers And
Cargo is 41MC at 3044 and 24MCA at WGJ7 — and the index forbids the second row
outright. Importing the client's "Courier Master For All Plant" sheet fails on
its first repeated name:

    IntegrityError: duplicate key value violates unique constraint
    "merilv_courier_unique_name_ci"
    DETAIL: Key (lower(courier_name::text))=(cancelled invoice) already exists.

So the per-plant rows could never be created, one row survived per name, and
Courier Master showed a carrier's customers against whichever plant happened to
win — the wrong counts reported from the floor.

Nothing in the code depends on the name being unique. ``_active_courier_names``
already states the opposite in as many words ("a courier name is not unique in
Courier Master: the same carrier is held once per plant"), and every lookup
that matters resolves on the code plus plant plus entity.

``IF EXISTS`` because the index is in no migration: a database built from these
migrations alone never had it, and CI is exactly that. Reverse recreates it,
which will fail if per-plant rows exist by then — correctly, because at that
point the data genuinely cannot satisfy it.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("merilv", "0017_courier_entity"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS merilv_courier_unique_name_ci;",
            reverse_sql=(
                "CREATE UNIQUE INDEX IF NOT EXISTS merilv_courier_unique_name_ci "
                "ON merilv_couriermaster (lower(courier_name));"
            ),
        ),
    ]
