"""Safe importer for the legacy Meril-V Supabase/Postgres dump.

The old database dump contains Supabase auth/session tables and RLS/function
DDL that must not be executed inside this Django app. This module parses only
data statements and maps public Meril-V rows into either current models or
legacy compatibility tables.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from merilv.models import (
    ActivityLog,
    BillingTypeMaster,
    CompanyMaster,
    CourierMaster,
    CustomerCourierMapping,
    CustomerMaster,
    Entity,
    FinishedDelivery,
    LegacyAuditLog,
    LegacyBranchTransferHistory,
    LegacyMessage,
    LegacyMessageReaction,
    LegacyOutboundDraft,
    LegacyOutboundHistory,
    LegacyPlantOrgMapping,
    LegacyProfile,
    LegacyScanHistory,
    LegacyUserData,
    LegacyUserPlantMapping,
    LegacyVehicle,
    MaterialMaster,
    OrganizationMaster,
    PlantMaster,
    SalesTypeMaster,
    StorageMaster,
    ToolSetting,
    UserPlantMapping,
)
from merilv.roles import ROLE_GROUPS


PUBLIC_TABLES = {
    "audit_logs",
    "billing_type_master",
    "bt_history",
    "company_master",
    "courier_master",
    "customer_courier_mapping",
    "customer_master",
    "finished_deliveries",
    "global_config",
    "material_master",
    "message_reactions",
    "messages",
    "mhpl_materials",
    "organization_master",
    "outbound_drafts",
    "outbound_history",
    "plant_master",
    "plant_org_mapping",
    "profiles",
    "sales_type_master",
    "scan_history",
    "storage_master",
    "user_data",
    "user_plant_mapping",
    "vehicle_master",
}

SUPPORTED_TABLES = PUBLIC_TABLES

LEGACY_COMPAT_MODELS = (
    LegacyAuditLog,
    LegacyBranchTransferHistory,
    LegacyMessage,
    LegacyMessageReaction,
    LegacyOutboundDraft,
    LegacyOutboundHistory,
    LegacyPlantOrgMapping,
    LegacyProfile,
    LegacyScanHistory,
    LegacyUserData,
    LegacyUserPlantMapping,
    LegacyVehicle,
)

ROLE_MAP = {
    "admin": "Meril-V Admin",
    "manager": "Meril-V Admin",
    "picker": "Meril-V Picker",
    "user": "Meril-V Picker",
    "viewer": "Meril-V Viewer",
}

INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(?:(?P<schema>\"[^\"]+\"|\w+)\.)?(?P<table>\"[^\"]+\"|\w+)\s*"
    r"\((?P<columns>.*?)\)\s+VALUES\s+(?P<values>.*)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
INSERT_TARGET_RE = re.compile(
    r"INSERT\s+INTO\s+(?:(?P<schema>\"[^\"]+\"|\w+)\.)?(?P<table>\"[^\"]+\"|\w+)\s*\(",
    re.IGNORECASE,
)
INSERT_HEADER_RE = re.compile(
    r"INSERT\s+INTO\s+(?:(?P<schema>\"[^\"]+\"|\w+)\.)?(?P<table>\"[^\"]+\"|\w+)\s*"
    r"\((?P<columns>.*?)\)\s+VALUES\s*(?P<tail>.*)$",
    re.IGNORECASE | re.DOTALL,
)
COPY_RE = re.compile(
    r"COPY\s+(?:(?P<schema>\"[^\"]+\"|\w+)\.)?(?P<table>\"[^\"]+\"|\w+)\s*"
    r"\((?P<columns>.*?)\)\s+FROM\s+stdin\s*;",
    re.IGNORECASE,
)
AUDIT_ROW_RE = re.compile(
    r"^\s*\('(?P<id>[^']*)',\s*(?P<actor_id>NULL|'[^']*'),\s*"
    r"'(?P<action>(?:''|[^'])*)',\s*'(?P<target_entity>(?:''|[^'])*)',\s*"
    r"(?P<target_id>NULL|'(?:''|[^'])*'),\s*'(?P<metadata>.*)',\s*"
    r"'(?P<created_at>[^']*)'\)[,;]?\s*$",
    re.DOTALL,
)


class LegacyImportError(Exception):
    """Raised when strict import mode turns row/report issues into failures."""


@dataclass
class TableReport:
    seen: int = 0
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    missing: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self):
        return {
            "seen": self.seen,
            "imported": self.imported,
            "updated": self.updated,
            "skipped": self.skipped,
            "missing": self.missing,
            "errors": self.errors,
        }


def _strip_identifier(value):
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"')
    return value


def parse_column_list(raw):
    columns = []
    token = []
    in_quote = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == '"':
            in_quote = not in_quote
            token.append(ch)
        elif ch == "," and not in_quote:
            columns.append(_strip_identifier("".join(token)))
            token = []
        else:
            token.append(ch)
        i += 1
    if token:
        columns.append(_strip_identifier("".join(token)))
    return columns


def _statement_complete(statement):
    in_quote = False
    i = 0
    while i < len(statement):
        ch = statement[i]
        if ch == "'":
            if in_quote and i + 1 < len(statement) and statement[i + 1] == "'":
                i += 2
                continue
            in_quote = not in_quote
        i += 1
    return not in_quote and statement.rstrip().endswith(";")


def _read_sql_string(value):
    prefix_len = 1
    if len(value) >= 2 and value[0] in {"E", "e"} and value[1] == "'":
        prefix_len = 2
    if not value[prefix_len - 1] == "'":
        return None, value

    out = []
    i = prefix_len
    while i < len(value):
        ch = value[i]
        if ch == "'":
            if i + 1 < len(value) and value[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), value[i + 1 :].strip()
        if prefix_len == 2 and ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), ""


def _parse_atom(value):
    value = value.strip()
    if not value:
        return ""
    upper = value.upper()
    if upper == "NULL":
        return None
    if upper == "TRUE":
        return True
    if upper == "FALSE":
        return False
    if value.startswith("'") or (len(value) > 1 and value[0] in {"E", "e"} and value[1] == "'"):
        text, suffix = _read_sql_string(value)
        if suffix and not suffix.startswith("::"):
            return value
        return text
    if "::" in value:
        return _parse_atom(value.split("::", 1)[0])
    try:
        if "." in value:
            return Decimal(value)
        return int(value)
    except (ValueError, InvalidOperation):
        return value


def _unquote_sql_value(value):
    value = value.strip()
    if value.upper() == "NULL":
        return None
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value.replace("''", "'")


def parse_audit_log_row(raw):
    match = AUDIT_ROW_RE.match(raw)
    if not match:
        return None
    return {
        "id": match.group("id"),
        "actor_id": _unquote_sql_value(match.group("actor_id")),
        "action": _unquote_sql_value(match.group("action")),
        "target_entity": _unquote_sql_value(match.group("target_entity")),
        "target_id": _unquote_sql_value(match.group("target_id")),
        "metadata": _unquote_sql_value(match.group("metadata")),
        "created_at": match.group("created_at"),
    }


def parse_values_rows(raw):
    rows = []
    row = []
    token = []
    in_quote = False
    depth = 0
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "'":
            token.append(ch)
            if in_quote and i + 1 < len(raw) and raw[i + 1] == "'":
                token.append(raw[i + 1])
                i += 2
                continue
            in_quote = not in_quote
        elif not in_quote and ch == "(":
            if depth:
                token.append(ch)
            depth += 1
        elif not in_quote and ch == ")":
            depth -= 1
            if depth == 0:
                row.append(_parse_atom("".join(token)))
                rows.append(row)
                row = []
                token = []
            else:
                token.append(ch)
        elif not in_quote and ch == "," and depth == 1:
            row.append(_parse_atom("".join(token)))
            token = []
        elif depth:
            token.append(ch)
        i += 1
    return rows


def parse_insert_statement(statement):
    match = INSERT_RE.match(statement.strip())
    if not match:
        return None
    schema = _strip_identifier(match.group("schema") or "public")
    table = _strip_identifier(match.group("table"))
    columns = parse_column_list(match.group("columns"))
    rows = [dict(zip(columns, values)) for values in parse_values_rows(match.group("values"))]
    return schema, table, rows


def parse_insert_target(statement):
    match = INSERT_TARGET_RE.match(statement.strip())
    if not match:
        return None
    return _strip_identifier(match.group("schema") or "public"), _strip_identifier(match.group("table"))


def parse_insert_header(statement):
    match = INSERT_HEADER_RE.match(statement.strip())
    if not match:
        return None
    return (
        _strip_identifier(match.group("schema") or "public"),
        _strip_identifier(match.group("table")),
        parse_column_list(match.group("columns")),
        match.group("tail").strip(),
    )


def _copy_unescape(value):
    if value == r"\N":
        return None
    return (
        value.replace(r"\t", "\t")
        .replace(r"\n", "\n")
        .replace(r"\r", "\r")
        .replace(r"\\", "\\")
    )


def iter_dump_rows(path):
    path = Path(path)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        pending_insert = []
        skipping_insert = False
        stream_insert = None
        copy_target = None
        copy_columns = None
        skip_copy = False
        for line in handle:
            if copy_target:
                raw = line.rstrip("\r\n")
                if raw == r"\.":
                    copy_target = None
                    copy_columns = None
                    skip_copy = False
                    continue
                if skip_copy:
                    continue
                values = [_copy_unescape(part) for part in raw.split("\t")]
                yield copy_target[0], copy_target[1], dict(zip(copy_columns, values))
                continue

            if skipping_insert:
                if line.rstrip().endswith(";"):
                    skipping_insert = False
                continue

            if stream_insert:
                schema, table, columns = stream_insert
                if table == "audit_logs":
                    audit_row = parse_audit_log_row(line)
                    if audit_row:
                        yield schema, table, audit_row
                    else:
                        for values in parse_values_rows(line):
                            yield schema, table, dict(zip(columns, values))
                else:
                    for values in parse_values_rows(line):
                        yield schema, table, dict(zip(columns, values))
                if line.rstrip().endswith(";"):
                    stream_insert = None
                continue

            stripped = line.strip()

            if pending_insert:
                pending_insert.append(line)
                statement = "".join(pending_insert)
                if _statement_complete(statement):
                    target = parse_insert_target(statement)
                    if target and (target[0] != "public" or target[1] not in SUPPORTED_TABLES):
                        yield target[0], target[1], None
                    else:
                        parsed = parse_insert_statement(statement)
                        if parsed:
                            schema, table, rows = parsed
                            for row in rows:
                                yield schema, table, row
                    pending_insert = []
                continue

            if not stripped or stripped.startswith("--"):
                continue

            copy_match = COPY_RE.match(stripped)
            if copy_match:
                schema = _strip_identifier(copy_match.group("schema") or "public")
                table = _strip_identifier(copy_match.group("table"))
                copy_target = (schema, table)
                copy_columns = parse_column_list(copy_match.group("columns"))
                skip_copy = schema != "public" or table not in SUPPORTED_TABLES
                if skip_copy:
                    yield schema, table, None
                continue

            if stripped.upper().startswith("INSERT INTO"):
                target = parse_insert_target(stripped)
                if target and (target[0] != "public" or target[1] not in SUPPORTED_TABLES):
                    yield target[0], target[1], None
                    skipping_insert = not stripped.endswith(";")
                    continue
                header = parse_insert_header(line)
                if header:
                    schema, table, columns, tail = header
                    if tail:
                        for values in parse_values_rows(tail):
                            yield schema, table, dict(zip(columns, values))
                    if not line.rstrip().endswith(";"):
                        stream_insert = (schema, table, columns)
                    continue
                pending_insert = [line]
                statement = "".join(pending_insert)
                if _statement_complete(statement):
                    target = parse_insert_target(statement)
                    if target and (target[0] != "public" or target[1] not in SUPPORTED_TABLES):
                        yield target[0], target[1], None
                    else:
                        parsed = parse_insert_statement(statement)
                        if parsed:
                            schema, table, rows = parsed
                            for row in rows:
                                yield schema, table, row
                    pending_insert = []


def discover_public_schema_tables(schema_path):
    if not schema_path:
        return set()
    path = Path(schema_path)
    if not path.exists():
        raise LegacyImportError(f"Schema file not found: {schema_path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        match.group("table")
        for match in re.finditer(
            r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"public"\."(?P<table>[^"]+)"',
            text,
            re.IGNORECASE,
        )
    }


def _clean(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _upper(value):
    return _clean(value).upper()


def _clip(value, limit):
    value = _clean(value)
    return value[:limit]


def _json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    if text.startswith("{") and text.endswith("}") and not text.startswith('{"'):
        inner = text[1:-1]
        return [] if not inner else [part.strip().strip('"') for part in inner.split(",")]
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _datetime(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value
    parsed = parse_datetime(str(value))
    if parsed and timezone.is_naive(parsed):
        return timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _uuid(value):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _int(value, default=0):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class LegacyDumpImporter:
    def __init__(self, *, dry_run=False, strict=False, stdout=None):
        self.dry_run = dry_run
        self.strict = strict
        self.stdout = stdout
        self.tables = defaultdict(TableReport)
        self.skipped_schemas = defaultdict(int)
        self.unsupported_tables = defaultdict(int)
        self.present_data_tables = set()
        self.schema_tables = set()

    def import_files(self, *, schema_path=None, data_path=None, truncate_merilv=False):
        if schema_path:
            self.schema_tables = discover_public_schema_tables(schema_path)
        if truncate_merilv:
            self._truncate_legacy_tables()

        call_command("seed_merilv", verbosity=0)
        for schema, table, row in iter_dump_rows(data_path):
            if schema != "public":
                self.skipped_schemas[schema] += 1
                continue
            self.present_data_tables.add(table)
            if table not in SUPPORTED_TABLES:
                self.unsupported_tables[table] += 1
                continue
            self._import_row(table, row)

        for table in sorted(PUBLIC_TABLES):
            if table not in self.present_data_tables:
                self.tables[table].missing = True

        report = self.report()
        if self.strict:
            errors = [
                f"{table}: {error}"
                for table, stats in report["tables"].items()
                for error in stats["errors"]
            ]
            if errors or report["unsupported_tables"]:
                raise LegacyImportError("; ".join(errors) or "Unsupported public tables were present.")
        return report

    def report(self):
        return {
            "dry_run": self.dry_run,
            "schema_tables": sorted(self.schema_tables),
            "data_tables": sorted(self.present_data_tables),
            "tables": {table: self.tables[table].as_dict() for table in sorted(PUBLIC_TABLES)},
            "skipped_schemas": dict(sorted(self.skipped_schemas.items())),
            "unsupported_tables": dict(sorted(self.unsupported_tables.items())),
        }

    def _truncate_legacy_tables(self):
        if self.dry_run:
            return
        for model in LEGACY_COMPAT_MODELS:
            model.objects.all().delete()

    def _import_row(self, table, row):
        stats = self.tables[table]
        stats.seen += 1
        try:
            handler = getattr(self, f"_import_{table}")
            created = handler(row)
        except Exception as exc:
            stats.skipped += 1
            stats.errors.append(str(exc))
            if self.strict:
                raise
            return
        if created is True:
            stats.imported += 1
        elif created is False:
            stats.updated += 1
        else:
            stats.skipped += 1

    def _upsert(self, model, lookup, defaults):
        exists = model.objects.filter(**lookup).exists()
        if self.dry_run:
            return not exists
        _, created = model.objects.update_or_create(**lookup, defaults=defaults)
        return created

    def _entity(self, code):
        code = _upper(code or "MLSIPL")
        if self.dry_run:
            entity = Entity.objects.filter(code=code).first()
            if entity:
                return entity
            return Entity(code=code, name=code)
        entity, _ = Entity.objects.get_or_create(code=code, defaults={"name": code})
        return entity

    def _company(self, code, name=""):
        code = _upper(code)
        if not code:
            return None
        if not self.dry_run:
            return CompanyMaster.objects.get_or_create(
                company_code=code,
                defaults={"company_name": _clip(name or code, 200)},
            )[0]
        return CompanyMaster(company_code=code, company_name=_clip(name or code, 200))

    def _organization(self, code, name="", company_code=""):
        code = _upper(code)
        if not code:
            return None
        company = self._company(company_code, company_code)
        if not self.dry_run:
            return OrganizationMaster.objects.get_or_create(
                org_code=code,
                defaults={"org_name": _clip(name or code, 200), "company": company},
            )[0]
        return OrganizationMaster(org_code=code, org_name=_clip(name or code, 200), company=company)

    def _plant(self, code, name="", org_code="", company_code=""):
        code = _upper(code)
        if not code:
            return None
        org = self._organization(org_code, org_code, company_code) if org_code else None
        if not self.dry_run:
            return PlantMaster.objects.get_or_create(
                plant_code=code,
                defaults={"plant_name": _clip(name or code, 200), "organization": org},
            )[0]
        return PlantMaster(plant_code=code, plant_name=_clip(name or code, 200), organization=org)

    def _import_profiles(self, row):
        legacy_id = _uuid(row.get("id"))
        if not legacy_id:
            raise ValueError("profiles row missing valid id")
        user = self._profile_user(row)
        defaults = {
            "email": _clip(row.get("email"), 254),
            "username": _clip(row.get("username"), 150),
            "full_name": _clip(row.get("full_name"), 200),
            "role": _clip(row.get("role") or "user", 40),
            "avatar_url": _clean(row.get("avatar_url")),
            "manager_legacy_id": _uuid(row.get("manager_id")),
            "permissions": _json(row.get("permissions"), {}),
            "raw_data": row,
            "imported_user": user if getattr(user, "pk", None) else None,
            "updated_at": _datetime(row.get("updated_at")),
            "last_seen": _datetime(row.get("last_seen")),
        }
        return self._upsert(LegacyProfile, {"legacy_id": legacy_id}, defaults)

    def _profile_user(self, row):
        email = _clean(row.get("email")).lower()
        username = _clip(row.get("username") or (email.split("@")[0] if email else row.get("id")), 150)
        full_name = _clean(row.get("full_name"))
        role = ROLE_MAP.get(_clean(row.get("role")).lower(), "Meril-V Picker")

        existing = None
        if email:
            existing = User.objects.filter(email__iexact=email).first()
        if not existing and username:
            existing = User.objects.filter(username=username).first()
        if self.dry_run:
            return existing or User(username=username, email=email)

        user = existing
        if not user:
            unique_username = username or email or str(row.get("id"))
            base = unique_username[:145]
            suffix = 1
            while User.objects.filter(username=unique_username).exists():
                unique_username = f"{base}-{suffix}"[:150]
                suffix += 1
            user = User(username=unique_username, email=email)
            user.set_unusable_password()
        elif email and user.email != email:
            user.email = email

        if full_name:
            parts = full_name.split(None, 1)
            user.first_name = parts[0][:150]
            user.last_name = parts[1][:150] if len(parts) > 1 else ""
        user.save()

        groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLE_GROUPS}
        user.groups.remove(*groups.values())
        user.groups.add(groups[role])
        return user

    def _import_audit_logs(self, row):
        legacy_id = _uuid(row.get("id"))
        if not legacy_id:
            raise ValueError("audit_logs row missing valid id")
        if self.dry_run:
            return self._upsert(LegacyAuditLog, {"legacy_id": legacy_id}, {})
        created_at = _datetime(row.get("created_at"))
        existing = LegacyAuditLog.objects.filter(legacy_id=legacy_id).first()
        activity = existing.activity if existing else None
        if not self.dry_run and activity is None:
            activity = ActivityLog.objects.create(
                actor=None,
                action=_clip(row.get("action"), 100) or "legacy_import",
                target_type=_clip(row.get("target_entity"), 100),
                target_id=_clip(row.get("target_id"), 100),
                metadata=_json(row.get("metadata"), {}),
            )
            if created_at:
                ActivityLog.objects.filter(pk=activity.pk).update(created_at=created_at)
        defaults = {
            "actor_legacy_id": _uuid(row.get("actor_id")),
            "action": _clip(row.get("action"), 100) or "legacy_import",
            "target_entity": _clip(row.get("target_entity"), 100),
            "target_id": _clip(row.get("target_id"), 100),
            "metadata": _json(row.get("metadata"), {}),
            "raw_data": row,
            "activity": activity,
            "created_at": created_at,
        }
        return self._upsert(LegacyAuditLog, {"legacy_id": legacy_id}, defaults)

    def _import_global_config(self, row):
        key = _clip(row.get("key"), 100)
        if not key:
            raise ValueError("global_config row missing key")
        return self._upsert(
            ToolSetting,
            {"key": key},
            {"value": _json(row.get("value"), {}), "description": "Imported from legacy global_config"},
        )

    def _import_company_master(self, row):
        code = _upper(row.get("company_code"))
        if not code:
            raise ValueError("company_master row missing company_code")
        return self._upsert(
            CompanyMaster,
            {"company_code": code},
            {"company_name": _clip(row.get("company_name") or code, 200)},
        )

    def _import_organization_master(self, row):
        code = _upper(row.get("org_code"))
        if not code:
            raise ValueError("organization_master row missing org_code")
        company = self._company(row.get("company_code"), row.get("company_code"))
        return self._upsert(
            OrganizationMaster,
            {"org_code": code},
            {"org_name": _clip(row.get("org_name") or code, 200), "company": company},
        )

    def _import_plant_master(self, row):
        code = _upper(row.get("plant_code"))
        if not code:
            raise ValueError("plant_master row missing plant_code")
        org = self._organization(row.get("org_code"), row.get("org_code"), row.get("company_code"))
        return self._upsert(
            PlantMaster,
            {"plant_code": code},
            {"plant_name": _clip(row.get("plant_name") or code, 200), "organization": org},
        )

    def _import_storage_master(self, row):
        code = _upper(row.get("storage_code"))
        plant = self._plant(row.get("plant_code"), row.get("plant_code"))
        if not code or not plant:
            raise ValueError("storage_master row missing storage_code or plant_code")
        return self._upsert(
            StorageMaster,
            {"storage_code": code, "plant": plant},
            {"storage_name": _clip(row.get("storage_name") or code, 200)},
        )

    def _import_billing_type_master(self, row):
        code = _clip(row.get("billing_type"), 20)
        if not code:
            raise ValueError("billing_type_master row missing billing_type")
        return self._upsert(
            BillingTypeMaster,
            {"billing_type": code},
            {"billing_description": _clip(row.get("billing_description"), 200)},
        )

    def _import_sales_type_master(self, row):
        code = _clip(row.get("sales_type"), 20)
        if not code:
            raise ValueError("sales_type_master row missing sales_type")
        return self._upsert(
            SalesTypeMaster,
            {"sales_type": code},
            {"sales_description": _clip(row.get("sales_description"), 200)},
        )

    def _import_material_master(self, row):
        material = _clip(row.get("material"), 100)
        if not material:
            raise ValueError("material_master row missing material")
        entity = self._entity(row.get("entity") or "MHPL")
        return self._upsert(
            MaterialMaster,
            {"entity": entity, "scan_code": material},
            {
                "material_code": material,
                "material_description": _clip(row.get("description"), 400),
                "raw_data": row,
            },
        )

    def _import_mhpl_materials(self, row):
        material = _clip(row.get("material"), 100)
        if not material:
            raise ValueError("mhpl_materials row missing material")
        entity = self._entity("MHPL")
        return self._upsert(
            MaterialMaster,
            {"entity": entity, "scan_code": material},
            {
                "material_code": material,
                "material_description": _clip(row.get("material_description"), 400),
                "gtin": _clip(row.get("ean_upc"), 20),
                "material_group": _clip(row.get("material_group"), 100),
                "raw_data": row,
            },
        )

    def _import_customer_master(self, row):
        code = _clip(row.get("customer_code"), 50)
        if not code:
            raise ValueError("customer_master row missing customer_code")
        entity = self._entity(row.get("entity") or "MLSIPL")
        address = _clean(row.get("address")) or " ".join(
            part for part in (
                _clean(row.get("street")),
                _clean(row.get("street2")),
                _clean(row.get("street3")),
                _clean(row.get("street4")),
                _clean(row.get("street5")),
            )
            if part
        )
        blocked_reason = _clean(row.get("blocked_reason")) or " ".join(
            part for part in (
                _clean(row.get("central_order_block")),
                _clean(row.get("order_block_sales_area")),
                _clean(row.get("central_delivery_block")),
                _clean(row.get("delivery_block_sales_area")),
                _clean(row.get("central_billing_block")),
                _clean(row.get("billing_block_sales_area")),
            )
            if part
        )
        return self._upsert(
            CustomerMaster,
            {"customer_code": code},
            {
                "customer_name": _clip(row.get("customer_name") or row.get("name1") or row.get("search_term"), 300),
                "entity": entity,
                "company_code": _clip(row.get("company_code"), 20),
                "courier_partner": _clip(row.get("courier_partner"), 150),
                "address": address,
                "city": _clip(row.get("city") or row.get("district"), 120),
                "state": _clip(row.get("state") or row.get("region"), 120),
                "country": _clip(row.get("country"), 120),
                "pincode": _clip(row.get("pincode") or row.get("postal_code"), 20),
                "gstin": _clip(row.get("gstin") or row.get("gst_no"), 30),
                "email": _clip(row.get("email"), 254),
                "mobile": _clip(row.get("mobile") or row.get("telephone1"), 40),
                "contact_person": _clip(row.get("contact_person"), 150),
                "is_blocked": bool(row.get("is_blocked")),
                "blocked_reason": _clip(blocked_reason, 300),
                "raw_data": row,
            },
        )

    def _import_courier_master(self, row):
        """Carry the dispatching plant across, not just the name.

        ``plant`` decides whether a courier can be auto-fetched at all: with it
        blank the plant tier in ``_resolve_couriers_for`` never fires and the
        Finish Delivery modal fills in nothing. Dropping it — along with the
        vehicle type, GSTIN and mode the legacy export also carries — meant a
        migrated courier arrived unusable for the one job it has.

        Aliases are accepted because the legacy export's own column names
        drift: "plant", "plant_code" and "werks" are one field in SAP.
        """
        name = _clip(row.get("courier_name"), 150)
        if not name:
            raise ValueError("courier_master row missing courier_name")

        def first(*keys, limit):
            for key in keys:
                value = _clip(row.get(key), limit)
                if value:
                    return value
            return ""

        return self._upsert(
            CourierMaster,
            {"courier_name": name, "courier_number": _clip(row.get("courier_number"), 100)},
            {
                "plant": first("plant", "plant_code", "werks", limit=20),
                "vehicle_number": _clip(row.get("vehicle_number"), 60),
                "vehicle_type": first("vehicle_type", "vehicle_category", limit=60),
                "gstin": first("gstin", "gst_no", "gst_number", limit=20),
                # This branch's field is mode_of_transit; smith's lineage calls
                # the same thing default_mode_of_transit. Both spellings are
                # accepted as INPUT so an export written for either migrates.
                "mode_of_transit": first(
                    "mode_of_transit", "default_mode_of_transit", "mode", limit=20,
                ),
                "is_active": True,
            },
        )

    def _import_customer_courier_mapping(self, row):
        customer_code = _clip(row.get("customer_code"), 50)
        plant = _clip(row.get("plant"), 20)
        if not customer_code:
            raise ValueError("customer_courier_mapping row missing customer_code")
        return self._upsert(
            CustomerCourierMapping,
            {"customer_code": customer_code, "entity": None, "plant": plant},
            {
                "courier_name": _clip(row.get("courier_partner"), 150),
                "courier_number": _clip(row.get("courier_number"), 100),
            },
        )

    def _import_finished_deliveries(self, row):
        delivery_no = _clip(row.get("delivery_no"), 50)
        if not delivery_no:
            raise ValueError("finished_deliveries row missing delivery_no")
        entity = self._entity(row.get("entity") or "MLSIPL")
        picker = None
        picker_email = _clean(row.get("picker_email")).lower()
        if picker_email:
            picker = User.objects.filter(email__iexact=picker_email).first()
        return self._upsert(
            FinishedDelivery,
            {"delivery_no": delivery_no, "entity": entity},
            {
                "customer_code": _clip(row.get("customer_code"), 50),
                "customer_name": _clip(row.get("customer_name"), 300),
                "courier_partner": _clip(row.get("courier_partner"), 150),
                "courier_number": _clip(row.get("courier_number"), 100),
                "vehicle_number": _clip(row.get("vehicle_number"), 60),
                "docket_no": _clip(row.get("docket_no"), 100),
                "weight_kg": _decimal(row.get("weight_kg")),
                "box_count": max(_int(row.get("box_count")), 0),
                "boxes_used": _clip(row.get("boxes_used"), 200),
                "is_partial": _clean(row.get("status")).lower() not in {"", "complete"},
                "payload": row,
                "picker": picker,
            },
        )

    def _import_outbound_drafts(self, row):
        email = _clip(row.get("user") or row.get("user_email"), 254).lower()
        if not email:
            raise ValueError("outbound_drafts row missing user")
        return self._upsert(
            LegacyOutboundDraft,
            {"user_email": email},
            {"last_updated": _datetime(row.get("last_updated")), "state": _json(row.get("state"), {})},
        )

    def _import_outbound_history(self, row):
        legacy_id = _clip(row.get("id"), 100)
        if not legacy_id:
            raise ValueError("outbound_history row missing id")
        return self._upsert(
            LegacyOutboundHistory,
            {"legacy_id": legacy_id},
            {
                "timestamp": _datetime(row.get("timestamp")),
                "user_email": _clip(row.get("user") or row.get("user_email"), 254).lower(),
                "mode": _clip(row.get("mode"), 80),
                "total": _int(row.get("total"), None),
                "data": _json(row.get("data"), {}),
            },
        )

    def _import_scan_history(self, row):
        legacy_id = _clip(row.get("id"), 100)
        if not legacy_id:
            raise ValueError("scan_history row missing id")
        return self._upsert(
            LegacyScanHistory,
            {"legacy_id": legacy_id},
            {
                "timestamp": _datetime(row.get("timestamp")),
                "user_email": _clip(row.get("user") or row.get("user_email"), 254).lower(),
                "mode": _clip(row.get("mode"), 80),
                "total": _int(row.get("total"), None),
                "data": _json(row.get("data"), {}),
            },
        )

    def _import_bt_history(self, row):
        legacy_id = _uuid(row.get("id"))
        if not legacy_id:
            raise ValueError("bt_history row missing valid id")
        return self._upsert(
            LegacyBranchTransferHistory,
            {"legacy_id": legacy_id},
            {
                "created_at": _datetime(row.get("created_at")),
                "created_by": _clip(row.get("created_by"), 254),
                "scanner_type": _clip(row.get("scanner_type"), 40),
                "receiver_code": _clip(row.get("receiver_code"), 80),
                "plant_name": _clip(row.get("plant_name"), 200),
                "total_materials": _int(row.get("total_materials")),
                "total_picked": _int(row.get("total_picked")),
                "total_pending": _int(row.get("total_pending")),
                "total_boxes": _int(row.get("total_boxes")),
                "color_zones": _json(row.get("color_zones"), []),
                "remarks": _clean(row.get("remarks")),
                "plan_data": _json(row.get("plan_data"), []),
                "scan_data": _json(row.get("scan_data"), []),
                "box_data": _json(row.get("box_data"), []),
            },
        )

    def _import_messages(self, row):
        legacy_id = _uuid(row.get("id"))
        if not legacy_id:
            raise ValueError("messages row missing valid id")
        return self._upsert(
            LegacyMessage,
            {"legacy_id": legacy_id},
            {
                "context_id": _clip(row.get("context_id"), 200),
                "sender_legacy_id": _uuid(row.get("sender_id")),
                "sender_name": _clip(row.get("sender_name"), 200),
                "cipher": _clean(row.get("cipher")),
                "status": _clip(row.get("status"), 40),
                "message_type": _clip(row.get("message_type"), 40),
                "file_url": _clean(row.get("file_url")),
                "file_name": _clip(row.get("file_name"), 300),
                "read_by": _json(row.get("read_by"), []),
                "created_at": _datetime(row.get("created_at")),
            },
        )

    def _import_message_reactions(self, row):
        legacy_id = _uuid(row.get("id"))
        if not legacy_id:
            raise ValueError("message_reactions row missing valid id")
        return self._upsert(
            LegacyMessageReaction,
            {"legacy_id": legacy_id},
            {
                "message_legacy_id": _uuid(row.get("message_id")),
                "user_legacy_id": _uuid(row.get("user_id")),
                "emoji": _clip(row.get("emoji"), 40),
                "created_at": _datetime(row.get("created_at")),
            },
        )

    def _import_user_data(self, row):
        legacy_id = _uuid(row.get("id"))
        if not legacy_id:
            raise ValueError("user_data row missing valid id")
        return self._upsert(
            LegacyUserData,
            {"legacy_id": legacy_id},
            {
                "user_legacy_id": _uuid(row.get("user_id")),
                "tool_name": _clip(row.get("tool_name"), 120),
                "key": _clip(row.get("key"), 200),
                "file_data": _json(row.get("file_data"), {}),
                "value": _json(row.get("value"), {}),
                "created_at": _datetime(row.get("created_at")),
                "updated_at": _datetime(row.get("updated_at")),
            },
        )

    def _import_vehicle_master(self, row):
        legacy_id = _uuid(row.get("id"))
        if not legacy_id:
            raise ValueError("vehicle_master row missing valid id")
        return self._upsert(
            LegacyVehicle,
            {"legacy_id": legacy_id},
            {
                "vehicle_number": _clip(row.get("vehicle_number"), 80),
                "vehicle_type": _clip(row.get("vehicle_type"), 120),
                "courier_code": _clip(row.get("courier_code"), 120),
                "owner_name": _clip(row.get("owner_name"), 200),
                "capacity_kg": _decimal(row.get("capacity_kg")),
                "is_active": row.get("is_active") is not False,
                "notes": _clean(row.get("notes")),
                "created_at": _datetime(row.get("created_at")),
            },
        )

    def _import_plant_org_mapping(self, row):
        legacy_id = _int(row.get("id"), None)
        if legacy_id is None:
            raise ValueError("plant_org_mapping row missing id")
        return self._upsert(
            LegacyPlantOrgMapping,
            {"legacy_id": legacy_id},
            {
                "plant_code": _upper(row.get("plant_code")),
                "org_code": _upper(row.get("org_code")),
                "is_primary": bool(row.get("is_primary")),
                "created_at": _datetime(row.get("created_at")),
            },
        )

    def _import_user_plant_mapping(self, row):
        legacy_id = _int(row.get("id"), None)
        user_legacy_id = _uuid(row.get("user_id"))
        if legacy_id is None or not user_legacy_id:
            raise ValueError("user_plant_mapping row missing id or user_id")
        plant = self._plant(row.get("plant_code"), row.get("plant_code"))
        profile = LegacyProfile.objects.filter(legacy_id=user_legacy_id).select_related("imported_user").first()
        if profile and profile.imported_user and plant and not self.dry_run:
            UserPlantMapping.objects.get_or_create(user=profile.imported_user, plant=plant)
        return self._upsert(
            LegacyUserPlantMapping,
            {"legacy_id": legacy_id},
            {
                "user_legacy_id": user_legacy_id,
                "plant_code": _upper(row.get("plant_code")),
                "created_at": _datetime(row.get("created_at")),
            },
        )


def import_legacy_dump(*, schema_path=None, data_path=None, report_path=None, dry_run=False, truncate_merilv=False, strict=False):
    if not data_path:
        raise LegacyImportError("A data SQL path is required.")
    if not Path(data_path).exists():
        raise LegacyImportError(f"Data file not found: {data_path}")
    importer = LegacyDumpImporter(dry_run=dry_run, strict=strict)
    with transaction.atomic():
        report = importer.import_files(
            schema_path=schema_path,
            data_path=data_path,
            truncate_merilv=truncate_merilv,
        )
        if dry_run:
            transaction.set_rollback(True)
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
