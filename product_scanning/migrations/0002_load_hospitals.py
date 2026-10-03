# Data migration to populate hospitals from provided list

from django.db import migrations


def load_hospitals(apps, schema_editor):
    Hospital = apps.get_model('product_scanning', 'Hospital')

    hospitals = [
        ('Life St Dominic\'s Hospital (DES)', '11200022', 'Eastern Cape'),
        ('Netcare Greenacres Pharmacy (DES)', '11200029', 'Eastern Cape'),
        ('Life St George\'s Hospital (DES)', '11200045', 'Eastern Cape'),
        ('Universitas State Hospital', '11200119', 'Free State'),
        ('Bloemfontein Mediclinic (DES)', '11200014', 'Free State'),
        ('Lenmed - Ahmed Kathrada Pharmacy', '11200004', 'Gauteng'),
        ('Netcare Alberton', '11200005', 'Gauteng'),
        ('Arwyp Medical Centre (Pty) Ltd (DES)', '11200009', 'Gauteng'),
        ('Life Eugene Marais Hospital (DES)', '11200087', 'Gauteng'),
        ('Life The Glynnwood (DES)', '11200095', 'Gauteng'),
        ('Pretoria Heart Hospital (DES)', '11200102', 'Gauteng'),
        ('Life Rosepark Hospital (DES)', '11200105', 'Gauteng'),
        ('Netcare Linksfield Park Pharmacy (DES)', '11200107', 'Gauteng'),
        ('Netcare Sunninghill Pharmacy (DES)', '11200112', 'Gauteng'),
        ('Netcare Sunward Park Pharmacy (DES)', '11200113', 'Gauteng'),
        ('Life Wilgers Hospital (DES)', '11200121', 'Gauteng'),
        ('Zuid-Afrikaans Hospital (DES)', '11200123', 'Gauteng'),
        ('Netcare Olivedale Pharmacy (DES)', '11200137', 'Gauteng'),
        ('Midstream Mediclinic (DES)', '11200149', 'Gauteng'),
        ('Netcare Milpark Pharmacy (DES)', '11200150', 'Gauteng'),
        ('Netcare Montana Pharmacy (DES)', '11200152', 'Gauteng'),
        ('Morningside Mediclinic (DES)', '11200154', 'Gauteng'),
        ('Dellomed Group  (PTY) LTD(JSH)', '11200386', 'Gauteng'),
        ('Mediclinic Durbanville Hospital', '11200445', 'Gauteng'),
        ('1 Military hospital (des)', '11200002', 'Gauteng'),
        ('Chris Hani Baragwanath Hospital (DES)', '11200010', 'Gauteng'),
        ('Life Flora Hospital (DES)', '11200091', 'Gauteng'),
        ('Life fourways hospital', '11200093', 'Gauteng'),
        ('Charlotte Maxeke Johannesburg', '11200097', 'Gauteng'),
        ('Netcare Krugersdorp Pharmacy (DES)', '11200099', 'Gauteng'),
        ('Steve Biko Academic Hospital (Con)', '11200109', 'Gauteng'),
        ('Netcare Unitas Pharmacy (DES)', '11200117', 'Gauteng'),
        ('Netcare union', '11200118', 'Gauteng'),
        ('Midvaal Private Hospital', '11200131', 'Gauteng'),
        ('City Hospital Ltd', '11200060', 'KwaZulu-Natal'),
        ('Ethekwini Hospital & Heart Centre (DES)', '11200065', 'KwaZulu-Natal'),
        ('Gateway Private Hospital (Pty) Ltd', '11200070', 'KwaZulu-Natal'),
        ('Pietermaritzburg Mediclinic (DES)', '11200074', 'KwaZulu-Natal'),
        ('Netcare St. Augustine\'s Hospital', '11200076', 'KwaZulu-Natal'),
        ('Capital Super Speciality Hospitals (Pty)', '11200160', 'KwaZulu-Natal'),
        ('Shelly Beach Hospital', '11200406', 'KwaZulu-Natal'),
        ('Life Entabeni Hospital (DES)', '11200062', 'KwaZulu-Natal'),
        ('GREY\'S HOSPITAL (DES)', '11200069', 'KwaZulu-Natal'),
        ('Busamed Hillcrest', '11200072', 'KwaZulu-Natal'),
        ('Life westville hospital', '11200083', 'KwaZulu-Natal'),
        ('Midlands Medical Centre (Pty ) Ltd', '11200396', 'KwaZulu-Natal'),
        ('Roman Catholic Hospital', '11200133', 'Namibia'),
        ('Lady Pohamba Private Hospital', '11200142', 'Namibia'),
        ('Life Anncron Hospital (DES)', '11200007', 'North West'),
        ('Life Peglerae Hospital', '11200241', 'North West'),
        ('Tshepong hospital', '11200401', 'North West'),
        ('Netcare Blaauwberg Pharmacy', '11200018', 'Western Cape'),
        ('Netcare Christiaan Barnard', '11200020', 'Western Cape'),
        ('Gatesville Hospital (DES)', '11200025', 'Western Cape'),
        ('Netcare Kuilsrivier Pharmacy (DES)', '11200032', 'Western Cape'),
        ('Bellville Melomed (DES)', '11200034', 'Western Cape'),
        ('Netcare N1 City Pharmacy (DES)', '11200036', 'Western Cape'),
        ('Busamed Paardevlei Private Hospital', '11200038', 'Western Cape'),
        ('Panorama Mediclinic (DES)', '11200041', 'Western Cape'),
        ('Tygerberg Hospital', '11200046', 'Western Cape'),
        ('Vergelegen Mediclinic (DES)', '11200051', 'Western Cape'),
        ('Life Vincent Pallotti Hospital (DES)', '11200054', 'Western Cape'),
        ('Melomed Tokai', '11200264', 'Western Cape'),
        ('Rondebosch Heart Medical Supplies', '11200391', 'Western Cape'),
        ('Life bay view private', '11200015', 'Western Cape'),
        ('Groote Schuur Hospital', '11200030', 'Western Cape'),
        ('Life Kingsbury Hospital', '11200031', 'Western Cape'),
        ('Mediclinic Cape Town Hospital', '11200437', 'Western Cape'),
    ]

    for name, code, province in hospitals:
        Hospital.objects.get_or_create(
            sap_code=code,
            defaults={
                'name': name,
                'province': province,
            }
        )


def reverse_hospitals(apps, schema_editor):
    """Remove all hospitals created during this migration"""
    try:
        Hospital = apps.get_model('product_scanning', 'Hospital')
        codes = [
            '11200022', '11200029', '11200045', '11200119', '11200014', '11200004',
            '11200005', '11200009', '11200087', '11200095', '11200102', '11200105',
            '11200107', '11200112', '11200113', '11200121', '11200123', '11200137',
            '11200149', '11200150', '11200152', '11200154', '11200386', '11200445',
            '11200002', '11200010', '11200091', '11200093', '11200097', '11200099',
            '11200109', '11200117', '11200118', '11200131', '11200060', '11200065',
            '11200070', '11200074', '11200076', '11200160', '11200406', '11200062',
            '11200069', '11200072', '11200083', '11200396', '11200133', '11200142',
            '11200007', '11200241', '11200401', '11200018', '11200020', '11200025',
            '11200032', '11200034', '11200036', '11200038', '11200041', '11200046',
            '11200051', '11200054', '11200264', '11200391', '11200015', '11200030',
            '11200031', '11200437',
        ]
        Hospital.objects.filter(sap_code__in=codes).delete()
    except Exception:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('product_scanning', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(load_hospitals, reverse_hospitals),
    ]
