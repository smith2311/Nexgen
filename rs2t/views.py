import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from .services.session_manager import MAX_HISTORY_PER_USER


def _session_row(s):
    """Serialise a TranslationSession for the history list (no transcript body)."""
    return {
        "id": s.pk,
        "title": s.title,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat(),
        "duration_seconds": s.duration_seconds,
        "target_language": s.target_language,
        "word_count": s.word_count,
        "speaker_count": s.speaker_count,
        "detected_languages": s.detected_languages,
        "summary": s.summary,
        "key_points": s.key_points or [],
        "segment_count": len(s.transcript or []),
    }


@login_required
def index(request):
    """Main translator page"""
    return render(request, "rs2t/index.html")


@login_required
def history_list(request):
    """Return the user's saved sessions as JSON, newest first.

    Optional `?q=` filters on title, summary and detected languages. The quota
    itself is enforced on save (MAX_HISTORY_PER_USER), so this returns every
    session the user still has rather than re-clipping the list.
    """
    from rs2t.models import TranslationSession

    sessions = TranslationSession.objects.filter(user=request.user)

    q = (request.GET.get("q") or "").strip()
    if q:
        sessions = sessions.filter(Q(title__icontains=q) | Q(summary__icontains=q))

    sessions = sessions.order_by("-ended_at")[:MAX_HISTORY_PER_USER]
    return JsonResponse(
        {
            "sessions": [_session_row(s) for s in sessions],
            "quota": MAX_HISTORY_PER_USER,
            "query": q,
        }
    )


@login_required
def session_detail(request, pk):
    """Return one saved session including its full transcript, for in-app reading."""
    from rs2t.models import TranslationSession

    sess = get_object_or_404(TranslationSession, pk=pk, user=request.user)
    data = _session_row(sess)
    data["transcript"] = sess.transcript or []
    return JsonResponse(data)


def _srt_timestamp(seconds):
    """Format seconds as an SRT cue timestamp (HH:MM:SS,mmm)."""
    ms = int(round(seconds * 1000))
    ms = max(ms, 0)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _transcript_offsets(sess):
    """Yield (offset_seconds, entry) for each transcript entry.

    Entries store absolute ISO timestamps; subtitles need offsets from the
    session start. Entries with an unparseable timestamp fall back to a
    fixed cadence so a partly-corrupt transcript still exports.
    """
    start = sess.started_at
    for i, entry in enumerate(sess.transcript or []):
        offset = None
        raw = entry.get("timestamp")
        if raw and start:
            try:
                ts = datetime.fromisoformat(str(raw))
                if ts.tzinfo is None and start.tzinfo is not None:
                    from django.utils.timezone import make_aware

                    ts = make_aware(ts)
                offset = (ts - start).total_seconds()
            except (ValueError, TypeError):
                offset = None
        if offset is None or offset < 0:
            offset = float(i * 4)  # even 4s cadence when timestamps are unusable
        yield offset, entry


def _render_srt(sess):
    """Render the session's translations as an SRT subtitle track."""
    rows = list(_transcript_offsets(sess))
    lines = []
    for i, (offset, entry) in enumerate(rows):
        # A cue runs until the next one starts, clamped to 1–7s so a long
        # pause doesn't leave a subtitle frozen on screen.
        nxt = rows[i + 1][0] if i + 1 < len(rows) else offset + 4
        end = min(max(nxt, offset + 1.0), offset + 7.0)
        text = entry.get("translation") or entry.get("original") or ""
        if not text.strip():
            continue
        lines += [str(i + 1), f"{_srt_timestamp(offset)} --> {_srt_timestamp(end)}", text.strip(), ""]
    return "\n".join(lines)


def _render_markdown(sess):
    """Render the session as a Markdown document."""
    dur_m, dur_s = divmod(sess.duration_seconds, 60)
    out = [
        f"# {sess.title or 'LiveTranslate session'}",
        "",
        f'- **Date** — {sess.ended_at.strftime("%d %b %Y, %H:%M")}',
        f"- **Duration** — {dur_m}m {dur_s}s",
        f"- **Words** — {sess.word_count}",
        f'- **Languages heard** — {", ".join(sess.detected_languages) or "—"}',
        f"- **Translated to** — {sess.target_language}",
        "",
    ]
    if sess.summary:
        out += ["## Summary", "", sess.summary, ""]
    if sess.key_points:
        out += ["## Key points", ""] + [f"- {p}" for p in sess.key_points] + [""]
    out += ["## Transcript", ""]
    for entry in sess.transcript or []:
        ts = str(entry.get("timestamp", ""))[11:19] or "--:--:--"
        out += [
            f'**{ts} · {entry.get("language", "")}**',
            "",
            f'> {entry.get("original", "")}',
            "",
            entry.get("translation", ""),
            "",
        ]
    return "\n".join(out)


def _render_txt(sess):
    """Render the session as a plain-text export."""
    dur_m, dur_s = divmod(sess.duration_seconds, 60)
    lines = [
        "LiveTranslate Session Export",
        "=" * 55,
        f"Title:     {sess.title}",
        f'Date:      {sess.ended_at.strftime("%d %b %Y, %H:%M")}',
        f"Duration:  {dur_m}m {dur_s}s",
        f"Words:     {sess.word_count}",
        f"Speakers:  {sess.speaker_count}",
        f'Languages: {", ".join(sess.detected_languages) or "—"}',
        "",
    ]
    if sess.summary:
        lines += ["SUMMARY", "-" * 55, sess.summary, ""]
    if sess.key_points:
        lines += ["KEY POINTS", "-" * 55] + [f"  * {p}" for p in sess.key_points] + [""]
    lines += ["TRANSCRIPT", "-" * 55]
    for entry in sess.transcript or []:
        ts = str(entry.get("timestamp", ""))[:16]
        sp = entry.get("speaker_name", "Speaker")
        lines += [
            f"[{ts}] {sp}",
            f'  Original:    {entry.get("original", "")}',
            f'  Translation: {entry.get("translation", "")}',
            "",
        ]
    return "\n".join(lines)


# format key → (renderer, content type, file extension)
EXPORT_FORMATS = {
    "txt": (_render_txt, "text/plain", "txt"),
    "md": (_render_markdown, "text/markdown", "md"),
    "srt": (_render_srt, "application/x-subrip", "srt"),
}


@login_required
def session_download(request, pk):
    """Download a saved session as TXT, Markdown, SRT subtitles or JSON."""
    from rs2t.models import TranslationSession

    sess = get_object_or_404(TranslationSession, pk=pk, user=request.user)
    fmt = request.GET.get("format", "txt")

    if fmt == "json":
        content = json.dumps(
            {
                "title": sess.title,
                "started_at": sess.started_at.isoformat() if sess.started_at else None,
                "ended_at": sess.ended_at.isoformat(),
                "duration_seconds": sess.duration_seconds,
                "target_language": sess.target_language,
                "summary": sess.summary,
                "key_points": sess.key_points,
                "transcript": sess.transcript,
            },
            ensure_ascii=False,
            indent=2,
        )
        ct, ext = "application/json", "json"
    else:
        renderer, ct, ext = EXPORT_FORMATS.get(fmt, EXPORT_FORMATS["txt"])
        content = renderer(sess)

    safe = sess.title[:30].replace(" ", "_").replace("/", "-") if sess.title else "session"
    filename = f'livetranslate-{safe}-{sess.ended_at.strftime("%Y%m%d")}.{ext}'
    response = HttpResponse(content.encode("utf-8"), content_type=f"{ct}; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ─── Glossary ──────────────────────────────────────────────────────────────
# Terms the translator must get right. Wired into Whisper's initial_prompt and
# the translation prompt, so a term added here changes both recognition and
# rendering.


@login_required
def glossary_list(request):
    """Return the user's glossary terms."""
    from rs2t.models import GlossaryTerm

    terms = GlossaryTerm.objects.filter(user=request.user)
    return JsonResponse({"terms": [{"id": t.pk, "term": t.term, "hint": t.hint} for t in terms]})


@login_required
@require_POST
def glossary_add(request):
    """Add or update one term. Re-posting an existing term updates its hint."""
    from rs2t.models import GlossaryTerm

    term = (request.POST.get("term") or "").strip()
    hint = (request.POST.get("hint") or "").strip()
    if not term:
        return JsonResponse({"error": "A term is required."}, status=400)
    if len(term) > 120:
        return JsonResponse({"error": "That term is too long (120 characters max)."}, status=400)

    obj, created = GlossaryTerm.objects.update_or_create(user=request.user, term=term, defaults={"hint": hint[:200]})
    return JsonResponse({"id": obj.pk, "term": obj.term, "hint": obj.hint, "created": created})


@login_required
@require_POST
def glossary_delete(request, pk):
    """Remove one term."""
    from rs2t.models import GlossaryTerm

    get_object_or_404(GlossaryTerm, pk=pk, user=request.user).delete()
    return JsonResponse({"status": "deleted"})


@login_required
@require_POST
def session_delete(request, pk):
    """Delete a saved session."""
    from rs2t.models import TranslationSession

    sess = get_object_or_404(TranslationSession, pk=pk, user=request.user)
    sess.delete()
    return JsonResponse({"status": "deleted"})
