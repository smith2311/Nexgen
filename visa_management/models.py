from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()


class VisaCompany(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    cin = models.CharField(max_length=30, blank=True)
    registered_address = models.TextField(blank=True)
    description = models.TextField(blank=True, help_text='Company boilerplate paragraph for letters')
    tel = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=100, blank=True)
    signatory_name = models.CharField(max_length=150, default='Dr. H.C. Panjikar')
    signatory_title = models.CharField(max_length=200, default='Group Head-Human Capital & Administration')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Visa Company'
        verbose_name_plural = 'Visa Companies'

    def __str__(self):
        return f'{self.code} — {self.name}'


class VisaCountry(models.Model):
    ENTRY_CHOICES = [('single', 'Single Entry'), ('multiple', 'Multiple Entry'), ('both', 'Both')]

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=3, unique=True)
    addressee_line1 = models.CharField(max_length=200, default='The Visa Officer,')
    addressee_line2 = models.CharField(max_length=200, blank=True, help_text='e.g. Embassy of Peoples Republic of China')
    embassy_city = models.CharField(max_length=50, blank=True)
    default_entry_type = models.CharField(max_length=10, choices=ENTRY_CHOICES, default='multiple')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Visa Country'
        verbose_name_plural = 'Visa Countries'

    def __str__(self):
        return self.name


class VisaLetterTemplate(models.Model):
    ENTRY_CHOICES = [('single', 'Single Entry'), ('multiple', 'Multiple Entry'), ('both', 'Both')]

    company = models.ForeignKey(
        VisaCompany, on_delete=models.CASCADE, related_name='letter_templates',
        null=True, blank=True, help_text='Leave blank for a default template used as a fallback',
    )
    country = models.ForeignKey(
        VisaCountry, on_delete=models.CASCADE, related_name='letter_templates',
        null=True, blank=True, help_text='Leave blank for a default template used as a fallback',
    )
    is_default = models.BooleanField(
        default=False,
        help_text='Used when no company + country specific template exists',
    )
    entry_type = models.CharField(max_length=10, choices=ENTRY_CHOICES, default='both')
    jurisdiction = models.CharField(
        max_length=100, blank=True, default='',
        help_text=(
            'Embassy/consulate jurisdiction this specific letter is written for, e.g. '
            '"Mumbai" or "Chennai" — leave blank for the primary template used automatically '
            'when a company + country has only one letter, or as the default among several.'
        ),
    )
    subject_line = models.CharField(max_length=300)
    letter_body = models.TextField(help_text='Use {{variable_name}} placeholders. See spec for full list.')
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company__code', 'country__name', 'jurisdiction']
        verbose_name = 'Letter Template'

    def __str__(self):
        if self.is_default:
            return f'Default ({self.entry_type})'
        label = f'{self.company.code} / {self.country.name} ({self.entry_type})'
        return f'{label} — {self.jurisdiction}' if self.jurisdiction else label


class VisaApplication(models.Model):
    STATUS_CHOICES = [
        ('draft',           'Draft'),
        ('pending_hod',     'Pending HOD Approval'),
        ('hod_approved',    'HOD Approved'),
        ('hod_rejected',    'HOD Rejected'),
        ('processing',      'Processing'),
        ('issued',          'Issued'),
        ('rejected',        'Rejected'),
    ]
    GENDER_CHOICES = [('M', 'Male'), ('F', 'Female')]
    ENTRY_CHOICES = [('single', 'Single Entry'), ('multiple', 'Multiple Entry')]

    # Reference
    ref_no = models.CharField(max_length=30, unique=True, blank=True)

    # Applicant info
    applicant = models.ForeignKey(User, on_delete=models.PROTECT, related_name='visa_applications')
    applicant_name = models.CharField(max_length=200)
    applicant_email = models.EmailField()
    designation = models.CharField(max_length=200)
    company = models.ForeignKey(VisaCompany, on_delete=models.PROTECT)
    joining_date = models.DateField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)

    # HOD details
    hod_name = models.CharField(max_length=200)
    hod_email = models.EmailField()

    # Travel details
    country = models.ForeignKey(VisaCountry, on_delete=models.PROTECT)
    visit_start_date = models.DateField()
    visit_end_date = models.DateField()
    entry_type = models.CharField(max_length=10, choices=ENTRY_CHOICES, default='multiple')
    host_company_name = models.CharField(max_length=300)
    host_company_address = models.TextField()
    purpose_of_visit = models.TextField(blank=True)

    # Passport details
    passport_name = models.CharField(max_length=200)
    passport_number = models.CharField(max_length=30)
    date_of_birth = models.DateField()
    passport_issue_date = models.DateField()
    passport_expiry_date = models.DateField()
    place_of_issue = models.CharField(max_length=100)
    nationality = models.CharField(max_length=50, default='INDIAN')

    # Remarks — one per stage, so each party's comment survives independently
    # instead of being overwritten by the next stage. The activity log keeps the
    # full history; these hold the latest remark for display on the detail pages.
    applicant_remark = models.TextField(blank=True, verbose_name='Applicant Remark')
    hod_remark = models.TextField(blank=True, verbose_name='HOD Remark')
    visa_dept_remark = models.TextField(blank=True, verbose_name='Visa Department Remark')

    # Status & tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    hod_actioned_at = models.DateTimeField(null=True, blank=True)
    hod_rejection_reason = models.TextField(blank=True)
    visa_dept_notes = models.TextField(blank=True)
    custom_letter_body = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Visa Application'

    def __str__(self):
        return f'{self.ref_no} — {self.applicant_name} → {self.country.name}'

    def save(self, *args, **kwargs):
        if not self.ref_no:
            year = timezone.now().year
            prefix = f'VISA/{year}/'
            # Derive the sequence from the highest ref issued, not from the row
            # count: deleting an application would otherwise lower the count and
            # hand the next applicant a ref_no that is already taken.
            highest = (VisaApplication.objects
                       .filter(ref_no__startswith=prefix)
                       .aggregate(models.Max('ref_no'))['ref_no__max'])
            seq = int(highest[len(prefix):]) + 1 if highest else 1
            self.ref_no = f'{prefix}{str(seq).zfill(4)}'
        super().save(*args, **kwargs)

    @property
    def he_she(self):
        return 'He' if self.gender == 'M' else 'She'

    @property
    def his_her(self):
        return 'his' if self.gender == 'M' else 'her'

    @property
    def him_her(self):
        return 'him' if self.gender == 'M' else 'her'

    @property
    def status_css(self):
        return {
            'draft':        'secondary',
            'pending_hod':  'warning',
            'hod_approved': 'info',
            'hod_rejected': 'danger',
            'processing':   'primary',
            'issued':       'success',
            'rejected':     'danger',
        }.get(self.status, 'secondary')

    @property
    def status_label(self):
        return dict(self.STATUS_CHOICES).get(self.status, self.status)


class VisaApplicationLog(models.Model):
    ACTION_CHOICES = [
        ('submitted',      'Submitted'),
        ('hod_approved',   'HOD Approved'),
        ('hod_rejected',   'HOD Rejected'),
        ('letter_edited',  'Letter Edited'),
        ('issued',         'Issued'),
        ('rejected',       'Rejected'),
    ]

    application = models.ForeignKey(VisaApplication, on_delete=models.CASCADE, related_name='logs')
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f'{self.application.ref_no} — {self.action} @ {self.timestamp:%Y-%m-%d %H:%M}'
