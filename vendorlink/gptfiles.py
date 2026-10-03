"""Attachment handling for Meril GPT.

Turns an uploaded file into something the model can actually read. Two paths,
because ``gpt-4o`` treats them differently:

* **Images** go to the model as-is, base64-encoded into an ``image_url`` content
  part. The model does the reading (vision).
* **Everything else** is text-extracted here and inlined into the prompt. The
  Chat Completions API does *not* parse a PDF or a spreadsheet for you — hand it
  a base64 PDF and it sees an opaque blob — so extraction is this module's job.

**No new dependencies.** ``pypdf``, ``openpyxl``, ``xlrd`` and ``Pillow`` are
already installed. ``python-docx`` and ``python-pptx`` are not, and rather than
add them (and a container rebuild) this reads DOCX/PPTX with ``zipfile`` plus the
stdlib XML parser — both formats are ZIP archives of XML, and the text lives in
``<w:t>`` / ``<a:t>`` nodes. Less nuanced than the dedicated libraries (no table
structure, no speaker notes ordering guarantees) but it recovers the prose,
which is what the model needs.

**Why the character caps matter.** A 30 MB PDF can hold tens of thousands of
pages. Extracted whole, that is millions of tokens: the request would be
rejected by the API, or accepted at an eye-watering cost. Every file is
truncated to :data:`PER_FILE_CHARS` and the batch to :data:`TOTAL_CHARS`, and
the truncation is *stated in the prompt* so the model knows it is reading an
excerpt rather than silently reasoning over a fragment it thinks is complete.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Hard limits the client asked for. Enforced server-side — the file input's
#: `accept` and `multiple` attributes are advisory and trivially bypassed.
MAX_FILES = 4
MAX_BYTES_PER_FILE = 30 * 1024 * 1024          # 30 MB
MAX_BYTES_TOTAL = MAX_FILES * MAX_BYTES_PER_FILE

#: Extraction budget. ~4 chars/token, so 40k chars is roughly 10k tokens per
#: file and 120k chars roughly 30k tokens for the batch — which leaves room for
#: the request/bid payload ask_meril_gpt already sends inside gpt-4o's window.
PER_FILE_CHARS = 40_000
TOTAL_CHARS = 120_000

#: Images the vision model accepts. WEBP and GIF included because the API takes
#: them; TIFF and BMP are not, so they are rejected with a clear reason rather
#: than uploaded and silently ignored.
IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

#: Everything text-extractable, mapped to the extractor that handles it.
#: Keys are lowercase extensions including the dot.
DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx", ".xlsm",
    ".xls",
    ".csv", ".tsv",
    ".txt", ".md", ".log", ".rst",
    ".json",
)

ALLOWED_EXTENSIONS = tuple(IMAGE_EXTENSIONS) + DOCUMENT_EXTENSIONS

#: Formats people reach for that this cannot read, with the reason. Named
#: explicitly so the error says *why* instead of a generic "unsupported".
KNOWN_UNSUPPORTED = {
    ".doc": "legacy Word (.doc) — re-save as .docx",
    ".ppt": "legacy PowerPoint (.ppt) — re-save as .pptx",
    ".pages": "Apple Pages — export as PDF or .docx",
    ".key": "Apple Keynote — export as PDF or .pptx",
    ".numbers": "Apple Numbers — export as .xlsx or .csv",
    ".zip": "archives are not unpacked — attach the files inside",
    ".rar": "archives are not unpacked — attach the files inside",
    ".7z": "archives are not unpacked — attach the files inside",
    ".heic": "HEIC images — export as PNG or JPEG",
    ".tif": "TIFF images — export as PNG or JPEG",
    ".tiff": "TIFF images — export as PNG or JPEG",
    ".bmp": "BMP images — export as PNG or JPEG",
    ".svg": "SVG is markup, not a readable image — export as PNG",
}


class AttachmentError(ValueError):
    """One attachment could not be accepted. The message is operator-facing."""


@dataclass
class Attachment:
    """One accepted upload, ready to hand to the model."""

    name: str
    size: int
    kind: str                       # "image" | "text"
    mime: str = ""
    text: str = ""
    data_url: str = ""
    truncated: bool = False
    notes: list = field(default_factory=list)

    @property
    def pretty_size(self) -> str:
        mb = self.size / (1024 * 1024)
        return f"{mb:.1f} MB" if mb >= 0.1 else f"{max(1, self.size // 1024)} KB"


# ── Extension helpers ────────────────────────────────────────────────────────
def _extension(name: str) -> str:
    lowered = (name or "").lower()
    index = lowered.rfind(".")
    return lowered[index:] if index != -1 else ""


def _clean(text: str) -> str:
    """Collapse the whitespace that extraction leaves behind.

    PDF and OOXML extraction both emit runs of blank lines and stray spaces; left
    in, they are pure token cost with no information.
    """
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Extractors ───────────────────────────────────────────────────────────────
def _extract_pdf(blob: bytes) -> tuple:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(blob))
    except Exception as exc:
        raise AttachmentError(f"could not be read as a PDF ({exc})") from exc

    if getattr(reader, "is_encrypted", False):
        # An empty-password decrypt covers the common "protected but not
        # really" case; a genuinely locked file is reported, not silently
        # returned as zero pages of text.
        try:
            reader.decrypt("")
        except Exception:
            raise AttachmentError("is password-protected — remove the password and retry")

    parts, notes = [], []
    used = 0
    for number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            notes.append(f"page {number} could not be extracted")
            continue
        parts.append(page_text)
        used += len(page_text)
        if used >= PER_FILE_CHARS:
            notes.append(f"truncated at page {number} of {len(reader.pages)}")
            break

    text = _clean("\n\n".join(parts))
    if not text:
        # A scanned PDF is images-in-a-wrapper. Saying so is far more useful
        # than handing the model an empty document.
        notes.append(
            "no selectable text found — this looks like a scan. "
            "Attach the pages as images instead, so the model can read them visually."
        )
    return text, notes


def _ooxml_text(blob: bytes, members, tag: str, label: str) -> tuple:
    """Text nodes from an OOXML part. Shared by DOCX and PPTX.

    ``members`` is a callable taking the namelist and returning the parts to
    read, in order. ``tag`` is the local element name holding the text.
    """
    # defusedxml, not xml.etree: this parses the XML inside a DOCX/XLSX the
    # VENDOR uploaded, so it is untrusted input. The stdlib parser is
    # vulnerable to entity-expansion ("billion laughs") and quadratic blowup;
    # defusedxml is a drop-in with the same Element API.
    from defusedxml import ElementTree

    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise AttachmentError(f"is not a valid {label} file") from exc

    with archive:
        wanted = members(archive.namelist())
        if not wanted:
            raise AttachmentError(f"has no readable {label} content")
        parts, notes = [], []
        used = 0
        for member in wanted:
            try:
                root = ElementTree.fromstring(archive.read(member))
            except Exception:
                notes.append(f"{member} could not be parsed")
                continue
            # Namespace-agnostic: match any element whose local name is `tag`,
            # so this survives the several namespace URIs OOXML uses.
            chunk = " ".join(
                node.text for node in root.iter()
                if node.tag.rsplit("}", 1)[-1] == tag and node.text
            )
            if chunk:
                parts.append(chunk)
                used += len(chunk)
            if used >= PER_FILE_CHARS:
                notes.append(f"truncated — read {len(parts)} of {len(wanted)} parts")
                break
    return _clean("\n\n".join(parts)), notes


def _extract_docx(blob: bytes) -> tuple:
    return _ooxml_text(
        blob,
        lambda names: [n for n in names if n == "word/document.xml"],
        "t",
        "Word",
    )


def _extract_pptx(blob: bytes) -> tuple:
    def slides(names):
        # Sorted numerically, not lexically: slide10 must not sort before
        # slide2, or the deck reaches the model out of order.
        found = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        return sorted(found, key=lambda n: int(re.search(r"(\d+)", n).group(1)))

    return _ooxml_text(blob, slides, "t", "PowerPoint")


def _extract_xlsx(blob: bytes) -> tuple:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as exc:
        raise AttachmentError(f"could not be read as a spreadsheet ({exc})") from exc

    parts, notes = [], []
    used = 0
    try:
        for sheet in workbook.worksheets:
            parts.append(f"### Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                if not any(cell is not None and str(cell).strip() for cell in row):
                    continue
                # Tab-separated: compact, and the model reads a table off it
                # more reliably than off re-flowed prose.
                line = "\t".join("" if cell is None else str(cell) for cell in row)
                parts.append(line)
                used += len(line)
                if used >= PER_FILE_CHARS:
                    notes.append(f"truncated inside sheet '{sheet.title}'")
                    raise StopIteration
    except StopIteration:
        pass
    finally:
        workbook.close()
    return _clean("\n".join(parts)), notes


def _extract_xls(blob: bytes) -> tuple:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=blob)
    except Exception as exc:
        raise AttachmentError(f"could not be read as a legacy .xls ({exc})") from exc

    parts, notes = [], []
    used = 0
    for sheet in book.sheets():
        parts.append(f"### Sheet: {sheet.name}")
        for index in range(sheet.nrows):
            values = sheet.row_values(index)
            if not any(str(value).strip() for value in values):
                continue
            line = "\t".join(str(value) for value in values)
            parts.append(line)
            used += len(line)
            if used >= PER_FILE_CHARS:
                notes.append(f"truncated inside sheet '{sheet.name}'")
                return _clean("\n".join(parts)), notes
    return _clean("\n".join(parts)), notes


def _extract_delimited(blob: bytes, delimiter: str) -> tuple:
    text, notes = _decode(blob)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    lines, used = [], 0
    for row in reader:
        line = "\t".join(row)
        lines.append(line)
        used += len(line)
        if used >= PER_FILE_CHARS:
            notes.append("truncated")
            break
    return _clean("\n".join(lines)), notes


def _decode(blob: bytes) -> tuple:
    """Best-effort text decode. Never raises — a mangled byte is not worth
    rejecting a whole document over."""
    for encoding in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return blob.decode(encoding), ([] if encoding == "utf-8" else [f"decoded as {encoding}"])
        except (UnicodeDecodeError, LookupError):
            continue
    return blob.decode("utf-8", errors="replace"), ["contained undecodable bytes"]


def _extract_json(blob: bytes) -> tuple:
    text, notes = _decode(blob)
    try:
        # Re-dumped rather than passed through: indented JSON is markedly easier
        # for the model to follow than one minified line.
        return _clean(json.dumps(json.loads(text), indent=2)[:PER_FILE_CHARS]), notes
    except ValueError:
        notes.append("not valid JSON — read as plain text")
        return _clean(text[:PER_FILE_CHARS]), notes


def _extract_text(blob: bytes) -> tuple:
    text, notes = _decode(blob)
    return _clean(text), notes


_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".pptx": _extract_pptx,
    ".xlsx": _extract_xlsx,
    ".xlsm": _extract_xlsx,
    ".xls": _extract_xls,
    ".csv": lambda blob: _extract_delimited(blob, ","),
    ".tsv": lambda blob: _extract_delimited(blob, "\t"),
    ".json": _extract_json,
    ".txt": _extract_text,
    ".md": _extract_text,
    ".log": _extract_text,
    ".rst": _extract_text,
}


# ── Validation + dispatch ────────────────────────────────────────────────────
def _validate_image(blob: bytes) -> list:
    """Confirm the bytes really are a decodable image.

    An operator who renames ``notes.txt`` to ``chart.png`` would otherwise have
    it base64'd and sent, and the API would reject the whole request — this
    turns that into a per-file message before the call is made.
    """
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(blob))
        image.verify()
    except Exception as exc:
        raise AttachmentError(f"is not a readable image ({exc})") from exc
    return []


def extract_one(uploaded) -> Attachment:
    """Validate and read one uploaded file. Raises :class:`AttachmentError`."""
    name = getattr(uploaded, "name", "") or "attachment"
    size = getattr(uploaded, "size", None)
    blob = uploaded.read()
    if size is None:
        size = len(blob)

    if not blob:
        raise AttachmentError("is empty")
    if size > MAX_BYTES_PER_FILE:
        raise AttachmentError(
            f"is {size / (1024 * 1024):.1f} MB — the limit is "
            f"{MAX_BYTES_PER_FILE // (1024 * 1024)} MB per file"
        )

    extension = _extension(name)
    if extension in IMAGE_EXTENSIONS:
        notes = _validate_image(blob)
        return Attachment(
            name=name, size=size, kind="image",
            mime=IMAGE_EXTENSIONS[extension],
            data_url=f"data:{IMAGE_EXTENSIONS[extension]};base64,"
                     + base64.b64encode(blob).decode("ascii"),
            notes=notes,
        )

    extractor = _EXTRACTORS.get(extension)
    if extractor is None:
        if extension in KNOWN_UNSUPPORTED:
            raise AttachmentError(f"is not supported — {KNOWN_UNSUPPORTED[extension]}")
        raise AttachmentError(
            f"has an unsupported type ({extension or 'no extension'}). "
            "Accepted: PDF, Word, PowerPoint, Excel, CSV, text and images."
        )

    text, notes = extractor(blob)
    truncated = False
    if len(text) > PER_FILE_CHARS:
        text = text[:PER_FILE_CHARS]
        truncated = True
    if any("truncated" in note for note in notes):
        truncated = True
    return Attachment(
        name=name, size=size, kind="text", text=text,
        truncated=truncated, notes=notes,
    )


def collect(files) -> tuple:
    """Validate and extract a batch of uploads.

    Returns ``(attachments, errors)``. Errors are per-file and operator-facing;
    one bad file does not discard the good ones — the caller decides whether to
    proceed, and the UI shows both.
    """
    files = list(files or [])
    attachments, errors = [], []

    if len(files) > MAX_FILES:
        errors.append(
            f"{len(files)} files attached — the limit is {MAX_FILES}. "
            f"Only the first {MAX_FILES} were read."
        )
        files = files[:MAX_FILES]

    budget = TOTAL_CHARS
    for uploaded in files:
        name = getattr(uploaded, "name", "attachment")
        try:
            attachment = extract_one(uploaded)
        except AttachmentError as exc:
            errors.append(f"{name} {exc}")
            continue
        except Exception as exc:                       # pragma: no cover
            # A library blowing up on a malformed file must not 500 the whole
            # question — the other attachments are still usable.
            logger.exception("attachment extraction failed for %s", name)
            errors.append(f"{name} could not be read ({exc})")
            continue

        if attachment.kind == "text":
            if not attachment.text:
                errors.append(f"{name} contained no readable text")
                continue
            if len(attachment.text) > budget:
                attachment.text = attachment.text[:budget]
                attachment.truncated = True
                attachment.notes.append("shortened to fit the combined attachment budget")
            budget -= len(attachment.text)
        attachments.append(attachment)

    return attachments, errors


def as_prompt_parts(attachments) -> tuple:
    """Split attachments into ``(text_block, image_parts)`` for the API call.

    ``image_parts`` are Chat Completions ``image_url`` content parts.
    ``text_block`` is one delimited string, each file labelled and any
    truncation stated — the model is told it holds an excerpt, so it does not
    reason as though it saw the whole document.
    """
    blocks, images = [], []
    for attachment in attachments:
        if attachment.kind == "image":
            images.append({
                "type": "image_url",
                "image_url": {"url": attachment.data_url},
            })
            continue
        header = f"----- ATTACHED FILE: {attachment.name} ({attachment.pretty_size})"
        if attachment.truncated:
            header += " — EXCERPT ONLY, the file was longer than the limit"
        notes = [note for note in attachment.notes if "truncated" not in note]
        if notes:
            header += f" [{'; '.join(notes)}]"
        blocks.append(f"{header} -----\n{attachment.text}")
    return "\n\n".join(blocks), images


def describe_support() -> dict:
    """What the UI advertises, derived from this module so the two cannot drift."""
    return {
        "max_files": MAX_FILES,
        "max_mb": MAX_BYTES_PER_FILE // (1024 * 1024),
        "accept": ",".join(ALLOWED_EXTENSIONS),
        "extensions": list(ALLOWED_EXTENSIONS),
    }
