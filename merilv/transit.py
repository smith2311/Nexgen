"""Mode of transit, and the two codes SAP knows it by.

SAP's invoice sheet carries the mode as a two-character code, not a word:

    M1  air / rail
    M2  surface / by hand

The warehouse does not think in those codes — a picker closing a delivery knows
whether it went on a van or a plane — so the two live side by side: the screen
says ``Road - M2`` and every Excel column that carries the mode writes ``M2``.
That split is the whole point of this module, and it is why the mapping lives in
one place instead of being spelled out at each of the seven call sites that need
it.

What is STORED is the word, not the code. ``FinishedDelivery.mode_of_transit``
and ``CourierMaster.mode_of_transit`` keep "Road", "Air" and so on, because a
delivery record read a year later should say what actually happened rather than
make the reader hold a lookup table in their head. The code is derived on the
way out. That also means no migration: rows written before this existed are
already in the right shape.

:func:`code_for` is deliberately forgiving about what it is handed. The field is
free text and always has been — it has been filled by a radio button, by a
datalist, by a courier-master spreadsheet import, and by whatever an operator
typed — so it answers for "AIR", "by hand", "M1" and "" alike rather than
trusting the caller to have normalised first.
"""

#: Display name → SAP code. Order is the order the radios and the datalist
#: offer them, so this list is also the UI's.
#:
#: Sea is a judgement, not a given: the request named air/rail for M1 and
#: surface/by hand for M2 and said nothing about water. It is not air and it is
#: not rail, so it falls to M2 with everything else that is not flown.
MODES: tuple[tuple[str, str], ...] = (
    ("Road", "M2"),
    ("Air", "M1"),
    ("Rail", "M1"),
    ("Sea", "M2"),
    ("By Hand", "M2"),
)

#: What a delivery gets when nothing says otherwise. Every dispatch used to get
#: Road implicitly; this keeps that true and names it.
DEFAULT_MODE = "Road"
DEFAULT_CODE = "M2"

#: Words that mean one of the modes above without being spelled like it. SAP's
#: own vocabulary ("SURFACE") and the phrasing on a courier master sheet both
#: land here rather than falling through to the default and losing the
#: distinction between "the operator said surface" and "nobody said anything".
_ALIASES: dict[str, str] = {
    "surface": "Road",
    "by road": "Road",
    "road": "Road",
    "air": "Air",
    "by air": "Air",
    "rail": "Rail",
    "by rail": "Rail",
    "train": "Rail",
    "sea": "Sea",
    "ship": "Sea",
    "by hand": "By Hand",
    "hand": "By Hand",
    "handcarry": "By Hand",
    "hand carry": "By Hand",
}

_BY_NAME = {name.lower(): code for name, code in MODES}
_BY_CODE = {code: name for name, code in reversed(MODES)}  # first name wins per code


def canonical(value) -> str:
    """The display name this value means, or "" if it means nothing.

    Empty is returned as empty rather than as the default: "nobody has said"
    and "somebody said Road" are different facts, and only the caller knows
    whether a default is appropriate. :func:`code_for` applies one; a radio
    group pre-selecting itself should not invent a choice the operator did not
    make.
    """
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in _BY_NAME:
        return next(name for name, _ in MODES if name.lower() == lowered)
    if lowered in _ALIASES:
        return _ALIASES[lowered]
    # Already a code — courier-master imports have carried "M1"/"M2" directly.
    if text.upper() in _BY_CODE:
        return _BY_CODE[text.upper()]
    return ""


def code_for(value) -> str:
    """The SAP code for a stored mode. Always one of M1 / M2.

    Falls back to :data:`DEFAULT_CODE` for anything unrecognised, because the
    column it feeds is coded and there is no third value to express doubt with.
    Writing the operator's raw word into it — which is what happened before
    this module existed — puts "Road" in a field SAP reads as an enum.
    """
    name = canonical(value)
    return _BY_NAME.get(name.lower(), DEFAULT_CODE) if name else DEFAULT_CODE


def label_for(value) -> str:
    """What the screen shows: ``Road - M2``.

    Both halves, because the two audiences are in the same room — the picker
    who chose it reads the word, and whoever reconciles the SAP upload reads
    the code. An unrecognised value is shown as typed rather than silently
    relabelled; it is still exported as M2, and showing it verbatim is what
    makes that visible instead of mysterious.
    """
    text = (value or "").strip()
    if not text:
        return ""
    name = canonical(text)
    return f"{name} - {_BY_NAME[name.lower()]}" if name else text


def choices() -> list[tuple[str, str]]:
    """``[(stored value, label)]`` for the radio group and the datalist."""
    return [(name, f"{name} - {code}") for name, code in MODES]
