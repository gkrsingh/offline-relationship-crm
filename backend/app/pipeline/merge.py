"""Survivorship: turning a cluster of duplicate records into one canonical view.

Deterministic by design. Which value survives a merge is a business rule, not a
judgment call, and it has to give the same answer every time or the record
changes shape depending on when it was merged.

Merges are reversible. Nothing here deletes a source row -- the plan records
which ids fed the result and which record each surviving value came from, so an
operator can undo a merge and get the originals back intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from backend.app.pipeline.records import NormalizedRecord

# Fields resolved by "most recent non-null". full_name, email, company and bio
# are NOT here -- each has its own rule below, because for those four the most
# recent value is routinely the worse one.
SIMPLE_FIELDS = ("linkedin_url", "title", "location", "source")
UNION_FIELDS = ("needs", "offers", "tags")


@dataclass
class Conflict:
    field: str
    values: tuple[str, ...]
    detail: str


@dataclass
class MergePlan:
    canonical_id: str
    source_ids: tuple[str, ...]
    resolved: dict
    provenance: dict          # field -> id of the record the value came from
    conflicts: list[Conflict] = field(default_factory=list)
    first_contact_at: str | None = None
    decided_by: str = "unknown"   # stage1_exact | stage2_fuzzy | llm | human

    @property
    def requires_review(self) -> bool:
        """Email or company conflicts are never resolved automatically."""
        return bool(self.conflicts)


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def cluster_ids(pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    """Union-find over merge edges: A~B and B~C means one cluster of three."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            # Always point at the lower id so the root is stable across runs.
            parent[max(rx, ry)] = min(rx, ry)

    for a, b in pairs:
        union(a, b)

    groups: dict[str, list[str]] = {}
    for node in parent:
        groups.setdefault(find(node), []).append(node)
    return sorted((sorted(members) for members in groups.values()), key=lambda g: g[0])


# ---------------------------------------------------------------------------
# Survivorship
# ---------------------------------------------------------------------------


def _recency_key(record: NormalizedRecord) -> tuple:
    # Most recent first; a missing date sorts last; id breaks ties so the
    # result never depends on input ordering.
    return (record.created_at or "", record.id)


def _by_recency(records: Sequence[NormalizedRecord]) -> list[NormalizedRecord]:
    return sorted(records, key=_recency_key, reverse=True)


def choose_canonical(records: Sequence[NormalizedRecord]) -> NormalizedRecord:
    """The most complete record wins, then the oldest, then the lowest id."""
    return sorted(
        records,
        key=lambda r: (-r.completeness.score, r.created_at or "9999", r.id),
    )[0]


def _pick_email(records: Sequence[NormalizedRecord]) -> tuple[str | None, str | None, list[Conflict]]:
    """Rule 2: a work address outranks a personal one, regardless of recency.

    Two different work addresses (or two different personal ones) is a real
    disagreement about who this is, and goes to a human.
    """
    with_email = [r for r in _by_recency(records) if r.email]
    if not with_email:
        return None, None, []

    work = [r for r in with_email if not r.is_personal_email]
    personal = [r for r in with_email if r.is_personal_email]

    conflicts: list[Conflict] = []
    for label, group in (("work", work), ("personal", personal)):
        distinct = sorted({r.email_normalized for r in group if r.email_normalized})
        if len(distinct) > 1:
            conflicts.append(Conflict(
                field="email",
                values=tuple(distinct),
                detail=f"{len(distinct)} different {label} addresses across the cluster",
            ))

    winner = (work or personal)[0]
    return winner.raw_email, winner.id, conflicts


def _is_abbreviation_of(short: str, long: str) -> bool:
    """`delta` is an abbreviation of `deltaai`; `delta` is not of `helixgrid`.

    The 3-character floor stops a two-letter fragment from swallowing an
    unrelated employer. This is only ever asked about records already judged to
    be the same person, so it decides presentation, not identity.
    """
    return len(short) >= 3 and long.startswith(short) and short != long


def _pick_company(records: Sequence[NormalizedRecord]) -> tuple[str | None, str | None, list[Conflict]]:
    """Prefer the fullest form of the employer name.

    An abbreviation is not a disagreement. `Delta` and `Delta AI` are one
    employer written two ways, so the fuller spelling wins and no conflict is
    raised. Two genuinely different employers still go to a human.
    """
    with_company = [r for r in _by_recency(records) if r.company_key]
    if not with_company:
        return None, None, []

    distinct = sorted({r.company_key for r in with_company})
    conflicts: list[Conflict] = []

    if len(distinct) > 1:
        # Compatible means every key is a prefix-abbreviation of the longest.
        longest = max(distinct, key=len)
        compatible = all(key == longest or _is_abbreviation_of(key, longest)
                         for key in distinct)
        if not compatible:
            conflicts.append(Conflict(
                field="company",
                values=tuple(distinct),
                detail="records disagree on the employer after canonicalization",
            ))

    # Fullest spelling wins; recency only breaks ties between equal spellings.
    longest_key = max(len(r.company_key) for r in with_company)
    fullest = [r for r in with_company if len(r.company_key) == longest_key]
    winner = _by_recency(fullest)[0]
    return winner.raw_company, winner.id, conflicts


def _name_fullness(record: NormalizedRecord) -> tuple:
    """Rank a name by how much of it survived data entry.

    Ordering, most significant first:
      1. real tokens -- an initial ("b") carries less than a surname ("bakshi")
      2. total length of the normalized name
      3. proper casing -- `Ines Bakshi` reads better than `INES BAKSHI`
      4. recency, then id, purely to make ties deterministic
    """
    name = record.name
    if name is None:
        return (-1, -1, 0, "", "")
    real_tokens = sum(1 for token in name.tokens if len(token) > 1)
    raw = (record.raw_name or "").strip()
    properly_cased = raw not in (raw.upper(), raw.lower())
    return (real_tokens, len(name.normalized), int(properly_cased),
            record.created_at or "", record.id)


def _pick_name(records: Sequence[NormalizedRecord]) -> tuple[str | None, str | None]:
    """The fullest name, not the newest.

    `INES B.` arriving after `Ines Bakshi` is an abbreviated re-entry, not new
    information. Taking the most recent value there would make a merge
    destructive -- which is the opposite of the point.
    """
    named = [r for r in records if r.name and (r.raw_name or "").strip()]
    if not named:
        return None, None
    winner = max(named, key=_name_fullness)
    return winner.raw_name.strip(), winner.id


def _pick_bio(records: Sequence[NormalizedRecord]) -> tuple[str | None, str | None]:
    """Rule 3: the longest bio, not the newest. A later import is often a
    thinner one, and losing biographical detail loses enrichment evidence."""
    with_bio = [r for r in records if r.bio and r.bio.strip()]
    if not with_bio:
        return None, None
    winner = sorted(with_bio, key=lambda r: (-len(r.bio.strip()), r.id))[0]
    return winner.bio, winner.id


def _union(records: Sequence[NormalizedRecord], attr: str) -> list[str]:
    """Union preserving first-seen order, so the result is stable."""
    seen: dict[str, None] = {}
    for record in _by_recency(records)[::-1]:  # oldest first
        for value in getattr(record, attr, ()) or ():
            if value.strip():
                seen.setdefault(value.strip(), None)
    return list(seen)


def plan_merge(records: Sequence[NormalizedRecord],
               decided_by: str = "unknown") -> MergePlan:
    """Build the merged view of one duplicate cluster. Pure: no DB, no writes."""
    if len(records) < 2:
        raise ValueError("a merge needs at least two records")

    canonical = choose_canonical(records)
    ordered = _by_recency(records)

    resolved: dict = {}
    provenance: dict = {}
    conflicts: list[Conflict] = []

    # Rule 1: most recent non-null, for everything without a special rule.
    for field_name in SIMPLE_FIELDS:
        attr = {"linkedin_url": "raw_linkedin", "title": "raw_title",
                "location": "raw_location"}.get(field_name, field_name)
        for record in ordered:
            value = getattr(record, attr, None)
            if value not in (None, "", ()):
                resolved[field_name] = value
                provenance[field_name] = record.id
                break

    # Rule 1a: name follows the bio rule, not the recency rule. A later import
    # is frequently an abbreviated re-entry, and "INES B." overwriting
    # "Ines Bakshi" loses information the merge was supposed to consolidate.
    name, name_source = _pick_name(records)
    if name is not None:
        resolved["full_name"] = name
        provenance["full_name"] = name_source

    # Rule 2: email.
    email, email_source, email_conflicts = _pick_email(records)
    resolved["email"] = email
    if email_source:
        provenance["email"] = email_source
    conflicts.extend(email_conflicts)

    # Company overrides the generic rule so its conflicts are detected.
    company, company_source, company_conflicts = _pick_company(records)
    if company is not None:
        resolved["company"] = company
        provenance["company"] = company_source
    conflicts.extend(company_conflicts)

    # Rule 3: bio.
    bio, bio_source = _pick_bio(records)
    resolved["bio"] = bio
    if bio_source:
        provenance["bio"] = bio_source

    # Rule 4: union list fields.
    for field_name in UNION_FIELDS:
        values = _union(records, field_name)
        if values or field_name in ("needs", "offers"):
            resolved[field_name] = values

    # Rule 5: earliest created_at is when this relationship actually started.
    dates = sorted(r.created_at for r in records if r.created_at)
    first_contact = dates[0] if dates else None
    resolved["created_at"] = first_contact

    # Rule 6: never lose provenance.
    source_ids = tuple(sorted(r.id for r in records))
    resolved["source_record_ids"] = list(source_ids)

    return MergePlan(
        canonical_id=canonical.id,
        source_ids=source_ids,
        resolved=resolved,
        provenance=provenance,
        conflicts=conflicts,
        first_contact_at=first_contact,
        decided_by=decided_by,
    )
