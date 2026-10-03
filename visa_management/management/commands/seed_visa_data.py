from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from visa_management.models import VisaCompany, VisaCountry, VisaLetterTemplate


# ── Companies ─────────────────────────────────────────────────────────────────

COMPANIES = [
    {
        'code': 'MLSPL',
        'name': 'Meril Life Sciences Pvt. Ltd.',
        'cin': 'U24239GJ2007PTC051137',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Meril is a global medical device company dedicated towards design and development of novel, '
            'clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering '
            'and improve the Quality of Life, spanning broad operational canvas from vascular interventional '
            'devices to orthopedics, in-vitro diagnostics and Endo-surgery.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'MLSIPL',
        'name': 'Meril Life Sciences India Pvt. Ltd.',
        'cin': 'U33110GJ1999PTC060787',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Meril Life Sciences India Pvt. Ltd. is engaged in manufacture, import, export, sell, distribute '
            'and deal in all kinds of medical devices, surgical instruments and allied products.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'MMIPL',
        'name': 'Meril Medical Innovations Pvt. Ltd.',
        'cin': 'U33100GJ2022PTC136543',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Meril Medical Innovations Pvt. Ltd., headquartered in Vapi, Gujarat, India, is a medical '
            'technology company established in 2022. The company is engaged in the research, design, '
            'manufacturing, and distribution of medical devices including suture needles, catheters, '
            'disposable syringes, testing machines, and other medical equipment.\n'
            'Meril operates in more than 150 countries through its global subsidiaries and distribution '
            'network. The company complies with international quality and regulatory standards and is '
            'committed to innovation and excellence in healthcare solutions.\n'
            'Meril is part of the Bilakhia Group and maintains strong corporate governance and '
            'operational standards.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'MHPL',
        'name': 'Meril Healthcare Pvt. Ltd.',
        'cin': 'U24230GJ2011PTC065022',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Meril is a global medical device company dedicated towards design and development of novel, '
            'clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering '
            'and improve the Quality of Life, spanning broad operational canvas from vascular interventional '
            'devices to orthopedics, in-vitro diagnostics and Endo-surgery.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'MDPL',
        'name': 'Meril Diagnostics Pvt. Ltd.',
        'cin': 'U33110GJ2011PTC064994',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Meril is a global medical device company dedicated towards design and development of novel, '
            'clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering '
            'and improve the Quality of Life, spanning broad operational canvas from vascular interventional '
            'devices to orthopedics, in-vitro diagnostics and Endo-surgery.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'MEPL',
        'name': 'Meril Endo Surgery Pvt. Ltd.',
        'cin': 'U33110GJ2011PTC065366',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Meril is a global medical device company dedicated towards design and development of novel, '
            'clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering '
            'and improve the Quality of Life, spanning broad operational canvas from vascular interventional '
            'devices to orthopedics, in-vitro diagnostics and Endo-surgery.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'MERAI',
        'name': 'Merai Newage Pvt. Ltd.',
        'cin': 'U62099GJ2023PTC146394',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Merai NewAge Pvt. Ltd. is an Indian robotics company developing advanced AI-enabled medical '
            'robotic systems. It specializes in robot-assisted surgical technologies to enhance precision, '
            'safety, and clinical outcomes. Its flagship products, MISSO (orthopedic knee replacement) and '
            'MIZZO (multi-specialty soft-tissue surgery), aim to make next-generation robotic surgery more '
            'accessible, efficient, and cost-effective worldwide.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'NUVOAI',
        'name': 'Nuvo AI Pvt. Ltd.',
        'cin': 'U62099GJ2023PTC142003',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Nuvo Lab., established on 18th July 2023, is a global medical device company committed to the '
            'design and development of novel, clinically relevant, and state-of-the-art devices. Our focus '
            'is on delivering best-in-class solutions that alleviate human suffering and improve the Quality '
            'of Life. With a wide operational scope, we specialize in areas ranging from vascular '
            'interventional devices to orthopedics, in-vitro diagnostics, and endo-surgery.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'NUVOLABS',
        'name': 'Nuvo Labs Pvt. Ltd.',
        'cin': 'U62099GJ2023PTC143047',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Nuvo Lab., established on 18th July 2023, is a global medical device company committed to the '
            'design and development of novel, clinically relevant, and state-of-the-art devices. Our focus '
            'is on delivering best-in-class solutions that alleviate human suffering and improve the Quality '
            'of Life. With a wide operational scope, we specialize in areas ranging from vascular '
            'interventional devices to orthopedics, in-vitro diagnostics, and endo-surgery.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'MICROLS',
        'name': 'Micro Life Sciences Pvt. Ltd.',
        'cin': 'U24119GJ2006PTC048361',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Incorporated in 2006, Micro Life Sciences Private Limited is the holding company for the '
            'healthcare division of the Bilakhia Group, Gujarat, Vapi. The company is engaged in the '
            'manufacturing and marketing of a wide range of medical devices, including stents, catheters, '
            'implants, diagnostic kits, and reagents. With strong capabilities in design, research, and '
            'product development, Micro Life Sciences has established a global footprint with marketing '
            'offices across Germany, the United States, and the United Kingdom. The company has recently '
            'attracted significant investments from leading global investors such as Temasek and the Abu '
            'Dhabi Investment Authority (ADIA), reinforcing its growth trajectory and commitment to '
            'advancing healthcare innovation worldwide. We have 10,000+ employees.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
    {
        'code': 'MICROCRISPR',
        'name': 'Micro CRISPR Pvt. Ltd.',
        'cin': 'U33309GJ2021PTC124575',
        'registered_address': '65, 66, 67 Muktanand Nagar, Vallabh Vidyanagar, Anand, Gujarat – 388 120',
        'description': (
            'Micro CRISPR Pvt. Ltd. is a biotechnology company based in Vapi, Gujarat, India, with a strong '
            'focus on advancing healthcare through cutting-edge research in Cell and Gene Therapy (CGT). '
            'With an outlier mindset and commitment to scientific excellence, the company is working toward '
            'developing 30 new Cell and Gene Therapies aimed at treating serious diseases such as Sickle '
            'Cell Anemia and blood cancers including Lymphoma, Leukemia, and Myeloma.'
        ),
        'tel': '+91 260 2408000',
        'email': 'Secretarial@merillife.com',
        'website': 'www.merillife.com',
        'signatory_name': 'Dr. H.C. Panjikar',
        'signatory_title': 'Group Head-Human Capital & Administration',
    },
]


# ── Countries ─────────────────────────────────────────────────────────────────

COUNTRIES = [
    # Asia-Pacific
    {
        'name': 'China',
        'code': 'CN',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Peoples Republic of China',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Japan',
        'code': 'JP',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Japan',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'South Korea',
        'code': 'KR',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Republic of Korea',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Singapore',
        'code': 'SG',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'High Commission of the Republic of Singapore',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Malaysia',
        'code': 'MY',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'High Commission of Malaysia',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Thailand',
        'code': 'TH',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Royal Thai Embassy',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Indonesia',
        'code': 'ID',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Republic of Indonesia',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Vietnam',
        'code': 'VN',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Socialist Republic of Vietnam',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Taiwan',
        'code': 'TW',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Taipei Economic and Cultural Center',
        'embassy_city': 'Mumbai',
        'default_entry_type': 'single',
    },
    {
        'name': 'Hong Kong',
        'code': 'HK',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Hong Kong Immigration Department',
        'embassy_city': 'Hong Kong',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Australia',
        'code': 'AU',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Australian High Commission',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    # Middle East
    {
        'name': 'UAE',
        'code': 'AE',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the United Arab Emirates',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Saudi Arabia',
        'code': 'SA',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Royal Embassy of Saudi Arabia',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'single',
    },
    {
        'name': 'Turkey',
        'code': 'TR',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Republic of Turkey',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    # Europe
    {
        'name': 'Germany',
        'code': 'DE',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Consulate General of Germany',
        'embassy_city': 'Mumbai',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'France',
        'code': 'FR',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Consulate General of France',
        'embassy_city': 'Mumbai',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Italy',
        'code': 'IT',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Consulate General of Italy',
        'embassy_city': 'Mumbai',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'United Kingdom',
        'code': 'GB',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'British High Commission',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Switzerland',
        'code': 'CH',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Switzerland',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Netherlands',
        'code': 'NL',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Kingdom of the Netherlands',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Spain',
        'code': 'ES',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Spain',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Belgium',
        'code': 'BE',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Belgium',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Austria',
        'code': 'AT',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Austria',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Sweden',
        'code': 'SE',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Sweden',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Denmark',
        'code': 'DK',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Kingdom of Denmark',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Poland',
        'code': 'PL',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Republic of Poland',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Czech Republic',
        'code': 'CZ',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Czech Republic',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Hungary',
        'code': 'HU',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Hungary',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Russia',
        'code': 'RU',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Russian Federation',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    # Americas
    {
        'name': 'USA',
        'code': 'US',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the United States of America',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Canada',
        'code': 'CA',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'High Commission of Canada',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Brazil',
        'code': 'BR',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Brazil',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    # Africa
    {
        'name': 'South Africa',
        'code': 'ZA',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Consulate General of the Republic of South Africa',
        'embassy_city': 'Mumbai',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Egypt',
        'code': 'EG',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Arab Republic of Egypt',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'single',
    },
    {
        'name': 'Kenya',
        'code': 'KE',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'High Commission of the Republic of Kenya',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'single',
    },
    {
        'name': 'Nigeria',
        'code': 'NG',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'High Commission of the Federal Republic of Nigeria',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'single',
    },
    {
        'name': 'Ethiopia',
        'code': 'ET',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of the Federal Democratic Republic of Ethiopia',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'single',
    },
    # Added from real covering-letter samples
    {
        'name': 'Colombia',
        'code': 'CO',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Colombia',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Israel',
        'code': 'IL',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Israel',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Algeria',
        'code': 'DZ',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Algeria',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Bangladesh',
        'code': 'BD',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Bangladesh High Commission',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Chile',
        'code': 'CL',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Chile',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Greece',
        'code': 'GR',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Embassy of Greece',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Lebanon',
        'code': 'LB',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'The Embassy of Lebanon',
        'embassy_city': 'New Delhi',
        'default_entry_type': 'multiple',
    },
    {
        'name': 'Mexico',
        'code': 'MX',
        'addressee_line1': 'The Visa Officer,',
        'addressee_line2': 'Consulate General of Mexico',
        'embassy_city': 'Mumbai',
        'default_entry_type': 'multiple',
    },
]


# ── Standard invitation letter body (works for all countries) ─────────────────

STANDARD_LETTER_BODY = """\
To,
The Visa Officer,
{{embassy_name}},
{{embassy_city}}

Sub: Request for {{entry_type_text}} Business Visa for {{employee_name}}

Dear Sir/Madam,

{{company_description}}

This is to certify that {{employee_name}}, {{designation}} at {{company_name}} (Date of Joining: {{joining_date}}), has been deputed for an official business visit to {{host_company_name}}, {{host_company_address}} for {{purpose_of_visit}}. {{he_she}} is scheduled to travel from {{visit_start_date}} to {{visit_end_date}}.

Particulars of the travel document are furnished below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

We, therefore, request you to kindly issue a {{entry_type_text}} Business Visa to {{employee_name}} to facilitate {{his_her}} travel. We assure you that {{he_she}} will comply with all rules and regulations applicable in your country and will return to India on or before {{visit_end_date}}.

Thanking you,

Yours faithfully,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
"""


# ── Custom per-(company, country) letter bodies transcribed from real,
# previously-sent covering letters — this dict is the ONLY source of which
# (company, country) template rows get seeded (see _seed_letter_templates
# below); there is no generic-boilerplate fallback seeded for any other
# combination — the shared default template (see _seed_default_template)
# covers everything else. Applicant-specific facts are replaced by
# {{placeholders}}. Deliberately has NO leading "Date:" line (the print/PDF
# wrapper templates already render the date once — a body-level date line
# would duplicate it), and the internal ref no is intentionally NOT shown
# in the "Sub:" line (an official embassy-facing letter shouldn't carry an
# internal tracking code).
# Keyed by (company_code, country_code); iterated directly by
# _seed_letter_templates — no other pair gets a template auto-created.

CUSTOM_LETTER_BODIES = {
    ('MHPL', 'CO'): [  # Colombia
        {
            'jurisdiction': '',
            'subject_line': 'Request for Colombia Business Visa',
            'letter_body': """\
To,
The Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub - Request for Colombia Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working in the capacity of {{designation}}, who needs to visit {{country_name}} for {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}. Considering the key features of our products, it would be absolutely essential for {{him_her}} to visit {{country_name}} and train the staff there and assist in further cases involved in the clinical trial.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and Endo-surgery.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} requisite visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MLSIPL', 'DK'): [  # Denmark
        {
            'jurisdiction': '',
            'subject_line': 'Request for Denmark Business Visa',
            'letter_body': """\
To,
The Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub - Request for Denmark Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with us as {{designation}} to {{purpose_of_visit}} taking place at {{host_company_name}}, {{host_company_address}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by {{company_name}}. Kindly grant {{him_her}} long term {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MLSPL', 'FR'): [  # France
        {
            'jurisdiction': '',
            'subject_line': 'Request for France Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for France Business visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working with us as {{designation}}, to {{purpose_of_visit}} scheduled from {{visit_start_date}} till {{visit_end_date}} in {{country_name}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to Orthopedics, in-vitro diagnostics and Endo-surgery.

All the expenses related to the travel and stay will be borne by company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MLSPL', 'IL'): [  # Israel
        {
            'jurisdiction': '',
            'subject_line': 'Request for Israel Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working with us in the capacity of {{designation}}. {{he_she}} is intending to travel to {{country_name}} to {{purpose_of_visit}}. {{his_her}} visit is scheduled between {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by the company and company takes guarantee of employees returning back to India after completion of {{his_her}} business trip. Kindly grant {{him_her}} long term multiple entry visa as {{he_she}} has to travel multiple times during the year.

{{mr_ms}} {{employee_name}} will get a proper health check-up done and provide a certificate from Indian medical authorities declaring {{his_her}} Covid-19 free status at the time of {{his_her}} travel.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MLSPL', 'JP'): [  # Japan
        {
            'jurisdiction': '',
            'subject_line': 'Request for Japan Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for Japan Business visa

Dear Sir,
This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with our company in the capacity of {{designation}} to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and Endo-surgery.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} business visa and oblige.
Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MLSPL', 'DZ'): [  # Algeria
        {
            'jurisdiction': '',
            'subject_line': 'Request for Algeria Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for {{entry_type_text}} Algeria business visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with us as {{designation}}. {{he_she}} is intending to travel to {{country_name}} to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by the company and company takes guarantee of employees returning back to India after completion of {{his_her}} business trip. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MEPL', 'AU'): [  # Australia
        {
            'jurisdiction': '',
            'subject_line': 'Request for Australia Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Subject: Business visa for {{mr_ms}} {{employee_name}}.

Dear Sir,
This is to request that, we would like our employee {{mr_ms}} {{employee_name}} working with us as {{designation}}, to visit {{country_name}} from {{visit_start_date}} to {{visit_end_date}} to {{purpose_of_visit}}.

{{his_her}} passport details as below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and Endo-surgery.

{{mr_ms}} {{employee_name}} will need to travel {{country_name}} for business meetings and hence we request you to kindly grant {{him_her}} business visa. All expenses of travel and stay in {{country_name}} would be borne by the India Company.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MLSIPL', 'BD'): [  # Bangladesh
        {
            'jurisdiction': '',
            'subject_line': 'Request for Bangladesh Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for Bangladesh {{entry_type_text}} Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working with us in the capacity of {{designation}} to {{purpose_of_visit}}. Hence would request for an issuance of {{entry_type_text}} business visa as {{he_she}} will be travelling to {{country_name}} for business meetings frequently. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopaedics, robotics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by the company. We earnestly request your expedited processing of {{his_her}} visa application to facilitate {{his_her}} timely travel.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MEPL', 'BE'): [  # Belgium
        {
            'jurisdiction': '',
            'subject_line': 'Request for Belgium Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for {{entry_type_text}} Belgium business visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with us as {{designation}}. {{he_she}} is intending to travel to {{country_name}} to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by the company and company takes guarantee of employees returning back to India after completion of {{his_her}} business trip. Kindly grant {{him_her}} long term {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MICROLS', 'BR'): [  # Brazil
        {
            'jurisdiction': '',
            'subject_line': 'Request for Brazil Business Visa',
            'letter_body': """\
To,
Visa Officer
{{embassy_name}}, {{embassy_city}}.

Sub- Request for Multiple Visit Brazil Business Visa

Dear Sir/Madam,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working with us as {{designation}}, to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Incorporated in 2006, Micro Life Sciences Private Limited is the holding company for the healthcare division of the Bilakhia Group, Gujarat, Vapi. The company is engaged in the manufacturing and marketing of a wide range of medical devices, including stents, catheters, implants, diagnostic kits, and reagents. With strong capabilities in design, research, and product development, Micro Life Sciences has established a global footprint with marketing offices across Germany, the United States, and the United Kingdom. The company has recently attracted significant investments from leading global investors such as Temasek and the Abu Dhabi Investment Authority (ADIA), reinforcing its growth trajectory and commitment to advancing healthcare innovation worldwide. We have 10,000+ employees.

All local expenses during {{his_her}} stay in {{country_name}} will be borne by the India Company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MDPL', 'CL'): [  # Chile
        {
            'jurisdiction': '',
            'subject_line': 'Request for Chile Business Visa',
            'letter_body': """\
To,
Consul General of Chile Mr. Gustavo Gonzalez

Sub- Request for Multiple Visit Chile Business Visa

Dear Sir/Madam,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working with us as {{designation}} to attend a business meeting at our subsidiary location in {{country_name}}, at {{host_company_address}} from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device manufacturing company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, robotics, in-vitro diagnostics and Endo-surgery.

All local expenses during {{his_her}} stay in {{country_name}} will be borne by the India Company. Kindly grant {{him_her}} long term {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MMIPL', 'CN'): [  # China
        {
            'jurisdiction': '',
            'subject_line': 'Request for China Business Visa',
            'letter_body': """\
To,
The Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Subject- Request for {{entry_type_text}} China Business Visa

Dear Sir,
This is to confirm that {{mr_ms}} {{employee_name}} is employed with {{company_name}} as {{designation}}, based at our Vapi office located at No. 135/2, Muktanand Marg, Chala, Vapi, Gujarat 396191, India. {{he_she}} has been working with our organization since {{joining_date}}. The purpose of the visit is to {{purpose_of_visit}}, with {{host_company_name}}, at {{host_company_address}}. The visit is scheduled from {{visit_start_date}} to {{visit_end_date}}. All travel expenses will be borne by {{company_name}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

{{company_name}}, headquartered in Vapi, Gujarat, India, is a medical technology company established in 2022. The company is engaged in the research, design, manufacturing, and distribution of medical devices including suture needles, catheters, disposable syringes, testing machines, and other medical equipment.
Meril operates in more than 150 countries through its global subsidiaries and distribution network. The company complies with international quality and regulatory standards and is committed to innovation and excellence in healthcare solutions.

Meril is part of the Bilakhia Group and maintains strong corporate governance and operational standards. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
        {
            'jurisdiction': 'Mumbai',
            'subject_line': 'Request for China Business Visa (Mumbai jurisdiction)',
            'letter_body': """\
To,
The Visa Officer,
Consulate of People's Republic of China,
Mumbai.

Subject- Request for {{entry_type_text}} China Business Visa

Dear Sir,
This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with us in the capacity of {{designation}}, based in Mumbai office, to {{purpose_of_visit}} with {{host_company_name}}, at {{host_company_address}}. {{he_she}} joined the organization on {{joining_date}}, to attend a series of important business meetings from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

{{company_name}} is a subsidiary of Micro Life Sciences Private Limited (Micro), a holding company of the Bilakhia Group. Incorporated in November 2022, it is constructing a new manufacturing facility for backward integration, aiming to reduce raw material costs and ensure quality for the parent group's medical device businesses. The parent company, Micro, has a strong presence in coronary implants, orthopedic implants, and diagnostic, robotics and endo-surgery products, and we have 10000+ employees.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} {{entry_type_text}} visa.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MHPL', 'DE'): [  # Germany
        {
            'jurisdiction': '',
            'subject_line': 'Request for Germany Business Visa',
            'letter_body': """\
To,
Visa Officer
{{embassy_name}},
{{embassy_city}}.

Sub- Request for Multiple Visit Germany Business Visa

Dear Sir/Madam,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, in the capacity of {{designation}} working with our company to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device manufacturing company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, robotics, in-vitro diagnostics and endo-surgery.

All local expenses during {{his_her}} stay in Germany will be borne by the India Company. Due to the ongoing business relationship and long-term cooperation between Meril GmbH and {{company_name}}, {{mr_ms}} {{employee_name}} will be required to travel frequently to Germany for business meetings, distributor interactions, training programs, and other business development activities. We kindly request you to grant {{him_her}} a long-term {{entry_type_text}} business visa.

Thanking You,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
        {
            'jurisdiction': 'New Delhi',
            'subject_line': 'Request for Germany Business Visa (New Delhi jurisdiction)',
            'letter_body': """\
To,
Visa Officer
Embassy of Germany,
New Delhi.

Sub- Request for Multiple Visit Germany Business Visa

Dear Sir/Madam,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, in the capacity of {{designation}} working with our company to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device manufacturing company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, robotics, in-vitro diagnostics and endo-surgery.

All local expenses during {{his_her}} stay in Germany will be borne by the India Company. Due to the ongoing business relationship and long-term cooperation between Meril GmbH and {{company_name}}, {{mr_ms}} {{employee_name}} will be required to travel frequently to Germany for business meetings, distributor interactions, training programs, and other business development activities. We kindly request you to grant {{him_her}} a long-term {{entry_type_text}} business visa.

Thanking You,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MLSPL', 'GR'): [  # Greece
        {
            'jurisdiction': '',
            'subject_line': 'Request for Greece Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for {{entry_type_text}} Greece business visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with us as {{designation}}. {{he_she}} is intending to travel to {{country_name}} to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by the company and company takes guarantee of employees returning back to India after completion of {{his_her}} business trip. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MEPL', 'HU'): [  # Hungary
        {
            'jurisdiction': '',
            'subject_line': 'Request for Hungary Business Visa',
            'letter_body': """\
To,
The Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for Hungary Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with the capacity of {{designation}}, to {{purpose_of_visit}}. {{his_her}} date of travel is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device manufacturing company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics, robotics and Endo-surgery.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




Mr. Chintan Sampat
Senior Manager
""",
        },
        {
            'jurisdiction': 'Mumbai',
            'subject_line': 'Request for Hungary Business Visa (Mumbai jurisdiction)',
            'letter_body': """\
To,
The Visa Officer,
Consulate General of Hungary,
Mumbai.

Sub- Request for Hungary Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with the capacity of {{designation}}, to {{purpose_of_visit}} and to continue discussion on the nature of the business relationship and other aspects of proposed cooperation in detail. {{his_her}} date of travel is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device manufacturing company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics, robotics and Endo-surgery.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




Mr. Hemchandra Panjikar
Group Head-Human Capital & Administration
""",
        },
    ],

    ('MHPL', 'KE'): [  # Kenya
        {
            'jurisdiction': '',
            'subject_line': 'Request for Kenya Business Visa',
            'letter_body': """\
To
The Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for Kenya {{entry_type_text}} Business Visa

Dear Sir/Madam,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with us as {{designation}} to {{purpose_of_visit}} scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MLSPL', 'LB'): [  # Lebanon
        {
            'jurisdiction': '',
            'subject_line': 'Request for Lebanon Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}}
{{embassy_city}}.

Sub- Request for Lebanon Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working with us in the capacity of {{designation}}. {{he_she}} is intending to visit {{country_name}} for {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, robotics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,
Yours Truly,
For {{company_name}}




Ms. Tanveer Khan
Area Manager
""",
        },
    ],

    ('MEPL', 'MX'): [  # Mexico
        {
            'jurisdiction': '',
            'subject_line': 'Request for Mexico Business Visa',
            'letter_body': """\
To
The Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub- Request for Mexico Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working with us in the {{designation}}. {{he_she}} is intending to visit {{country_name}} to {{purpose_of_visit}}. {{his_her}} travel is planned from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopaedics, in-vitro diagnostics and Endo-surgery. {{company_name}} is subsidiary company of Meril Life Sciences Pvt. Ltd.

All the expenses related to the travel and stay will be borne by {{company_name}}, India, which takes guarantee of employees returning back to India after completion of {{his_her}} business trip. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MHPL', 'PL'): [  # Poland
        {
            'jurisdiction': '',
            'subject_line': 'Request for Poland Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}

Sub- Request for Poland {{entry_type_text}} Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working as {{designation}} for attending a series of important business meetings. {{mr_ms}} {{employee_name}} has been posted for field work since {{he_she}} joined our organization {{company_name}} on {{joining_date}}. {{his_her}} visit is scheduled from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device manufacturing company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics, robotics and endo-surgery.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} requisite visa and oblige.

{{mr_ms}} {{employee_name}} is a genuine traveler and we undertake the guarantee of {{his_her}} return before the expiry of the visa.

Thanking You,

Yours Truly,
For {{company_name}}




Mr. Sanjay Yadav
Vice President- International Sales
""",
        },
    ],

    ('MHPL', 'GB'): [  # United Kingdom
        {
            'jurisdiction': '',
            'subject_line': 'Request for United Kingdom Business Visa',
            'letter_body': """\
To,
The Visa Officer,
{{embassy_name}},
{{embassy_city}}.

Sub - Request for {{entry_type_text}} UK Business Visa

Dear Sir,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}}, working with us as {{designation}} to attend our UK Subsidiary Meril UK Limited to have business meetings for {{purpose_of_visit}} from {{visit_start_date}} to {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are given below:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device manufacturing company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics, robotics and Endo-surgery.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} {{entry_type_text}} visa.

Thanking You,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],

    ('MICROLS', 'US'): [  # USA
        {
            'jurisdiction': '',
            'subject_line': 'Request for USA Business Visa',
            'letter_body': """\
To,
Visa Officer,
{{embassy_name}},
{{embassy_city}}, India.

Sub- Request for US Business visa

Dear Sir/Madam,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with our company as {{designation}}, to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} till {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are as follows:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Incorporated in 2006, Micro Life Sciences Private Limited is the holding company for the healthcare division of the Bilakhia Group, Gujarat, Vapi. The company is engaged in the manufacturing and marketing of a wide range of medical devices, including stents, catheters, implants, diagnostic kits, and reagents. With strong capabilities in design, research, and product development, Micro Life Sciences has established a global footprint with marketing offices across Germany, the United States, and the United Kingdom. The company has recently attracted significant investments from leading global investors such as Temasek and the Abu Dhabi Investment Authority (ADIA), reinforcing its growth trajectory and commitment to advancing healthcare innovation worldwide. We have 10,000+ employees.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

{{mr_ms}} {{employee_name}} will get a proper health check-up done and provide a certificate from Indian medical authorities declaring {{his_her}} Covid-19 free status at the time of {{his_her}} travel.

Thanking you,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
        {
            'jurisdiction': 'Mumbai',
            'subject_line': 'Request for USA Business Visa (Mumbai jurisdiction)',
            'letter_body': """\
To,
Visa Officer,
Consulate General of USA,
Mumbai, India.

Sub- Request for US Business visa

Dear Sir/Madam,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with our company as {{designation}}, to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} till {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are as follows:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

{{mr_ms}} {{employee_name}} will get a proper health check-up done and provide a certificate from Indian medical authorities declaring {{his_her}} Covid-19 free status at the time of {{his_her}} travel.

Thanking you,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
        {
            'jurisdiction': 'Chennai',
            'subject_line': 'Request for USA Business Visa (Chennai jurisdiction)',
            'letter_body': """\
To,
Visa Officer,
Consulate General of USA,
Chennai, India.

Sub- Request for US Business visa

Dear Sir/Madam,

This is to confirm that {{company_name}} is sponsoring the travel of {{mr_ms}} {{employee_name}} working with our company as {{designation}}, to {{purpose_of_visit}}. {{his_her}} visit is scheduled from {{visit_start_date}} till {{visit_end_date}}.

Passport details of {{mr_ms}} {{employee_name}} are as follows:

  Name as per Passport  :  {{passport_name}}
  Passport Number       :  {{passport_number}}
  Date of Birth         :  {{date_of_birth}}
  Date of Issue         :  {{passport_issue_date}}
  Date of Expiry        :  {{passport_expiry_date}}
  Place of Issue        :  {{place_of_issue}}
  Nationality           :  {{nationality}}

Meril is a global medical device company dedicated towards design and development of novel, clinically relevant, state-of-the-art and best-in-class devices to alleviate human suffering and improve the Quality of Life, spanning broad operational canvas from vascular interventional devices to orthopedics, in-vitro diagnostics and endo-surgery.

All the expenses related to the travel and stay will be borne by the company. Kindly grant {{him_her}} {{entry_type_text}} visa and oblige.

{{mr_ms}} {{employee_name}} will get a proper health check-up done and provide a certificate from Indian medical authorities declaring {{his_her}} Covid-19 free status at the time of {{his_her}} travel.

Thanking you,

Yours Truly,
For {{company_name}}




{{signatory_name}}
{{signatory_title}}
""",
        },
    ],
}


GROUPS = ['Visa-Applicant', 'Visa-HOD', 'Visa-Department']


class Command(BaseCommand):
    help = 'Seed Visa Management: auth groups, companies, countries, and letter templates'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-templates',
            action='store_true',
            help='Skip seeding letter templates',
        )
        parser.add_argument(
            '--reset-templates',
            action='store_true',
            help='Delete all non-default letter templates before reseeding (the is_default fallback template is never touched)',
        )

    def handle(self, *args, **options):
        self._seed_groups()
        self._seed_companies()
        self._seed_countries()
        if not options['skip_templates']:
            if options['reset_templates']:
                self._reset_templates()
            self._seed_letter_templates()
            self._seed_default_template()
        self.stdout.write(self.style.SUCCESS('\nVisa seed data loaded successfully.'))

    def _reset_templates(self):
        self.stdout.write('\n--- Resetting non-default templates ---')
        deleted, _ = VisaLetterTemplate.objects.filter(is_default=False).delete()
        self.stdout.write(self.style.WARNING(f'  Deleted {deleted} non-default template(s)'))

    def _seed_groups(self):
        self.stdout.write('\n--- Auth Groups ---')
        for name in GROUPS:
            _, created = Group.objects.get_or_create(name=name)
            status = 'created' if created else 'exists'
            self.stdout.write(f'  Group "{name}" — {status}')

    def _seed_companies(self):
        self.stdout.write('\n--- Companies ---')
        for data in COMPANIES:
            obj, created = VisaCompany.objects.update_or_create(
                code=data['code'],
                defaults={k: v for k, v in data.items() if k != 'code'},
            )
            status = 'created' if created else 'updated'
            self.stdout.write(f'  {obj.code:12s} — {obj.name[:50]} [{status}]')

    def _seed_countries(self):
        self.stdout.write('\n--- Countries ---')
        for data in COUNTRIES:
            obj, created = VisaCountry.objects.update_or_create(
                code=data['code'],
                defaults={k: v for k, v in data.items() if k != 'code'},
            )
            status = 'created' if created else 'updated'
            self.stdout.write(f'  {obj.code:4s} {obj.name:25s} — {status}')

    def _seed_letter_templates(self):
        self.stdout.write('\n--- Letter Templates (only pairs backed by a real letter sample) ---')
        created_count = updated_count = skipped_count = 0
        for (company_code, country_code), variants in CUSTOM_LETTER_BODIES.items():
            try:
                company = VisaCompany.objects.get(code=company_code)
                country = VisaCountry.objects.get(code=country_code)
            except (VisaCompany.DoesNotExist, VisaCountry.DoesNotExist):
                self.stdout.write(self.style.WARNING(
                    f'  {company_code} / {country_code} — company or country not found, skipping'
                ))
                skipped_count += 1
                continue

            for variant in variants:
                jurisdiction = variant.get('jurisdiction', '')
                obj, created = VisaLetterTemplate.objects.update_or_create(
                    company=company,
                    country=country,
                    entry_type='both',
                    jurisdiction=jurisdiction,
                    defaults={
                        'subject_line': variant['subject_line'],
                        'letter_body': variant['letter_body'],
                        'is_active': True,
                    },
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1
                status = 'created' if created else 'updated'
                label = jurisdiction or '(default)'
                self.stdout.write(f'  {company_code:8s} / {country.name:20s} / {label:12s} — {status}')

        self.stdout.write(
            f'\n  {created_count} template(s) created, {updated_count} updated, {skipped_count} skipped'
        )

    def _seed_default_template(self):
        self.stdout.write('\n--- Default Letter Template (fallback) ---')
        obj, created = VisaLetterTemplate.objects.update_or_create(
            company=None,
            country=None,
            is_default=True,
            entry_type='both',
            defaults={
                'subject_line': 'Request for Business Visa',
                'letter_body': STANDARD_LETTER_BODY,
                'is_active': True,
            },
        )
        status = 'created' if created else 'updated'
        self.stdout.write(f'  Default template — {status}')
