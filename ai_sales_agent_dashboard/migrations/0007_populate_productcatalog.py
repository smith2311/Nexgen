# Data migration to populate ProductCatalog with Meril products

from django.db import migrations


def populate_products(apps, schema_editor):
    ProductCatalog = apps.get_model('ai_sales_agent_dashboard', 'ProductCatalog')

    products_data = [
        {
            'name': 'Cardiovascular',
            'icon': '❤️',
            'description': 'Coronary stents, heart valves, structural heart devices & TAVR',
            'tags': ['DES Stents', 'TAVR', 'Mitral Clip', 'CTO Devices'],
            'keywords': ['cardiac', 'cardio', 'cath lab', 'coronary', 'heart valve',
                         'cardiovascular', 'angioplasty', 'heart hospital', 'heart care',
                         'pci ', 'ccu ', 'structural heart', 'interventional cardiol'],
            'display_order': 1,
        },
        {
            'name': 'Endosurgery',
            'icon': '🔬',
            'description': 'Surgical staplers, energy devices, trocars & laparoscopic access',
            'tags': ['Staplers', 'Vessel Sealer', 'Trocars', 'Laparoscopy'],
            'keywords': ['laparoscop', 'oncolog', 'bariatric', 'operating theatre',
                         'endoscop', 'multi-speciality', 'multispeciality',
                         'multi speciality', 'super speciality', 'cancer hospital',
                         'cancer centre', 'robotic surgery', 'minimally invasive'],
            'display_order': 2,
        },
        {
            'name': 'Orthopaedics',
            'icon': '🦴',
            'description': 'Joint replacements, trauma implants & sports medicine devices',
            'tags': ['Knee Replacement', 'Hip Replacement', 'Trauma', 'Spine'],
            'keywords': ['orthop', 'orthopaedic', 'bone and joint', 'spine hospital',
                         'joint replacement', 'trauma centre', 'sports medicine',
                         'musculoskeletal', 'fracture clinic'],
            'display_order': 3,
        },
        {
            'name': 'Diagnostics',
            'icon': '🧪',
            'description': 'Rapid test kits, in-vitro diagnostics & point-of-care testing',
            'tags': ['Rapid Tests', 'IVD', 'POC Testing', 'Glucometer'],
            'keywords': ['diagnostic centre', 'diagnostic lab', 'pathology lab',
                         'pathology centre', 'clinical laboratory', 'ivd ', 'point of care',
                         'blood bank', 'laboratory services'],
            'display_order': 4,
        },
        {
            'name': 'Electrophysiology',
            'icon': '⚡',
            'description': 'EP catheters, cardiac ablation & mapping systems',
            'tags': ['EP Catheters', 'RF Ablation', 'Mapping'],
            'keywords': ['electrophysiol', 'ep lab', 'arrhythmia', 'ablation clinic',
                         'pacemaker', 'defibrillat', 'heart rhythm'],
            'display_order': 5,
        },
        {
            'name': 'Vascular Access & Intervention',
            'icon': '💉',
            'description': 'Peripheral intervention, guide wires, balloons & stent grafts',
            'tags': ['PTA Balloons', 'Guide Wires', 'Stent Grafts', 'Peripheral'],
            'keywords': ['vascular', 'peripheral intervention', 'angiograph',
                         'dialysis centre', 'nephrology', 'kidney hospital', 'renal care'],
            'display_order': 6,
        },
        {
            'name': 'Wound Care & Aesthetics',
            'icon': '🩹',
            'description': 'Sutures, skin staplers & aesthetic fillers',
            'tags': ['Sutures', 'Skin Staplers', 'Fillers'],
            'keywords': ['wound care', 'aesthetic', 'dermatolog', 'plastic surgery',
                         'cosmetic surgery', 'burn unit', 'skin clinic', 'hair clinic',
                         'skin care clinic', 'cosmetolog'],
            'display_order': 7,
        },
    ]

    for product in products_data:
        ProductCatalog.objects.create(**product)


def reverse_products(apps, schema_editor):
    ProductCatalog = apps.get_model('ai_sales_agent_dashboard', 'ProductCatalog')
    ProductCatalog.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('ai_sales_agent_dashboard', '0006_productcatalog'),
    ]

    operations = [
        migrations.RunPython(populate_products, reverse_products),
    ]
