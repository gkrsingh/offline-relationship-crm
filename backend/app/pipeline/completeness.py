"""Completeness scoring. Deterministic, and phrased for a human.

A percentage tells an operator nothing they can act on. "Can't send an intro --
no email on file" tells them what is broken and what it stops. So the score is
kept for sorting, and the sentence is what the UI leads with.
"""

from __future__ import annotations

from dataclasses import dataclass

FIELD_WEIGHTS: dict[str, float] = {
    "email": 0.20,
    "linkedin_url": 0.15,
    "company": 0.15,
    "title": 0.15,
    "bio": 0.15,
    "location": 0.10,
    "needs_offers": 0.10,
}

# Without these a record cannot be acted on at all, whatever else it has.
BLOCKING_FIELDS = ("email", "company")

BLOCKED_SENTENCES = {
    "email": "Can't send an intro — no email on file",
    "company": "Can't judge fit or filter out colleagues — no company on file",
}

_LABELS = {
    "email": "email",
    "linkedin_url": "LinkedIn",
    "company": "company",
    "title": "title",
    "bio": "bio",
    "location": "location",
    "needs_offers": "needs or offers",
}


@dataclass(frozen=True)
class Completeness:
    score: float                 # 0..1, weighted
    missing: tuple[str, ...]     # field keys, in weight order
    blocked: bool
    blocked_reason: str | None   # a sentence, or None when actionable


def _present(record: dict, field: str) -> bool:
    if field == "needs_offers":
        return bool(record.get("needs")) or bool(record.get("offers"))
    value = record.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def score_completeness(record: dict) -> Completeness:
    """Score one record. `record` is the raw row plus whatever it already has;
    presence is judged on the source value, not the normalized one, so a record
    is never penalised for normalization failing."""
    missing = tuple(f for f in FIELD_WEIGHTS if not _present(record, f))
    score = round(sum(w for f, w in FIELD_WEIGHTS.items() if f not in missing), 4)

    blocking = [f for f in BLOCKING_FIELDS if f in missing]
    if blocking:
        reason = " and ".join(BLOCKED_SENTENCES[f] for f in blocking) \
            if len(blocking) == 1 else \
            "Can't send an intro or judge fit — no email or company on file"
    else:
        reason = None

    return Completeness(
        score=score,
        missing=missing,
        blocked=bool(blocking),
        blocked_reason=reason,
    )


def describe_gaps(completeness: Completeness) -> str:
    """One line listing what is missing, for the record detail panel."""
    if not completeness.missing:
        return "Complete"
    return "Missing " + ", ".join(_LABELS[f] for f in completeness.missing)
