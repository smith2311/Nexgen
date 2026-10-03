"""
Data migration: authoritative full sync of the hospital master list.

Source: hospital_master_upload.csv (committed alongside this migration)
Key:    sap_code  (unique, never changes for a customer)
Logic:  update_or_create — safe to run on any existing DB, fully idempotent.

What this does on every deploy:
  • Creates any hospital not already in the DB
  • Updates name / province / salesperson_email for hospitals already present
  • Does NOT delete hospitals that may have been added later via the UI

To update: edit HOSPITALS below (or regenerate from the CSV), bump the
migration number, and re-run migrate.  Never hardcode data in application
code — keep it here so every environment stays in sync automatically.
"""
from django.db import migrations

# (name, sap_code, province, salesperson_email)
HOSPITALS = [
    # ── Western Cape / Eastern Cape  (sakshi.chopra) ─────────────────────────
    ('Bellville Melomed (DES)',              '11200034', 'Western Cape',   'sakshi.chopra@merillife.com'),
    ('Gatesville Hospital (DES)',            '11200025', 'Western Cape',   'sakshi.chopra@merillife.com'),
    ('Groote Schuur Hospital',               '11200030', 'Western Cape',   'sakshi.chopra@merillife.com'),
    ('Life St George\'s Hospital (DES)',     '11200045', 'Eastern Cape',   'sakshi.chopra@merillife.com'),
    ('Mediclinic Cape Town Hospital',        '11200437', 'Western Cape',   'sakshi.chopra@merillife.com'),
    ('Melomed Tokai',                        '11200264', 'Western Cape',   'sakshi.chopra@merillife.com'),
    ('Netcare Greenacres Pharmacy (DES)',    '11200029', 'Eastern Cape',   'sakshi.chopra@merillife.com'),
    ('Tygerberg Hospital',                   '11200046', 'Western Cape',   'sakshi.chopra@merillife.com'),

    # ── Gauteng / North West  (debbie) ───────────────────────────────────────
    ('Arwyp Medical Centre (Pty) Ltd (DES)', '11200009', 'Gauteng',        'debbie@merillife.com'),
    ('Charlotte Maxeke Johannesburg',        '11200097', 'Gauteng',        'debbie@merillife.com'),
    ('Lenmed - Ahmed Kathrada Pharmacy',     '11200004', 'Gauteng',        'debbie@merillife.com'),
    ('Lenmed Wilmed Park Hospital and Heart Centre', '11200329', 'North West', 'debbie@merillife.com'),
    ('Life Anncron Hospital (DES)',          '11200007', 'North West',     'debbie@merillife.com'),
    ('Life The Glynnwood (DES)',             '11200095', 'Gauteng',        'debbie@merillife.com'),
    ('Midvaal Private Hospital',             '11200131', 'Gauteng',        'debbie@merillife.com'),
    ('Morningside Mediclinic (DES)',         '11200154', 'Gauteng',        'debbie@merillife.com'),
    ('Netcare Alberton',                     '11200005', 'Gauteng',        'debbie@merillife.com'),
    ('Netcare Krugersdorp Pharmacy (DES)',   '11200099', 'Gauteng',        'debbie@merillife.com'),
    ('Netcare Linksfield Park Pharmacy (DES)', '11200107', 'Gauteng',      'debbie@merillife.com'),
    ('Netcare Sunward Park Pharmacy (DES)',  '11200113', 'Gauteng',        'debbie@merillife.com'),
    ('Tshepong hospital',                    '11200401', 'North West',     'debbie@merillife.com'),

    # ── Free State / Gauteng  (catharina.fourie) ─────────────────────────────
    ('Bloemfontein Mediclinic (DES)',        '11200014', 'Free State',     'catharina.fourie@merillife.com'),
    ('Life Eugene Marais Hospital (DES)',    '11200087', 'Gauteng',        'catharina.fourie@merillife.com'),
    ('Life Rosepark Hospital (DES)',         '11200105', 'Gauteng',        'catharina.fourie@merillife.com'),
    ('Netcare Unitas Pharmacy (DES)',        '11200117', 'Gauteng',        'catharina.fourie@merillife.com'),
    ('Steve Biko Academic Hospital (Con)',   '11200109', 'Gauteng',        'catharina.fourie@merillife.com'),
    ('Universitas State Hospital',           '11200119', 'Free State',     'catharina.fourie@merillife.com'),
    ('Life Wilgers Hospital (DES)',          '11200121', 'Gauteng',        'catharina.fourie@merillife.com'),

    # ── Gauteng / North West / Limpopo / Mpumalanga  (precious.monegi) ──────
    ('Life Peglerae Hospital',               '11200241', 'North West',     'precious.monegi@merillife.com'),
    ('Netcare Montana Pharmacy (DES)',       '11200152', 'Gauteng',        'precious.monegi@merillife.com'),
    ('Midstream Mediclinic (DES)',           '11200149', 'Gauteng',        'precious.monegi@merillife.com'),
    ('Mediclinic Nelspruit Hospital & Day Clinic', '11200156', 'Mpumalanga', 'precious.monegi@merillife.com'),
    ('Netcare Pholoso Hospital',             '11200100', 'Limpopo',        'precious.monegi@merillife.com'),
    ('Pretoria Heart Hospital (DES)',        '11200102', 'Gauteng',        'precious.monegi@merillife.com'),
    ('Raslouw Private Hospital',             '11200364', 'Gauteng',        'precious.monegi@merillife.com'),
    ('Zuid-Afrikaans Hospital (DES)',        '11200123', 'Gauteng',        'precious.monegi@merillife.com'),

    # ── Gauteng  (madaleen.roux) ─────────────────────────────────────────────
    ('Chris Hani Baragwanath Hospital (DES)', '11200010', 'Gauteng',      'madaleen.roux@merillife.com'),
    ('Dellomed Group (PTY) LTD(JSH)',        '11200386', 'Gauteng',        'madaleen.roux@merillife.com'),
    ('Life Flora Hospital (DES)',            '11200091', 'Gauteng',        'madaleen.roux@merillife.com'),
    ('Life fourways hospital',               '11200093', 'Gauteng',        'madaleen.roux@merillife.com'),
    ('Netcare Milpark Pharmacy (DES)',       '11200150', 'Gauteng',        'madaleen.roux@merillife.com'),
    ('Netcare Olivedale Pharmacy (DES)',     '11200137', 'Gauteng',        'madaleen.roux@merillife.com'),
    ('Netcare Sunninghill Pharmacy (DES)',   '11200112', 'Gauteng',        'madaleen.roux@merillife.com'),
    ('Mediclinic Vereeniging Hospital',      '11200389', 'Gauteng',        'madaleen.roux@merillife.com'),

    # ── Western Cape / Namibia  (nicole.vanheerden) ──────────────────────────
    ('Lady Pohamba Private Hospital',        '11200142', 'Namibia',        'nicole.vanheerden@merillife.com'),
    ('Life bay view private',                '11200015', 'Western Cape',   'nicole.vanheerden@merillife.com'),
    ('Life Vincent Pallotti Hospital (DES)', '11200054', 'Western Cape',   'nicole.vanheerden@merillife.com'),
    ('Mediclinic Durbanville Hospital',      '11200445', 'Western Cape',   'nicole.vanheerden@merillife.com'),
    ('Netcare Christiaan Barnard',           '11200020', 'Western Cape',   'nicole.vanheerden@merillife.com'),
    ('Panorama Mediclinic (DES)',            '11200041', 'Western Cape',   'nicole.vanheerden@merillife.com'),
    ('Roman Catholic Hospital',              '11200133', 'Namibia',        'nicole.vanheerden@merillife.com'),
    ('Rondebosch Heart Medical Supplies',    '11200391', 'Western Cape',   'nicole.vanheerden@merillife.com'),

    # ── Western Cape / Eastern Cape  (shantelle.oelofse) ────────────────────
    ('Busamed Paardevlei Private Hospital',  '11200038', 'Western Cape',   'shantelle.oelofse@merillife.com'),
    ('Life Kingsbury Hospital',              '11200031', 'Western Cape',   'shantelle.oelofse@merillife.com'),
    ('Life St Dominic\'s Hospital (DES)',    '11200022', 'Eastern Cape',   'shantelle.oelofse@merillife.com'),
    ('Netcare Blaauwberg Pharmacy',          '11200018', 'Western Cape',   'shantelle.oelofse@merillife.com'),
    ('Netcare Kuilsrivier Pharmacy (DES)',   '11200032', 'Western Cape',   'shantelle.oelofse@merillife.com'),
    ('Netcare N1 City Pharmacy (DES)',       '11200036', 'Western Cape',   'shantelle.oelofse@merillife.com'),
    ('Vergelegen Mediclinic (DES)',          '11200051', 'Western Cape',   'shantelle.oelofse@merillife.com'),

    # ── KwaZulu-Natal  (sumaya.reddhi) ───────────────────────────────────────
    ('Busamed Hillcrest',                    '11200072', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Capital Super Speciality Hospitals (Pty)', '11200160', 'KwaZulu-Natal', 'sumaya.reddhi@merillife.com'),
    ('City Hospital Ltd',                    '11200060', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Ethekwini Hospital & Heart Centre (DES)', '11200065', 'KwaZulu-Natal', 'sumaya.reddhi@merillife.com'),
    ('Gateway Private Hospital (Pty) Ltd',   '11200070', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ("GREY'S HOSPITAL (DES)",                '11200069', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Life Entabeni Hospital (DES)',          '11200062', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Life westville hospital',              '11200083', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Midlands Medical Centre (Pty) Ltd',    '11200396', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Netcare St. Augustine\'s Hospital',    '11200076', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Pietermaritzburg Mediclinic (DES)',     '11200074', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Shelly Beach Hospital',                '11200406', 'KwaZulu-Natal',  'sumaya.reddhi@merillife.com'),
    ('Inkosi Albert Luthuli Central Hospital', '11200056', 'KwaZulu-Natal', 'sumaya.reddhi@merillife.com'),
]


def sync_hospitals(apps, schema_editor):
    Hospital = apps.get_model('product_scanning', 'Hospital')
    created_count = 0
    updated_count = 0

    for name, sap_code, province, salesperson_email in HOSPITALS:
        obj, created = Hospital.objects.update_or_create(
            sap_code=sap_code,
            defaults={
                'name': name,
                'province': province,
                'salesperson_email': salesperson_email,
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    print(f'\n  Hospital master sync: {created_count} created, {updated_count} updated.')


def reverse_sync(apps, schema_editor):
    # Clear only the salesperson_email values set by this migration.
    # Does not delete hospitals — they may have linked scan data.
    Hospital = apps.get_model('product_scanning', 'Hospital')
    sap_codes = [row[1] for row in HOSPITALS]
    Hospital.objects.filter(sap_code__in=sap_codes).update(salesperson_email=None)


class Migration(migrations.Migration):

    dependencies = [
        ('product_scanning', '0019_rename_ps_gtin_map_gtin_idx_product_sca_gtin_co_63c5dc_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(sync_hospitals, reverse_sync),
    ]
