"""
reconciliation/admin.py — Django admin for the master, the job log and access.
=============================================================================
The screen worth knowing about is **Module access**: pick a person, pick a
role. It writes ``Recon-Admin`` / ``Recon-User`` group membership and nothing
else, so it can never disagree with what ``roles.py`` enforces — there is one
record of who has access, not two.

``ReconRecord`` is registered read-only on purpose. A 204-column model makes a
change form nobody can use, and the module's own edit drawer already covers
every field with the same parsing and the same re-classification an import
uses; editing through the admin would skip both and leave a record whose
classification no longer matches its data. What the admin is good for here is
looking things up and spot-checking an import, so that is what it offers.
"""
from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import Group
from django.utils.html import format_html, format_html_join

from . import roles
from .models import (
    ReconBillingType,
    ReconciliationAccess,
    ReconImportJob,
    ReconRecord,
    ReconSalesType,
)

#: The choices offered on the access screen. "No access" is a real option
#: rather than a delete, because revoking a role is the common case and
#: deleting the user is never what was meant.
NO_ACCESS = ""
ROLE_CHOICES = (
    (NO_ACCESS, "No access"),
    (roles.ADMIN, "Admin — everything in the module"),
    (roles.USER, "User — read, correct and export"),
)


def _set_role(user, role):
    """Make ``role`` the only module role this user holds."""
    for group in Group.objects.filter(name__in=roles.ALL_GROUPS):
        if group.name == role:
            user.groups.add(group)
        else:
            user.groups.remove(group)


# ── access ───────────────────────────────────────────────────────────────────

class ReconciliationAccessForm(forms.ModelForm):
    """One field. Everything else about a user belongs on the user's own page."""

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        required=False,
        label="Module role",
        widget=forms.RadioSelect,
        help_text="Admin can do anything in the module. User can read, correct "
                  "individual documents and export.",
    )

    class Meta:
        model = ReconciliationAccess
        fields = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            held = set(self.instance.groups.filter(
                name__in=roles.ALL_GROUPS).values_list("name", flat=True))
            # Admin wins when somebody holds both, matching roles.role_of.
            current = roles.ADMIN if roles.ADMIN in held else (
                roles.USER if roles.USER in held else NO_ACCESS)
            self.fields["role"].initial = current

    def save(self, commit=True):
        user = super().save(commit=commit)
        _set_role(user, self.cleaned_data.get("role") or NO_ACCESS)
        return user


class ModuleRoleFilter(admin.SimpleListFilter):
    """Filter the user list by module role, including the people who have none."""

    title = "module role"
    parameter_name = "recon_role"

    def lookups(self, request, model_admin):
        return (
            (roles.ADMIN, "Admin"),
            (roles.USER, "User"),
            ("any", "Any access"),
            ("none", "No access"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value in roles.ALL_GROUPS:
            return queryset.filter(groups__name=value)
        if value == "any":
            return queryset.filter(groups__name__in=roles.ALL_GROUPS).distinct()
        if value == "none":
            return queryset.exclude(groups__name__in=roles.ALL_GROUPS)
        return queryset


@admin.register(ReconciliationAccess)
class ReconciliationAccessAdmin(admin.ModelAdmin):
    """Grant and revoke module access.

    Deliberately not an "add" screen: people come from the platform's own user
    list, and creating an account from a module's access page is how duplicate
    accounts get made. Find the person, set their role.
    """

    form = ReconciliationAccessForm
    list_display = ("username", "full_name", "email", "module_role",
                    "is_active", "last_login")
    list_filter = (ModuleRoleFilter, "is_active", "is_superuser")
    search_fields = ("username", "first_name", "last_name", "email")
    ordering = ("username",)
    list_select_related = False
    actions = ("grant_admin", "grant_user", "revoke_access")

    # The user's name and login belong to the platform, not to this module.
    readonly_fields = ("username", "full_name", "email", "capability_matrix")
    fields = ("username", "full_name", "email", "role", "capability_matrix")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("groups")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Removing module access is a role change, never a deleted account.
        return False

    @admin.display(description="Name", ordering="first_name")
    def full_name(self, obj):
        return obj.get_full_name() or "—"

    @admin.display(description="Module role")
    def module_role(self, obj):
        # Every format_html call passes its text as an argument rather than
        # baking it into the template string: the no-argument form is
        # deprecated in Django 6, and reaching for mark_safe instead would
        # trade a deprecation warning for a security-scanner finding.
        if obj.is_superuser:
            return format_html('<b>{}</b> <span style="color:#6b7a90">({})</span>',
                               "Admin", "superuser")
        held = {group.name for group in obj.groups.all()}
        if roles.ADMIN in held:
            return format_html("<b>{}</b>", "Admin")
        if roles.USER in held:
            return roles.ROLE_LABELS[roles.USER]
        return format_html('<span style="color:#9aa7b6">{}</span>', "No access")

    @admin.display(description="What each role can do")
    def capability_matrix(self, obj):
        """Rendered from roles.CAPABILITIES so the screen cannot promise
        something the module does not actually allow."""
        header = format_html(
            "<tr><th style='text-align:left;padding:4px 14px 4px 0'>{}</th>"
            "<th style='padding:4px 12px'>{}</th>"
            "<th style='padding:4px 12px'>{}</th></tr>",
            "Capability", roles.ROLE_LABELS[roles.ADMIN], roles.ROLE_LABELS[roles.USER])
        rows = format_html_join(
            "", "<tr><td style='padding:3px 14px 3px 0'>{}</td>"
                "<td style='text-align:center'>{}</td>"
                "<td style='text-align:center'>{}</td></tr>",
            ((label,
              "Yes" if roles.ADMIN in allowed else "—",
              "Yes" if roles.USER in allowed else "—")
             for label, allowed in roles.CAPABILITIES),
        )
        return format_html("<table>{}{}</table>", header, rows)

    # ── bulk actions ─────────────────────────────────────────────────────────

    def _apply(self, request, queryset, role, verb):
        count = 0
        for user in queryset:
            _set_role(user, role)
            count += 1
        self.message_user(
            request, f"{count} user(s) {verb}.", messages.SUCCESS)

    @admin.action(description="Grant Admin (everything in the module)")
    def grant_admin(self, request, queryset):
        self._apply(request, queryset, roles.ADMIN, "granted Admin")

    @admin.action(description="Grant User (read, correct, export)")
    def grant_user(self, request, queryset):
        self._apply(request, queryset, roles.USER, "granted User")

    @admin.action(description="Revoke module access")
    def revoke_access(self, request, queryset):
        self._apply(request, queryset, NO_ACCESS, "revoked")


# ── the billing-type master ──────────────────────────────────────────────────

@admin.register(ReconBillingType)
class ReconBillingTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "description", "doc_type", "gst_applicable",
                    "einv_applicable", "ewb_applicable", "health", "is_active")
    list_filter = ("doc_type", "health", "is_active",
                   "gst_applicable", "einv_applicable", "ewb_applicable")
    search_fields = ("code", "description", "final_remark")
    list_editable = ("is_active",)
    ordering = ("code",)


# ── the sales-type master ────────────────────────────────────────────────────

@admin.register(ReconSalesType)
class ReconSalesTypeAdmin(admin.ModelAdmin):
    """Which GSTR-1 section each ``GstTransactionType`` files under.

    Add a row when the export starts carrying a code the table does not have —
    an unknown code still classifies, it just falls back to the built-in rules
    until somebody says where it belongs.
    """

    list_display = ("code", "description", "gstr1_section", "is_export",
                    "is_b2c", "is_active")
    list_filter = ("gstr1_section", "is_export", "is_b2c", "is_active")
    search_fields = ("code", "description", "gstr1_section", "notes")
    list_editable = ("gstr1_section", "is_active")
    ordering = ("code",)
    fieldsets = (
        (None, {"fields": ("code", "description", "is_active")}),
        ("Where it files", {
            "fields": ("gstr1_section", "is_export", "is_b2c"),
            "description": "is_export makes this section win over the credit/debit-note "
                           "rule. is_b2c turns on the B2CL split — inter-state and above "
                           "Rs 2,50,000 becomes B2CL.",
        }),
        ("Notes", {"fields": ("notes",)}),
    )


# ── the import log ───────────────────────────────────────────────────────────

@admin.register(ReconImportJob)
class ReconImportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "state", "filename", "row_count",
                    "inserted", "updated", "skipped_manual", "created_at")
    list_filter = ("kind", "state", "mode")
    search_fields = ("filename", "message")
    date_hierarchy = "created_at"
    readonly_fields = tuple(
        field.name for field in ReconImportJob._meta.fields if field.name != "id"
    )

    def has_add_permission(self, request):
        return False


# ── the records themselves ───────────────────────────────────────────────────

@admin.register(ReconRecord)
class ReconRecordAdmin(admin.ModelAdmin):
    list_display = ("DocumentNumber", "DocumentType", "EntityGstin",
                    "GstDocumentDate", "GSTDocumentTaxableValue",
                    "EINVDocumentTaxableValue", "EWBDocumentTaxableValue",
                    "Presence", "ReasonRemark", "Health",
                    "ManualReason", "IsDeleted")
    list_filter = ("Health", "Presence", "IsDeleted", "ManualReason",
                   "DocumentType", "Segment", "Gstr1Section")
    search_fields = ("DocumentNumber", "EntityGstin", "EntityName",
                     "EINVIrn", "EwbNumber")
    date_hierarchy = "GstDocumentDate"
    ordering = ("DocumentNumber",)
    # 204 columns is far past what a change form can show usefully; the
    # module's own edit drawer is the way to change one.
    fields = ("DocumentNumber", "DocumentType", "EntityGstin", "EntityName",
              "GstDocumentDate", "GSTDocumentTaxableValue",
              "EINVDocumentTaxableValue", "EWBDocumentTaxableValue",
              "GSTDocumentValue", "EINVDocumentValue", "EWBDocumentValue",
              "Presence", "ReasonRemark", "Segment", "Gstr1Section", "Health",
              "MaxGap", "ManualReason", "CreatedBy", "SourceFile",
              "IsDeleted", "DeletedAt", "DeletedBy", "UpdatedAt")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
