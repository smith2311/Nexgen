"""Retrieval over the documents attached to a MerilGPT thread.

The panel used to inline every attachment's whole extracted text into one
prompt and then forget it. Two things went wrong with that, and both look to
the user like "it does not answer from the file":

**The document only existed for one turn.** Only the filename was persisted, so
the follow-up question — which is usually the real question — reached the model
with no document at all, while the panel still showed the file chip. The model
then answered from the request payload and sounded confident about it.

**On anything long, the relevant passage was cut or buried.** ``gptfiles``
truncates at 40k characters per file and 120k for the batch. A 200-page tender
lost everything past the first fifth, and even inside the budget the answer had
to be found in ~30k tokens of mostly irrelevant text.

So documents are now chunked, embedded once on upload, stored against the
thread, and only the passages that actually match the question are sent — with
their filename and position attached, so the model can cite where an answer
came from and the reader can check it.

**Failure is soft, and falls back to the old behaviour.** If embedding is
unavailable — no key, the API down, the package missing — the document is
stored unindexed and its text is sent directly, exactly as before. A broken
embedding call must not make the panel unable to read a file at all; it only
makes it less selective.

**Short documents are sent whole.** Retrieving 5 passages from a two-page
quotation risks dropping the half that mattered, and the whole thing costs less
than the retrieval machinery saves. The cut-off is
:data:`WHOLE_DOCUMENT_CHARS`.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

#: Embedding model. The small one: 1536 dimensions, and the accuracy
#: difference against the large model does not show up on procurement
#: documents, where the match is usually lexical anyway ("what is the
#: per-pallet rate").
EMBED_MODEL = "text-embedding-3-small"

#: Target characters per chunk, and how much neighbouring text each carries.
#: ~1000 characters is roughly 250 tokens — big enough to hold a whole rate
#: table row or a clause, small enough that ten of them still leave room for
#: the request payload and the conversation history in one request.
CHUNK_CHARS = 1000
#: Overlap, so a sentence spanning a boundary is whole in at least one chunk.
#: Without it, "the rate is EUR 12.50 / pallet / month" split across two chunks
#: matches neither well and retrieves neither.
CHUNK_OVERLAP = 180

#: Below this, skip retrieval and send the document whole.
WHOLE_DOCUMENT_CHARS = 6000

#: Passages returned per question, across all of the thread's documents.
TOP_K = 8

#: Cap on what retrieval may contribute to one prompt, so a thread with four
#: large documents cannot crowd out the live request payload.
MAX_CONTEXT_CHARS = 24000

#: Chunks embedded per API call.
EMBED_BATCH = 64


# ── Chunking ────────────────────────────────────────────────────────────────

def chunk_text(text, size=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
    """Split text into overlapping passages, preferring natural boundaries.

    Splits on blank lines first and only cuts mid-paragraph when a paragraph is
    itself longer than ``size``. A fixed-width cut through a rate table splits
    rows from their headers and makes both halves useless to retrieve.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks, current = [], ""
    for paragraph in paragraphs:
        # A paragraph longer than a whole chunk is cut on its own, on
        # sentence-ish boundaries where possible.
        if len(paragraph) > size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long(paragraph, size, overlap))
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= size:
            current = candidate
        else:
            chunks.append(current.strip())
            # Carry the tail of the last chunk forward, so a clause that
            # straddles the join is intact in the next one.
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]


def _split_long(paragraph, size, overlap):
    """Cut one over-long paragraph, preferring sentence ends."""
    pieces, start = [], 0
    while start < len(paragraph):
        end = min(start + size, len(paragraph))
        if end < len(paragraph):
            # Look back for a sentence end, then any whitespace, but never
            # more than a quarter of the chunk — a cut is better than a chunk
            # a fraction of the intended size.
            window = paragraph[start:end]
            floor = int(size * 0.75)
            cut = max(window.rfind(". "), window.rfind("\n"), window.rfind("; "))
            if cut < floor:
                cut = window.rfind(" ")
            if cut >= floor:
                end = start + cut + 1
        pieces.append(paragraph[start:end].strip())
        if end >= len(paragraph):
            break
        start = max(end - overlap, start + 1)
    return [p for p in pieces if p]


# ── Embedding ───────────────────────────────────────────────────────────────

def embed_texts(texts):
    """Embeddings for a list of strings, or None when embedding is unavailable.

    None rather than an exception: the caller's fallback is to send the
    document text directly, which is worse but correct, and an unavailable
    embedding service must not stop a file being read at all.
    """
    texts = [t for t in (texts or []) if t and t.strip()]
    if not texts:
        return []

    from .ai_evaluator import _get_client

    client = _get_client()
    if client is None:
        return None

    vectors = []
    try:
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start:start + EMBED_BATCH]
            response = client.embeddings.create(model=EMBED_MODEL, input=batch)
            # Sorted by index rather than trusted in order: the API documents
            # the field, and a reordered response would silently pair every
            # chunk with the wrong vector — which produces plausible-looking
            # nonsense rather than an error.
            for item in sorted(response.data, key=lambda d: d.index):
                vectors.append(list(item.embedding))
    except Exception:
        logger.warning("MerilGPT embedding failed; falling back to whole-text context",
                       exc_info=True)
        return None

    if len(vectors) != len(texts):
        logger.warning(
            "MerilGPT embedding returned %d vectors for %d chunks; falling back",
            len(vectors), len(texts),
        )
        return None
    return vectors


# ── Indexing ────────────────────────────────────────────────────────────────

def index_attachments(conversation, attachments):
    """Store each text attachment against the thread, chunked and embedded.

    Returns the created :class:`~vendorlink.models.GptDocument` rows. Images
    are skipped: the vision model reads them from the message itself and there
    is no text to retrieve over.

    Never raises for an embedding failure — the document is stored either way,
    with ``indexed=False`` recording that retrieval will fall back to sending
    its text.
    """
    from .models import GptDocument, GptDocumentChunk

    if conversation is None:
        return []

    documents = []
    for attachment in attachments or []:
        if getattr(attachment, "kind", "") != "text":
            continue
        text = (getattr(attachment, "text", "") or "").strip()
        if not text:
            continue

        document = GptDocument.objects.create(
            conversation=conversation,
            name=attachment.name[:255],
            kind="text",
            size_bytes=int(getattr(attachment, "size", 0) or 0),
            text=text,
            char_count=len(text),
            truncated=bool(getattr(attachment, "truncated", False)),
        )
        documents.append(document)

        # A short document is sent whole, so there is nothing to gain from
        # embedding it — and the API call is not free.
        if len(text) <= WHOLE_DOCUMENT_CHARS:
            document.indexed = False
            document.index_error = ""
            document.save(update_fields=["indexed", "index_error"])
            continue

        chunks = chunk_text(text)
        vectors = embed_texts(chunks)
        if vectors is None:
            document.index_error = "embeddings unavailable — sending text directly"
            document.save(update_fields=["index_error"])
            continue

        GptDocumentChunk.objects.bulk_create([
            GptDocumentChunk(
                document=document, ordinal=index, text=chunk,
                embedding=json.dumps(vector),
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ])
        document.indexed = True
        document.save(update_fields=["indexed"])

    return documents


# ── Retrieval ───────────────────────────────────────────────────────────────

def _cosine_top_k(query_vector, rows, top_k):
    """The ``top_k`` most similar rows. ``rows`` is [(chunk, vector), ...].

    NumPy over the thread's own chunks: a few hundred vectors of 1536 floats is
    a sub-millisecond matrix multiply, and it needs no database extension —
    pgvector is not installed on this deployment.
    """
    import numpy as np

    if not rows:
        return []

    matrix = np.asarray([vector for _chunk, vector in rows], dtype="float32")
    query = np.asarray(query_vector, dtype="float32")

    # Normalised, so the dot product *is* cosine similarity. The epsilon keeps
    # a zero vector — which an empty or all-whitespace chunk can produce — from
    # dividing by zero and poisoning the whole comparison with NaN.
    matrix_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix_norms[matrix_norms == 0] = 1e-9
    query_norm = np.linalg.norm(query) or 1e-9

    scores = (matrix / matrix_norms) @ (query / query_norm)
    order = np.argsort(-scores)[:top_k]
    return [(rows[i][0], float(scores[i])) for i in order]


def retrieve(conversation, question, top_k=TOP_K, max_chars=MAX_CONTEXT_CHARS):
    """Passages from the thread's documents that bear on ``question``.

    Returns ``(context_block, sources)``. ``context_block`` is a labelled
    string ready to drop into the prompt; ``sources`` describes what was used,
    so the UI can show which documents an answer drew on.

    Every document contributes something. A thread holding a short quotation
    and a long tender sends the quotation whole and the matching passages of
    the tender — never all of one and none of the other, which is what a
    single global top-k would do.
    """
    from .models import GptDocument

    if conversation is None:
        return "", []

    documents = list(
        GptDocument.objects.filter(conversation=conversation)
        .prefetch_related("chunks")
        .order_by("created_at", "id")
    )
    if not documents:
        return "", []

    question = (question or "").strip()
    blocks, sources = [], []
    budget = max_chars

    whole = [d for d in documents if not d.indexed]
    indexed = [d for d in documents if d.indexed]

    # Unindexed documents first: short ones, and ones whose embedding failed.
    # They have no scores to rank by, so giving them the budget ahead of a
    # ranked list is the only way to guarantee they are represented at all.
    for document in whole:
        if budget <= 0:
            break
        text = document.text[:budget]
        blocks.append(_label(document, text, whole_document=True))
        budget -= len(text)
        sources.append({
            "name": document.name,
            "mode": "whole document" if len(text) == len(document.text) else "start of document",
            "passages": 1,
            "truncated": document.truncated or len(text) < len(document.text),
        })

    if indexed and question and budget > 0:
        query = embed_texts([question])
        if query:
            per_document = max(2, top_k // max(len(indexed), 1))
            for document in indexed:
                if budget <= 0:
                    break
                rows = []
                for chunk in document.chunks.all():
                    try:
                        rows.append((chunk, json.loads(chunk.embedding)))
                    except (TypeError, ValueError):
                        continue
                hits = _cosine_top_k(query[0], rows, per_document)
                # Re-sorted into document order after ranking: passages read
                # in the order they appear in the file, which is how a reader
                # checks a citation. Relevance decided which; position decides
                # how they are presented.
                hits.sort(key=lambda pair: pair[0].ordinal)
                used = 0
                for chunk, score in hits:
                    if budget <= 0:
                        break
                    text = chunk.text[:budget]
                    blocks.append(_label(document, text, ordinal=chunk.ordinal, score=score))
                    budget -= len(text)
                    used += 1
                if used:
                    sources.append({
                        "name": document.name,
                        "mode": "matched passages",
                        "passages": used,
                        "truncated": document.truncated,
                    })
        else:
            # Embedding the question failed even though the document indexed
            # earlier. Falling back to the start of each document beats
            # answering as though no file were attached.
            for document in indexed:
                if budget <= 0:
                    break
                text = document.text[:min(budget, WHOLE_DOCUMENT_CHARS)]
                blocks.append(_label(document, text, whole_document=True))
                budget -= len(text)
                sources.append({
                    "name": document.name, "mode": "start of document",
                    "passages": 1, "truncated": True,
                })

    return "\n\n".join(blocks), sources


def _label(document, text, ordinal=None, score=None, whole_document=False):
    """One passage, labelled with where it came from.

    The label is not decoration: the model is told to cite the file a figure
    came from, and it cannot do that if every passage arrives anonymous. The
    excerpt warning matters too — an answer drawn from the first 40k characters
    of a 900-page PDF is not an answer about the whole PDF, and the model
    should say so rather than implying completeness.
    """
    parts = [f"FILE: {document.name}"]
    if whole_document:
        parts.append("whole document" if not document.truncated else "excerpt from the start")
    else:
        total = document.chunks.count()
        parts.append(f"passage {ordinal + 1} of {total}")
    if document.truncated:
        parts.append("the upload itself was truncated to fit the size limit")
    header = " | ".join(parts)
    return f"----- {header} -----\n{text}"


def describe_sources(sources):
    """A one-line summary of what retrieval used, for the UI."""
    if not sources:
        return ""
    return "; ".join(
        f"{s['name']} ({s['mode']}"
        + (f", {s['passages']} passages" if s["passages"] > 1 else "")
        + ")"
        for s in sources
    )
