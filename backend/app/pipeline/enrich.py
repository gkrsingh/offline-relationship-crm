"""AI enrichment: read the unstructured parts of a record, return structured fields.

This is the one stage where an LLM is clearly the right tool. Deciding that
"Founder & CPO at Cipher Cloud (logistics, Series A)" means persona=founder,
sector=logistics, stage=series_a is reading comprehension over prose that no
regex survives contact with.

Three rules shape the design:

1. **Every field is a closed enum.** A free-text `persona` would produce
   "founder/operator", "Founder (technical)" and "startup founder" for the same
   thing, and the introduction engine would have to re-parse them.

2. **`unknown` is always a legal answer and is never penalised.** A record with
   no bio, no title and no company genuinely does not say what someone is. A
   model forced to choose would invent, and the invention would look identical
   to a real classification downstream.

3. **Evidence must quote the record, and the quote is checked.** Every
   classification names the source field and copies the span that supports it.
   `verify_evidence` then looks for that span in the actual record. A quote that
   is not there is a fabrication, and the enrichment is flagged rather than
   trusted. This is the cheapest hallucination detector available: it costs one
   substring search and it cannot be argued with.
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, Field

from backend.app.llm import cache
from backend.app.llm.provider import LLMProvider, schema_hint
from backend.app.pipeline.records import NormalizedRecord

PROMPT_VERSION = "enrich-v1"

CONFIG = {
    # Small batches on purpose. A model asked for 20 objects at once starts
    # dropping fields; the marginal token saving is not worth re-running.
    "BATCH_SIZE": 8,

    # Below this, the enrichment is stored but marked low-confidence so the UI
    # can show it as a suggestion rather than a fact.
    "LOW_CONFIDENCE_AT": 0.5,

    # A quote shorter than this matches too much text to be evidence of anything.
    "MIN_QUOTE_CHARS": 4,
}

# ---------------------------------------------------------------------------
# The enums. Every one of them ends in `unknown`.
# ---------------------------------------------------------------------------

Persona = Literal["founder", "operator", "investor", "service_provider", "ic", "unknown"]

Seniority = Literal["founder", "c_level", "vp", "director", "head_or_lead",
                    "senior_ic", "mid", "junior", "unknown"]

CompanyStage = Literal["pre_seed", "seed", "series_a", "series_b", "growth",
                       "public", "not_applicable", "unknown"]

Sector = Literal["b2b_saas", "fintech", "healthtech", "climate", "devtools",
                 "ecommerce", "marketplace", "ai_infra", "cybersecurity",
                 "logistics", "edtech", "consumer", "investing", "services",
                 "other", "unknown"]

Geography = Literal["india", "south_east_asia", "middle_east", "europe",
                    "north_america", "latin_america", "africa", "apac", "unknown"]

# The fields an evidence quote may cite. Anything else is a fabricated source.
CITABLE_FIELDS = ("full_name", "email", "linkedin_url", "company", "title",
                  "location", "bio", "needs", "offers")

EvidenceField = Literal["full_name", "email", "linkedin_url", "company", "title",
                        "location", "bio", "needs", "offers"]


class Evidence(BaseModel):
    field: EvidenceField
    quote: str = Field(description="text copied verbatim from that field")
    supports: str = Field(description="which enrichment field this justifies")


class PersonEnrichment(BaseModel):
    person_id: str
    persona: Persona
    seniority: Seniority
    company_stage: CompanyStage
    sector: Sector
    geography: Geography
    needs: list[str] = Field(default_factory=list, max_length=5)
    offers: list[str] = Field(default_factory=list, max_length=5)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)


class EnrichmentBatch(BaseModel):
    people: list[PersonEnrichment]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

ENRICHMENT_PROMPT = """\
You are structuring records from a private professional network so they can be
matched against each other.

For each person below, classify them from the fields provided and nothing else.

RULES
- Use only the values listed in the schema. Never invent a category.
- `unknown` is a correct answer whenever the record does not say. A record with
  no bio, no title and no company supports almost nothing: return unknown rather
  than a guess. Returning unknown is never penalised. Guessing is.
- `needs` and `offers`: short phrases, at most five each, describing what this
  person is looking for and what they can provide. If the record already lists
  them, tidy them; if it does not, infer them ONLY from the bio, and leave the
  list empty when the bio does not support any.
- `evidence`: for each classification you are confident about, cite the field
  you used and copy the exact span of text from it. The quote must appear
  verbatim in that field -- it is checked automatically. Do not paraphrase, do
  not quote a field that is empty, and do not cite a field you did not use.
- `confidence`: your confidence in the whole record, 0.0 to 1.0. A record that
  is mostly unknown should score low.

Respond with JSON only: {{"people": [one object per person_id below]}}

FIELDS (use exactly these values, nothing else)
{fields}
  needs, offers  up to 5 short phrases each
  confidence     0.0 to 1.0
  evidence       [{{"field": <one of the input field names>, "quote": <verbatim>,
                  "supports": <which field above it justifies>}}]

PEOPLE:
{people}
"""


def _field_spec() -> str:
    """A compact enum listing, in place of the full JSON schema.

    The schema was half the prompt -- 980 tokens of it -- and constrained
    decoding already forces the shape, so dumping it again bought nothing. What
    the model actually needs from a schema is the list of legal values, which is
    a fifth of the size. Providers without constrained decoding still get the
    enums, which is the part that was ever load-bearing.
    """
    return "\n".join(
        f"  {name:<14} {' | '.join(annotation.__args__)}"
        for name, annotation in (
            ("persona", Persona), ("seniority", Seniority),
            ("company_stage", CompanyStage), ("sector", Sector),
            ("geography", Geography)))


def _render_person(record: NormalizedRecord) -> str:
    fields = [
        ("full_name", record.raw_name),
        ("email", record.raw_email),
        ("linkedin_url", record.raw_linkedin),
        ("company", record.raw_company),
        ("title", record.raw_title),
        ("location", record.raw_location),
        ("bio", record.bio),
        ("needs", "; ".join(record.needs) if record.needs else None),
        ("offers", "; ".join(record.offers) if record.offers else None),
    ]
    body = "\n".join(f"    {name}: {value if value else '(empty)'}"
                     for name, value in fields)
    return f"  person_id: {record.id}\n{body}"


def build_prompt(batch: Sequence[NormalizedRecord]) -> str:
    return ENRICHMENT_PROMPT.format(
        fields=_field_spec(),
        people="\n\n".join(_render_person(r) for r in batch),
    )


# ---------------------------------------------------------------------------
# Evidence verification
# ---------------------------------------------------------------------------


def _normalise_for_match(text: str) -> str:
    """Fold case, accents and whitespace so a quote is not rejected for cosmetics."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def source_text(record: NormalizedRecord, field_name: str) -> str | None:
    return {
        "full_name": record.raw_name,
        "email": record.raw_email,
        "linkedin_url": record.raw_linkedin,
        "company": record.raw_company,
        "title": record.raw_title,
        "location": record.raw_location,
        "bio": record.bio,
        "needs": "; ".join(record.needs) if record.needs else None,
        "offers": "; ".join(record.offers) if record.offers else None,
    }.get(field_name)


@dataclass
class EvidenceCheck:
    total: int = 0
    verified: int = 0
    unverified: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unverified

    @property
    def rate(self) -> float:
        return self.verified / self.total if self.total else 1.0


def verify_evidence(record: NormalizedRecord, evidence: Iterable[dict]) -> EvidenceCheck:
    """Confirm every quote actually appears in the field it claims to come from.

    A model that cites `bio` for a record with no bio, or quotes text that is not
    in the record, has fabricated its justification. That is worth catching even
    when the classification itself happens to be right, because the next one will
    not be.
    """
    check = EvidenceCheck()
    for item in evidence:
        check.total += 1
        field_name = item.get("field")
        quote = (item.get("quote") or "").strip()
        source = source_text(record, field_name) if field_name in CITABLE_FIELDS else None

        if not source:
            check.unverified.append({**item, "why": f"field '{field_name}' is empty or unknown"})
            continue
        if len(quote) < CONFIG["MIN_QUOTE_CHARS"]:
            check.unverified.append({**item, "why": "quote too short to be evidence"})
            continue
        if _normalise_for_match(quote) not in _normalise_for_match(source):
            check.unverified.append({**item, "why": "quote does not appear in that field"})
            continue
        check.verified += 1
    return check


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class EnrichedPerson:
    person_id: str
    persona: str
    seniority: str
    company_stage: str
    sector: str
    geography: str
    needs: list[str]
    offers: list[str]
    confidence: float
    evidence: list[dict]
    evidence_verified: int
    evidence_total: int
    evidence_unverified: list[dict]
    low_confidence: bool
    prompt_version: str = PROMPT_VERSION

    @property
    def is_unknown(self) -> bool:
        return self.persona == "unknown"


@dataclass
class EnrichmentResult:
    people: list[EnrichedPerson] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # ids the model skipped
    funnel: dict[str, int] = field(default_factory=dict)


def unknown_enrichment(person_id: str, reason: str) -> EnrichedPerson:
    """What we store when the model returned nothing for a record.

    Not an error, and not a guess: an explicit `unknown` with zero confidence,
    so the UI shows a gap rather than a fabrication.
    """
    return EnrichedPerson(
        person_id=person_id, persona="unknown", seniority="unknown",
        company_stage="unknown", sector="unknown", geography="unknown",
        needs=[], offers=[], confidence=0.0,
        evidence=[{"field": "bio", "quote": "", "supports": reason}],
        evidence_verified=0, evidence_total=0, evidence_unverified=[],
        low_confidence=True,
    )


def _batched(items: Sequence, size: int) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


PERSON_TASK = "enrichment_person"


def person_cache_key(provider: LLMProvider, record: NormalizedRecord) -> str:
    """Cache key for ONE person, independent of who they were batched with.

    Keying the cache on the batch prompt looked fine until the set of canonical
    people changed: every batch re-chunked, every key missed, and answers already
    paid for became unreachable. A person's enrichment depends on that person's
    fields and the prompt version -- so that is what it is keyed on.
    """
    return cache.cache_key(provider.name, provider.model, PERSON_TASK,
                           {"person": _render_person(record),
                            "prompt_version": PROMPT_VERSION})


def enrich_batch(provider: LLMProvider,
                 batch: Sequence[NormalizedRecord]) -> dict[str, PersonEnrichment]:
    """Answer a batch, reusing any person already cached and asking only for the rest."""
    answers: dict[str, PersonEnrichment] = {}
    outstanding: list[NormalizedRecord] = []

    for record in batch:
        cached = cache.load(PERSON_TASK, person_cache_key(provider, record))
        if cached is None:
            outstanding.append(record)
            continue
        try:
            answers[record.id] = PersonEnrichment(**cached)
        except Exception:  # noqa: BLE001 -- a stale entry is a miss, not a crash
            outstanding.append(record)

    if not outstanding:
        return answers

    response = provider.complete_json("enrichment", build_prompt(outstanding),
                                      EnrichmentBatch)
    returned = {p["person_id"]: p for p in response["people"]}
    by_id = {r.id: r for r in outstanding}
    for person_id, payload in returned.items():
        record = by_id.get(person_id)
        if record is None:
            continue
        answers[person_id] = PersonEnrichment(**payload)
        cache.store(PERSON_TASK, person_cache_key(provider, record),
                    provider=provider.name, model=provider.model,
                    request={"person_id": person_id}, response=payload)
    return answers


def run(records: Sequence[NormalizedRecord], provider: LLMProvider,
        batch_size: int | None = None, pace: float = 0.0,
        on_error=None) -> EnrichmentResult:
    """Enrich every record given. Callers decide which records those are."""
    batch_size = batch_size or CONFIG["BATCH_SIZE"]
    result = EnrichmentResult()
    funnel = result.funnel
    funnel.update({"records": len(records), "batches": 0, "returned": 0,
                   "missing": 0, "unknown_persona": 0, "low_confidence": 0,
                   "evidence_total": 0, "evidence_verified": 0,
                   "records_with_bad_evidence": 0})

    funnel["failed_batches"] = 0
    ordered = sorted(records, key=lambda r: r.id)   # determinism: fixed batching
    for index, batch in enumerate(_batched(ordered, batch_size)):
        funnel["batches"] += 1
        if pace and index:
            time.sleep(pace)
        try:
            answers = enrich_batch(provider, batch)
        except Exception as exc:  # noqa: BLE001
            # One bad batch must not discard the ones already paid for. Those
            # records are stored as unknown, and a resumed run fills them from
            # cache plus however many fresh calls are still needed.
            funnel["failed_batches"] += 1
            if on_error:
                on_error(index, exc)
            for record in batch:
                funnel["missing"] += 1
                result.missing.append(record.id)
                result.people.append(unknown_enrichment(
                    record.id, f"batch failed: {exc.__class__.__name__}"))
            continue

        for record in batch:
            answer = answers.get(record.id)
            if answer is None:
                funnel["missing"] += 1
                result.missing.append(record.id)
                result.people.append(
                    unknown_enrichment(record.id, "model returned no entry for this record"))
                continue

            funnel["returned"] += 1
            evidence = [e.model_dump() for e in answer.evidence]
            check = verify_evidence(record, evidence)

            funnel["evidence_total"] += check.total
            funnel["evidence_verified"] += check.verified
            if not check.ok:
                funnel["records_with_bad_evidence"] += 1

            low = answer.confidence < CONFIG["LOW_CONFIDENCE_AT"]
            funnel["low_confidence"] += low
            funnel["unknown_persona"] += answer.persona == "unknown"

            result.people.append(EnrichedPerson(
                person_id=record.id,
                persona=answer.persona,
                seniority=answer.seniority,
                company_stage=answer.company_stage,
                sector=answer.sector,
                geography=answer.geography,
                needs=list(answer.needs),
                offers=list(answer.offers),
                confidence=answer.confidence,
                evidence=evidence,
                evidence_verified=check.verified,
                evidence_total=check.total,
                evidence_unverified=check.unverified,
                low_confidence=low,
            ))

    return result
