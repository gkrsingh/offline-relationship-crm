"""The normalized record: what every stage after normalization operates on.

Raw rows are never handed to the matcher. `NormalizedRecord` carries both the
source values (for display and for survivorship) and the derived ones (for
matching), so no downstream stage has to re-derive anything or guess which it
is looking at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from backend.app.pipeline import normalize
from backend.app.pipeline.completeness import Completeness, score_completeness


@dataclass(frozen=True)
class NormalizedRecord:
    id: str

    # Source values, untouched.
    raw_name: str | None
    raw_email: str | None
    raw_linkedin: str | None
    raw_company: str | None
    raw_title: str | None
    raw_location: str | None
    bio: str | None
    source: str | None
    needs: tuple[str, ...]
    offers: tuple[str, ...]
    created_at: str | None

    # Derived values.
    name: normalize.Name | None
    email: normalize.Email | None
    linkedin_slug: str | None
    company_canonical: str | None
    company_key: str | None
    title_canonical: str | None
    title_clean: str | None
    city: str | None
    country: str | None
    completeness: Completeness = field(repr=False)

    # -- convenience accessors used by the matcher -------------------------

    @property
    def name_normalized(self) -> str:
        return self.name.normalized if self.name else ""

    @property
    def last_name(self) -> str | None:
        return self.name.last if self.name else None

    @property
    def first_name(self) -> str | None:
        return self.name.first if self.name else None

    @property
    def email_normalized(self) -> str | None:
        return self.email.normalized if self.email else None

    @property
    def email_local(self) -> str | None:
        return self.email.local if self.email else None

    @property
    def is_personal_email(self) -> bool:
        return bool(self.email and self.email.is_personal)

    def summary(self) -> str:
        """One line, for prompts and logs."""
        bits = [self.raw_name or "(no name)"]
        if self.raw_title:
            bits.append(self.raw_title)
        if self.raw_company:
            bits.append(self.raw_company)
        return " | ".join(bits)


def _as_list(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return (value,) if value.strip() else ()
    return tuple(v for v in value if isinstance(v, str) and v.strip())


def normalize_record(row: dict) -> NormalizedRecord:
    """Build a NormalizedRecord from a raw people row (dict or sqlite3.Row)."""
    row = dict(row)
    needs, offers = _as_list(row.get("needs")), _as_list(row.get("offers"))
    city, country = normalize.normalize_location(row.get("location"))

    return NormalizedRecord(
        id=row["id"],
        raw_name=row.get("full_name"),
        raw_email=row.get("email"),
        raw_linkedin=row.get("linkedin_url"),
        raw_company=row.get("company"),
        raw_title=row.get("title"),
        raw_location=row.get("location"),
        bio=row.get("bio"),
        source=row.get("source"),
        needs=needs,
        offers=offers,
        created_at=row.get("created_at"),
        name=normalize.normalize_name(row.get("full_name")),
        email=normalize.normalize_email(row.get("email")),
        linkedin_slug=normalize.normalize_linkedin(row.get("linkedin_url")),
        company_canonical=(canonical_company := normalize.canonicalize_company(row.get("company"))),
        company_key=normalize.company_key(canonical_company),
        title_canonical=normalize.normalize_title(row.get("title")),
        title_clean=normalize.clean_title(row.get("title")),
        city=city,
        country=country,
        completeness=score_completeness({**row, "needs": needs, "offers": offers}),
    )
