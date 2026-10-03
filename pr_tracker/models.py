import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousFileOperation
from django.db import models
from django.utils import timezone
from django.utils.text import get_valid_filename

from logistics_performance.models import AuditSoftDeleteModel

User = get_user_model()


# ── Tracking Number Master ────────────────────────────────────────────────────

class TrackingMaster(AuditSoftDeleteModel):
    tracking_number = models.CharField(max_length=100, unique=True)
    description     = models.CharField(max_length=255, blank=True)
    is_active       = models.BooleanField(default=True)

    class Meta:
        ordering = ['tracking_number']
        verbose_name = 'Tracking Number'
        verbose_name_plural = 'Tracking Numbers'

    def __str__(self):
        return self.tracking_number


# ── PR Assignment ─────────────────────────────────────────────────────────────

class PRAssignment(AuditSoftDeleteModel):

    STATUS_CHOICES = [
        ('open',        'Open'),
        ('in_progress', 'In Progress'),
        ('po_raised',   'PO Raised'),
        ('grn_created', 'GRN Created'),
        ('closed',      'Closed'),
        ('overdue',     'Overdue'),
    ]
    VENDOR_CHOICES = [
        ('yes',     'Yes'),
        ('no',      'No'),
        ('pending', 'Pending'),
    ]
    GRN_CHOICES = [
        ('yes',     'Yes'),
        ('no',      'No'),
        ('partial', 'Partial'),
    ]
    REG_CHOICES = [
        ('regularized', 'Regularized'),
        ('negotiation', 'Negotiation'),
        ('na',          'N/A'),
    ]

    # ── Core fields — admin fills (3 required) ──────────────────────────────
    pr_number          = models.CharField(max_length=100, db_index=True)
    responsible_person = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='responsible_prs',
    )
    assigned_person    = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_prs',
    )
    assigned_by        = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pr_assignments_made',
    )
    assigned_at  = models.DateTimeField(auto_now_add=True)
    email_sent   = models.BooleanField(default=False)

    # ── Detail fields — member fills ────────────────────────────────────────
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    vertical          = models.CharField(max_length=150, blank=True)
    pr_received_date  = models.DateField(null=True, blank=True)
    pr_release_date   = models.DateField(null=True, blank=True)
    closed_at         = models.DateField(
        null=True, blank=True,
        help_text='Auto-set the day status is changed to Closed; used to freeze ageing/TAT.',
    )
    tracking_number   = models.ForeignKey(
        TrackingMaster, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pr_assignments',
    )
    no_of_line_items  = models.PositiveIntegerField(null=True, blank=True)
    description       = models.TextField(blank=True)
    requester_name    = models.CharField(max_length=200, blank=True)
    request_for_use   = models.TextField(blank=True)

    vendors_creation       = models.CharField(max_length=20, choices=VENDOR_CHOICES, blank=True)
    po_number              = models.CharField(max_length=100, blank=True)
    po_number_ageing_days  = models.PositiveIntegerField(null=True, blank=True)
    amount                 = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    regularized_negotiation = models.CharField(max_length=20, choices=REG_CHOICES, blank=True)

    grn_created     = models.CharField(max_length=20, choices=GRN_CHOICES, blank=True)
    advance_number  = models.CharField(max_length=100, blank=True)
    closure         = models.TextField(blank=True)
    remark          = models.TextField(blank=True)

    last_filled_by  = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pr_details_filled',
    )
    last_filled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-assigned_at']
        verbose_name = 'PR Assignment'
        verbose_name_plural = 'PR Assignments'

    def __str__(self):
        return self.pr_number

    def save(self, *args, **kwargs):
        """Keep closed_at and po_number_ageing_days in sync automatically:
        ageing runs from pr_release_date to today while open, and freezes at
        closed_at (stamped the day status becomes 'closed') once closed."""
        if self.status == 'closed':
            if not self.closed_at:
                self.closed_at = timezone.now().date()
        else:
            self.closed_at = None

        if self.pr_release_date:
            end_date = self.closed_at or timezone.now().date()
            self.po_number_ageing_days = max((end_date - self.pr_release_date).days, 0)
        else:
            self.po_number_ageing_days = None

        super().save(*args, **kwargs)

    @property
    def is_detail_filled(self):
        return bool(
            self.vertical or self.description or self.po_number or
            self.status not in ('open', '')
        )

    @property
    def fill_percent(self):
        """Rough % of detail fields filled (for progress indicator)."""
        fields = [
            self.vertical, self.pr_received_date, self.pr_release_date,
            self.tracking_number_id, self.no_of_line_items, self.description,
            self.requester_name, self.request_for_use, self.vendors_creation,
            self.po_number, self.amount, self.regularized_negotiation,
            self.grn_created, self.closure,
        ]
        filled = sum(1 for f in fields if f not in (None, '', 0))
        return int(filled / len(fields) * 100)

    @property
    def status_label(self):
        return dict(self.STATUS_CHOICES).get(self.status, self.status)

    @property
    def ageing_days(self):
        """Days from PR received date to today. None if received date not set."""
        if not self.pr_received_date:
            return None
        from django.utils import timezone
        return (timezone.now().date() - self.pr_received_date).days


# ── PR Documents ──────────────────────────────────────────────────────────────


def _safe_segment(value, fallback):
    """
    One path segment, built from text a user typed.

    get_valid_filename strips every path separator, so nothing it returns can
    climb out of its directory — but it *raises* rather than returning ""
    when the whole value sanitises away, which a PR number of "///" or "." or
    ".." does. Left unhandled that is a 500 on an upload, so the exception is
    treated as what it actually means: no usable name, use the fallback.
    """
    try:
        cleaned = get_valid_filename(value or '')
    except SuspiciousFileOperation:
        cleaned = ''
    return cleaned or fallback


def pr_document_path(instance, filename):
    """
    Where one uploaded document lands under MEDIA_ROOT.

    Two things this has to get right, both because every part of the name
    comes from whoever filled in the form:

      * the PR number is a free-text field and goes into a directory name, so
        it goes through _safe_segment — a PR number of "../../etc/passwd" must
        not be able to choose where the file is written. The separators are
        stripped rather than the dots, so that example becomes the single
        directory "....etcpasswd": ugly, and exactly one level under
        pr_documents/, which is the property that matters;
      * the stored name is prefixed with a UUID, so two people uploading
        "quotation.pdf" against the same PR get two files rather than the
        second silently replacing the first. The name the user actually saw is
        kept in ``original_name`` and is what the email and the download show.
    """
    folder = _safe_segment(instance.pr.pr_number, 'unfiled')
    safe = _safe_segment(filename, 'document')
    return 'pr_documents/{}/{}_{}'.format(folder, uuid.uuid4().hex[:12], safe)


# What a browser can render in place, and the type it must be served as.
#
# The type is taken from this table rather than from PRDocument.content_type,
# which is whatever the uploading browser claimed. That is fine for a download
# — the file lands on disk either way — but serving a file inline hands it to
# the renderer, and a stored type of "text/html" on a file named .png would be
# a stored XSS on our own origin. The extension decided the file was allowed
# in; the same extension decides how it is served back.
PR_DOC_PREVIEW_TYPES = {
    '.pdf':  'application/pdf',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.webp': 'image/webp',
    '.txt':  'text/plain; charset=utf-8',
}


class PRDocument(AuditSoftDeleteModel):
    """
    A supporting document attached to a PR when it is assigned — a quotation,
    a specification, an approval mail.

    These exist to travel with the assignment notification: the point of
    uploading them on the Assign screen is that the people being notified
    receive them, rather than being told a PR exists and having to go and ask
    what it is about. email_utils decides attach-or-link from the total size;
    the file itself always stays here, so the link keeps working after the
    mail has been read and archived.
    """

    pr = models.ForeignKey(
        PRAssignment, on_delete=models.CASCADE, related_name='documents',
    )
    file = models.FileField(upload_to=pr_document_path, max_length=500)
    # What the uploader called it. The stored name carries a UUID prefix so it
    # cannot collide, which makes it useless to show anyone.
    original_name = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=120, blank=True)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pr_documents_uploaded',
    )

    class Meta:
        ordering = ['id']
        verbose_name = 'PR Document'
        verbose_name_plural = 'PR Documents'

    def __str__(self):
        return '{} — {}'.format(self.pr.pr_number, self.original_name)

    @property
    def extension(self):
        name = self.original_name or ''
        return ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''

    @property
    def preview_type(self):
        """The type to serve this as for reading in place, or None."""
        return PR_DOC_PREVIEW_TYPES.get(self.extension)

    @property
    def is_previewable(self):
        """
        Whether a browser can draw this file.

        Office documents cannot be shown in place by any browser, and the
        viewers that can are hosted services that would need to fetch the
        file — which is impossible here, and would be wrong anyway, since
        these sit behind the login precisely so they do not leave. So the
        eye icon is not offered on them; the download is.
        """
        return self.preview_type is not None

    @property
    def is_image(self):
        return (self.preview_type or '').startswith('image/')

    @property
    def icon_class(self):
        """Font Awesome class for this file type — display only."""
        return {
            '.pdf':  'fa-file-pdf',
            '.doc':  'fa-file-word',   '.docx': 'fa-file-word',
            '.xls':  'fa-file-excel',  '.xlsx': 'fa-file-excel',
            '.csv':  'fa-file-excel',
            '.ppt':  'fa-file-powerpoint', '.pptx': 'fa-file-powerpoint',
            '.zip':  'fa-file-zipper',
            '.msg':  'fa-envelope',    '.eml':  'fa-envelope',
            '.txt':  'fa-file-lines',
            '.png':  'fa-file-image',  '.jpg':  'fa-file-image',
            '.jpeg': 'fa-file-image',  '.gif':  'fa-file-image',
            '.webp': 'fa-file-image',
        }.get(self.extension, 'fa-file')

    @property
    def size_label(self):
        """A size a person can read: 812 KB, 2.4 MB."""
        size = self.size_bytes or 0
        if size < 1024:
            return '{} B'.format(size)
        if size < 1024 * 1024:
            return '{:.0f} KB'.format(size / 1024)
        return '{:.1f} MB'.format(size / (1024 * 1024))


# ── PR Activity Log ───────────────────────────────────────────────────────────

class PRLog(AuditSoftDeleteModel):
    pr           = models.ForeignKey(PRAssignment, on_delete=models.CASCADE, related_name='logs')
    action       = models.CharField(max_length=100)
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    notes        = models.TextField(blank=True)
    timestamp    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f'{self.pr.pr_number} — {self.action}'
