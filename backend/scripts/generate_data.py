"""Generate the synthetic network and its ground-truth file.

Run:
    python backend/scripts/generate_data.py            # default seed 42
    python backend/scripts/generate_data.py --seed 7

Writes:
    data/raw/people_raw.json      messy records, exactly what an export would look like
    data/raw/applications.json    free-text application answers for applicant records
    data/ground_truth.json        evaluation only -- application code must never read this

Design notes
------------
* Everything is driven by one seeded RNG, so the same seed always produces the
  same dataset byte for byte.
* Record ids are assigned *after* the records are shuffled. If ids were handed
  out in creation order, a duplicate would always sit next to its original and
  the id itself would leak the answer.
* The generator knows which records are duplicates. Nothing downstream does --
  that knowledge only ever lands in ground_truth.json.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.scripts import vocab  # noqa: E402

# Fixed reference date so `created_at` values do not drift with the wall clock.
REFERENCE_DATE = date(2026, 8, 1)

DEFAULT_CANONICAL = 245
DUPLICATE_PLAN = {
    "exact": 6,           # same human entered twice, no field changed
    "email_variant": 12,  # work vs personal address, +tag, gmail dots
    "name_variant": 14,   # nickname, initial, accent stripped, transposed
    "company_variant": 10,  # Inc. / Ltd / renamed / abbreviated
}
TRIPLE_CLUSTERS = 3       # clusters that get a third record
NEAR_MISS_PAIRS = 9       # genuinely different people that look like duplicates
APPLICANT_COUNT = 40

MISSING_RATES = {
    "email": 0.08,
    "company": 0.05,
    "linkedin_url": 0.18,
    "bio": 0.06,
    "location": 0.04,
    "needs": 0.07,
    "offers": 0.07,
}

# A private founder network is bottom-heavy: far more seed companies than
# growth ones. Sampling stages uniformly would put two thirds of the network at
# Series A or later, which is not what any real community looks like.
STAGE_WEIGHTS = {
    "pre_seed": 0.18,
    "seed": 0.30,
    "series_a": 0.22,
    "series_b": 0.13,
    "growth": 0.12,
    "public": 0.05,
}

PERSONA_WEIGHTS = {
    "founder": 0.32,
    "operator": 0.27,
    "investor": 0.12,
    "ic": 0.19,
    "service_provider": 0.10,
}


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------


@dataclass
class Person:
    """One row of the messy export, plus generator-only bookkeeping."""

    key: str  # internal handle, replaced by a real id at the very end
    full_name: str
    email: str | None
    linkedin_url: str | None
    company: str | None
    title: str | None
    location: str | None
    bio: str | None
    source: str
    needs: list[str]
    offers: list[str]
    created_at: str

    # Never emitted into people_raw.json.
    persona: str = "unknown"
    seniority: str = "senior"
    sector_id: str = ""
    stage_id: str = ""
    first: str = ""
    last: str = ""
    domain: str = ""
    city: str = ""
    country: str = ""
    is_duplicate: bool = False
    need_topics: list[str] = field(default_factory=list)
    offer_topics: list[str] = field(default_factory=list)
    applicant_band: str | None = None

    def as_record(self, record_id: str) -> dict:
        return {
            "id": record_id,
            "full_name": self.full_name,
            "email": self.email,
            "linkedin_url": self.linkedin_url,
            "company": self.company,
            "title": self.title,
            "location": self.location,
            "bio": self.bio,
            "source": self.source,
            "needs": self.needs,
            "offers": self.offers,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def article_for(word: str) -> str:
    return "an" if word[:1].upper() in "AEIOU" else "a"


def random_date(rng: random.Random, max_days_ago: int = 1095) -> str:
    return (REFERENCE_DATE - timedelta(days=rng.randint(1, max_days_ago))).isoformat()


# ---------------------------------------------------------------------------
# Canonical person construction
# ---------------------------------------------------------------------------


def pick_persona(rng: random.Random) -> str:
    roll = rng.random()
    cumulative = 0.0
    for persona, weight in PERSONA_WEIGHTS.items():
        cumulative += weight
        if roll < cumulative:
            return persona
    return "founder"


def make_company(rng: random.Random, persona: str) -> str:
    stem = rng.choice(vocab.COMPANY_STEMS)
    if persona == "investor":
        return f"{stem} {rng.choice(vocab.FUND_TAILS)}"
    if persona == "service_provider":
        return f"{stem} {rng.choice(vocab.SERVICE_FIRM_TAILS)}"
    tail = rng.choice(vocab.COMPANY_TAILS)
    return f"{stem} {tail}".strip()


def make_title(rng: random.Random, persona: str, seniority: str) -> str:
    buckets = vocab.TITLES[persona]
    return rng.choice(buckets.get(seniority) or next(iter(buckets.values())))


def make_email(rng: random.Random, first: str, last: str, company: str | None) -> tuple[str, str]:
    """Return (email, domain). Roughly a fifth of the network uses personal mail."""
    f, l = slugify(first), slugify(last)
    if company is None or rng.random() < 0.2:
        domain = rng.choice(["gmail.com", "outlook.com", "proton.me", "hey.com"])
    else:
        domain = f"{slugify(company).replace('-', '')}.com"
    local = rng.choice([f"{f}.{l}", f"{f}{l}", f"{f[0]}{l}", f])
    return f"{local}@{domain}", domain


def pick_topics(rng: random.Random, persona: str, side: str, count: int) -> list[str]:
    attr = "need_personas" if side == "needs" else "offer_personas"
    pool = [t.id for t in vocab.TOPICS if persona in getattr(t, attr)]
    if not pool:
        pool = [t.id for t in vocab.TOPICS]
    return rng.sample(pool, min(count, len(pool)))


def render_topic(rng: random.Random, topic_id: str, side: str, ctx: dict) -> str:
    topic = vocab.TOPICS_BY_ID[topic_id]
    phrasings = topic.needs if side == "needs" else topic.offers
    return rng.choice(phrasings).format(**ctx)


def make_bio(rng: random.Random, p: Person, ctx: dict, focus: str) -> str:
    template = rng.choice(vocab.BIO_TEMPLATES[p.persona])
    return template.format(
        first=p.first,
        title=p.title,
        company=p.company,
        city=ctx["city"],
        sector=ctx["sector"],
        stage=ctx["stage"],
        a_stage="a",
        article=article_for(p.title or "operator"),
        years=rng.randint(4, 18),
        prior_line=rng.choice(vocab.PRIOR_LINES),
        focus=focus,
    )


def build_person(rng: random.Random, key: str, *, persona: str | None = None) -> Person:
    persona = persona or pick_persona(rng)
    seniority = "founder" if persona == "founder" else rng.choice(["senior", "senior", "mid"])

    first = rng.choice(vocab.FIRST_NAMES)
    last = rng.choice(vocab.LAST_NAMES)
    full_name = f"{first} {last}"

    sector_id, sector_label = rng.choice(vocab.SECTORS)
    stage_id = rng.choices(list(STAGE_WEIGHTS), weights=list(STAGE_WEIGHTS.values()))[0]
    stage_label = dict(vocab.STAGES)[stage_id]
    city, country, _alias = rng.choice(vocab.LOCATIONS)

    company = make_company(rng, persona)
    title = make_title(rng, persona, seniority)
    email, domain = make_email(rng, first, last, company)

    ctx = {"sector": sector_label, "stage": stage_label, "city": city}

    need_topics = pick_topics(rng, persona, "needs", rng.randint(1, 3))
    offer_topics = pick_topics(rng, persona, "offers", rng.randint(1, 3))
    needs = [render_topic(rng, t, "needs", ctx) for t in need_topics]
    offers = [render_topic(rng, t, "offers", ctx) for t in offer_topics]

    focus = " and ".join(vocab.TOPICS_BY_ID[t].label for t in offer_topics[:2]) or "operating"

    person = Person(
        key=key,
        full_name=full_name,
        email=email,
        linkedin_url=f"https://www.linkedin.com/in/{slugify(first)}-{slugify(last)}-{rng.randint(10, 9999)}",
        company=company,
        title=title,
        location=f"{city}, {country}" if country != city else city,
        bio=None,
        source=rng.choice(vocab.SOURCES),
        needs=needs,
        offers=offers,
        created_at=random_date(rng),
        persona=persona,
        seniority=seniority,
        sector_id=sector_id,
        stage_id=stage_id,
        first=first,
        last=last,
        domain=domain,
        city=city,
        country=country,
        need_topics=need_topics,
        offer_topics=offer_topics,
    )
    person.bio = make_bio(rng, person, ctx, focus)
    return person


# ---------------------------------------------------------------------------
# Noise
# ---------------------------------------------------------------------------


def noisy_email(rng: random.Random, person: Person) -> str | None:
    if not person.email:
        return None
    local, _, domain = person.email.partition("@")
    style = rng.choice(["personal", "plus_tag", "dots", "reordered"])
    if style == "personal":
        return f"{slugify(person.first)}.{slugify(person.last)}@{rng.choice(['gmail.com', 'outlook.com'])}"
    if style == "plus_tag":
        return f"{local}+{rng.choice(['events', 'newsletter', 'crm'])}@{domain}"
    if style == "dots" and domain == "gmail.com":
        return f"{'.'.join(local)}@{domain}"
    return f"{slugify(person.last)}.{slugify(person.first)}@{domain}"


def noisy_name(rng: random.Random, person: Person) -> str:
    styles = ["initial_last", "first_initial", "accent_stripped", "case", "extra_space"]
    if person.first.lower() in vocab.NICKNAMES:
        styles.append("nickname")
    style = rng.choice(styles)
    if style == "nickname":
        return f"{vocab.NICKNAMES[person.first.lower()]} {person.last}"
    if style == "initial_last":
        return f"{person.first[0]}. {person.last}"
    if style == "first_initial":
        return f"{person.first} {person.last[0]}."
    if style == "accent_stripped":
        return strip_accents(person.full_name)
    if style == "case":
        return person.full_name.upper() if rng.random() < 0.5 else person.full_name.lower()
    return f"  {person.first}  {person.last} "


def noisy_company(rng: random.Random, company: str | None) -> str | None:
    if not company:
        return None
    style = rng.choice(["suffix", "abbreviate", "case", "punctuation"])
    if style == "suffix":
        return company + rng.choice(vocab.COMPANY_SUFFIX_NOISE)
    if style == "abbreviate":
        return company.split()[0]
    if style == "case":
        return company.upper()
    return company.replace(" ", "") if len(company.split()) == 2 else company + " ."


def noisy_title(rng: random.Random, title: str | None) -> str | None:
    if not title:
        return None
    for short, long in vocab.TITLE_VARIANTS:
        if title.startswith(short) or title == short:
            return title.replace(short, long, 1)
    return rng.choice([title.lower(), title.upper(), f"{title} (interim)"])


def noisy_linkedin(rng: random.Random, url: str | None) -> str | None:
    if not url:
        return None
    handle = url.rstrip("/").rsplit("/", 1)[-1]
    return rng.choice([
        f"linkedin.com/in/{handle}",
        f"http://linkedin.com/in/{handle}/",
        f"https://www.linkedin.com/in/{handle}?utm_source=share",
        f"www.linkedin.com/in/{handle}",
    ])


def noisy_location(rng: random.Random, location: str | None) -> str | None:
    if not location:
        return None
    city = location.split(",")[0]
    for name, _country, alias in vocab.LOCATIONS:
        if name == city and alias:
            return alias
    return city


def make_duplicate(rng: random.Random, original: Person, key: str, kind: str) -> tuple[Person, list[str]]:
    """Return a second record for the same human, plus the noise applied."""
    dup = Person(**{**original.__dict__, "key": key})
    dup.needs = list(original.needs)
    dup.offers = list(original.offers)
    dup.need_topics = list(original.need_topics)
    dup.offer_topics = list(original.offer_topics)
    dup.created_at = random_date(rng, max_days_ago=400)
    dup.source = rng.choice(vocab.SOURCES)
    # The duplicate is a second *contact record*, not a second application. One
    # human submitting one application must not produce two scored applicants.
    dup.applicant_band = None
    dup.is_duplicate = True
    applied: list[str] = []

    if kind == "exact":
        return dup, ["exact"]

    if kind == "email_variant":
        dup.email = noisy_email(rng, original)
        applied.append("email_variant")
    elif kind == "name_variant":
        dup.full_name = noisy_name(rng, original)
        applied.append("name_variant")
    elif kind == "company_variant":
        dup.company = noisy_company(rng, original.company)
        applied.append("company_variant")

    # Real exports are never noisy in exactly one dimension.
    if rng.random() < 0.55:
        dup.title = noisy_title(rng, dup.title)
        applied.append("title_variant")
    if rng.random() < 0.45:
        dup.linkedin_url = noisy_linkedin(rng, dup.linkedin_url)
        applied.append("linkedin_format")
    if rng.random() < 0.30:
        dup.location = noisy_location(rng, dup.location)
        applied.append("location_variant")
    if rng.random() < 0.35:
        dropped = rng.choice(["email", "linkedin_url", "company", "bio"])
        setattr(dup, dropped, None)
        applied.append(f"missing_{dropped}")
    if rng.random() < 0.40:
        dup.needs = original.needs[:1]
        dup.offers = []
        applied.append("partial_needs_offers")

    return dup, applied


def make_near_miss(rng: random.Random, anchor: Person, key: str) -> tuple[Person, str]:
    """A genuinely different human that a naive matcher would merge with `anchor`."""
    kind = rng.choice(["same_name", "same_company_similar_name", "same_surname_same_company"])
    other = build_person(rng, key, persona=anchor.persona)
    was_first, was_company = other.first, other.company

    if kind == "same_name":
        # Same full name, different company, city, email and LinkedIn.
        other.full_name = anchor.full_name
        other.first, other.last = anchor.first, anchor.last
    elif kind == "same_company_similar_name":
        # Same employer, names one edit apart. The guard matters: if the edit
        # happened to reproduce the anchor's first name we would be labelling a
        # real duplicate as a near miss.
        other.company = anchor.company
        candidates = [anchor.first[:-1] + s for s in ("a", "it", "an", "esh")]
        other.first = rng.choice([c for c in candidates if c.lower() != anchor.first.lower()])
        other.full_name = f"{other.first} {anchor.last}"
        other.last = anchor.last
        other.email, other.domain = make_email(rng, other.first, other.last, other.company)
    else:
        # Same surname at the same company -- looks like a data-entry twin.
        other.company = anchor.company
        other.last = anchor.last
        other.first = rng.choice([n for n in vocab.FIRST_NAMES if n != anchor.first])
        other.full_name = f"{other.first} {other.last}"

    # Rebuilt after the rename in every branch: a contact whose LinkedIn slug
    # still spelled the name they were generated with would be a giveaway.
    other.email, other.domain = make_email(rng, other.first, other.last, other.company)
    other.linkedin_url = (
        f"https://www.linkedin.com/in/{slugify(other.first)}-{slugify(other.last)}-{rng.randint(10000, 99999)}"
    )

    # A near miss is two DIFFERENT people. If the generator hands them the same
    # mailbox -- easy to do when they share a surname and a first initial, as
    # Elena and Elenesh Raghavan both reduce to eraghavan@ -- then the label is
    # a lie: no correct matcher can separate two records with one email, and
    # stage 1 is right to merge them. Broken deterministically, without touching
    # the RNG, so datasets that never collide are byte-identical either way.
    if other.email and anchor.email and other.email.lower() == anchor.email.lower():
        local, _, domain = other.email.partition("@")
        other.email = f"{local}.{slugify(other.first)}@{domain}"
    if other.bio:
        other.bio = other.bio.replace(was_first, other.first)
        if was_company and other.company:
            other.bio = other.bio.replace(was_company, other.company)
    return other, kind


def enforce_identifier_uniqueness(people: list[Person], clusters: list[dict]) -> int:
    """Two different humans must never share an email address or a LinkedIn slug.

    Stage 1 merges on an identical email and is right to: one mailbox is one
    person. So if the generator hands the same address to two different humans,
    the resulting "false merge" is a mislabelled dataset, not a matcher defect --
    and it silently corrupts every held-out measurement taken against it.

    Collisions are easy to produce by accident. `make_email` sometimes uses just
    the first name on a personal domain, so any two people called Maya who both
    drew outlook.com collide.

    Records that ARE the same human keep their shared identifiers, which is the
    whole point of a duplicate. Disambiguation is deterministic and consumes no
    RNG, so a dataset with no collisions is byte-identical either way.
    """
    human_of: dict[str, str] = {}
    for cluster in clusters:
        canonical = cluster["record_keys"][0]
        for key in cluster["record_keys"]:
            human_of[key] = canonical

    fixed = 0
    for attr, splitter in (("email", "@"), ("linkedin_url", None)):
        owners: dict[str, str] = {}
        for person in sorted(people, key=lambda p: p.key):
            value = getattr(person, attr)
            if not value:
                continue
            identity = human_of.get(person.key, person.key)
            token = value.strip().lower()
            owner = owners.get(token)
            if owner is None:
                owners[token] = identity
                continue
            if owner == identity:
                continue  # same human, same identifier: correct

            # Different human, same identifier. Perturb deterministically.
            suffix = person.key[-3:]
            if splitter and splitter in value:
                local, _, domain = value.partition(splitter)
                new_value = f"{local}.{suffix}{splitter}{domain}"
            else:
                new_value = f"{value.rstrip('/')}-{suffix}"
            setattr(person, attr, new_value)
            owners[new_value.strip().lower()] = identity
            fixed += 1
    return fixed


def apply_missing_fields(rng: random.Random, people: list[Person]) -> dict[str, list[str]]:
    """Drop fields across the whole dataset, mimicking partially-filled sources."""
    missing: dict[str, list[str]] = {f"missing_{f}": [] for f in MISSING_RATES}
    for person in people:
        for field_name, rate in MISSING_RATES.items():
            if getattr(person, field_name) in (None, [], ""):
                missing[f"missing_{field_name}"].append(person.key)
                continue
            if rng.random() < rate:
                setattr(person, field_name, [] if field_name in ("needs", "offers") else None)
                missing[f"missing_{field_name}"].append(person.key)
    return missing


def apply_formatting_noise(rng: random.Random, people: list[Person]) -> None:
    """Cosmetic mess that normalization must absorb: casing, spacing, URL shapes."""
    for person in people:
        if rng.random() < 0.12:
            person.full_name = f" {person.full_name} "
        if rng.random() < 0.08 and person.full_name:
            person.full_name = person.full_name.upper()
        if rng.random() < 0.25:
            person.linkedin_url = noisy_linkedin(rng, person.linkedin_url)
        if rng.random() < 0.10 and person.email:
            person.email = person.email.upper()
        if rng.random() < 0.10:
            person.title = noisy_title(rng, person.title)
        if rng.random() < 0.08:
            person.location = noisy_location(rng, person.location)
        if rng.random() < 0.06 and person.company:
            person.company = person.company + rng.choice([" Inc.", " Ltd", "  "])


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def select_applicants(rng: random.Random, people: list[Person],
                      count: int) -> list[Person]:
    """Choose who applied for membership, AFTER the network exists.

    Offline vets founders, so the pool is mostly founders, with senior operators
    and a tail of people the network is not really for. Applicants skew to India
    because that is the business.

    Selecting post-hoc also keeps the person-generation RNG stream independent of
    the application layer: reworking applications cannot silently reshuffle the
    duplicate clusters the dedupe evaluation is measured against.
    """
    def eligible(persona: str) -> list[Person]:
        pool = [p for p in people if p.persona == persona and not p.is_duplicate]
        india = [p for p in pool if p.country == "India"]
        rest = [p for p in pool if p.country != "India"]
        return india, rest

    chosen: list[Person] = []
    for persona, share in vocab.APPLICANT_PERSONA_MIX:
        want = round(count * share)
        india, rest = eligible(persona)
        want_india = min(len(india), round(want * vocab.APPLICANT_INDIA_SHARE))
        picked = rng.sample(india, want_india)
        leftover = [p for p in india + rest if p not in picked]
        picked += rng.sample(leftover, min(want - len(picked), len(leftover)))
        chosen += picked

    # Top up or trim to land exactly on `count`, whatever the rounding did.
    if len(chosen) < count:
        pool = [p for p in people
                if not p.is_duplicate and p not in chosen
                and p.persona in {name for name, _ in vocab.APPLICANT_PERSONA_MIX}]
        chosen += rng.sample(pool, min(count - len(chosen), len(pool)))
    return sorted(chosen[:count], key=lambda p: p.key)


# The latent quality model. An applicant's band is a CONSEQUENCE of their
# attributes, not a label stamped on beforehand -- otherwise half the rubric's
# dimensions would be uncorrelated with the answer and Phase 8 would be
# measuring nothing.
#
# These weights mirror the Phase 4 rubric's dimensions on purpose. What makes
# the evaluation non-trivial is that Phase 4 never sees any of this: it works
# from the record and the free text, after normalization, missing fields and
# LLM enrichment have all had a go at them.
PERSONA_POINTS = {"founder": 30, "operator": 19, "ic": 10,
                  "investor": 14, "service_provider": 10}

# Seniority is "how much have you actually operated at scale", not "do you have
# the founder title". A solo pre-seed founder and a Series B founder are not the
# same seniority, and a rubric that treats them alike cannot rank anybody.
FOUNDER_SENIORITY_BY_STAGE = {"growth": 20, "public": 20, "series_b": 20,
                              "series_a": 16, "seed": 11, "pre_seed": 7}
SENIORITY_POINTS = {"senior": 14, "mid": 6}

STAGE_POINTS = {"growth": 20, "public": 19, "series_b": 18, "series_a": 16,
                "seed": 10, "pre_seed": 5}
REFERRAL_POINTS = {True: 15, False: 4}

# The fifth dimension is profile signal: how much substance the application and
# the record actually carry. Concrete traction numbers count, and so does having
# filled the profile in at all.
TRACTION_POINTS = {"high": 10, "mid": 6, "low": 2}
COMPLETENESS_POINTS = (0, 1, 2, 4, 5)   # indexed by fields present, 0..4

BAND_THRESHOLDS = (("strong", 75), ("review", 55))   # below 55 is weak

# ---------------------------------------------------------------------------
# The unobserved part of the decision.
#
# Without this the band would be an exact function of the same five dimensions
# the Phase 4 rubric scores, and a competent rubric would agree with ground
# truth ~99% of the time -- a number that would say nothing about the rubric and
# everything about the generator. Any accuracy figure reported from that setup
# would be a measurement of its own construction.
#
# It is also the honest model. A real membership decision turns on things no CRM
# row contains: how the founder came across, what a reference actually said,
# whether the market they picked is hot this year, a prior exit nobody wrote
# down. Those are not noise in the pejorative sense -- they are signal the
# rubric structurally cannot see.
#
# Calibrated so a rubric that perfectly recovers the observable part still lands
# around 0.77 band agreement. See backend/scripts/calibrate_bands.py.
# ---------------------------------------------------------------------------
UNOBSERVED_SD = 6.0              # everyday variation in how someone reads
UNOBSERVED_FLAG_PROBABILITY = 0.10   # a reference, an exit, or a red flag
UNOBSERVED_FLAG_RANGE = (8.0, 12.0)

REFERRAL_PROBABILITY = {"founder": 0.42, "operator": 0.30, "ic": 0.18}


def band_for(score: float) -> str:
    for band, floor in BAND_THRESHOLDS:
        if score >= floor:
            return band
    return "weak"


def seniority_points(person: Person) -> int:
    if person.persona == "founder":
        return FOUNDER_SENIORITY_BY_STAGE.get(person.stage_id, 7)
    return SENIORITY_POINTS.get(person.seniority, 6)


def profile_points(person: Person, traction_level: str) -> int:
    filled = sum(1 for value in (person.bio, person.needs, person.offers,
                                 person.linkedin_url) if value)
    return TRACTION_POINTS[traction_level] + COMPLETENESS_POINTS[filled]


def observable_score(person: Person, traction_level: str, referred: bool) -> float:
    """The part of fit that is visible in the data, on a 0-100 scale.

    This is the ceiling for any rubric: five dimensions, all recoverable in
    principle from the record and the application text.
    """
    return (
        PERSONA_POINTS.get(person.persona, 10)
        + seniority_points(person)
        + STAGE_POINTS.get(person.stage_id, 8)
        + REFERRAL_POINTS[referred]
        + profile_points(person, traction_level)
    )


def unobserved_component(rng: random.Random) -> float:
    """What the decision turned on that the CRM never recorded."""
    value = rng.gauss(0, UNOBSERVED_SD)
    if rng.random() < UNOBSERVED_FLAG_PROBABILITY:
        magnitude = rng.uniform(*UNOBSERVED_FLAG_RANGE)
        value += magnitude if rng.random() < 0.5 else -magnitude
    return value


def latent_score(rng: random.Random, person: Person, traction_level: str,
                 referred: bool) -> float:
    """The applicant's true fit: what is visible, plus what is not.

    Phase 4 never sees this. It has to recover the judgment from the record and
    the free text, after normalization, missing fields and LLM enrichment have
    each had a go at them -- and it can never recover the unobserved part at all,
    which is the point.
    """
    return max(0.0, min(100.0,
               observable_score(person, traction_level, referred)
               + unobserved_component(rng)))


def build_application(rng: random.Random, person: Person,
                      members: list[Person]) -> tuple[dict, str]:
    """Render one membership application. Returns (application, intended band).

    Stage comes from the person's own company -- the same stage their bio already
    describes -- so the application and the profile agree. Traction follows the
    stage with deliberate overlap, and the band falls out of the result.
    """
    stage_id = person.stage_id
    stage_label = dict(vocab.STAGES)[stage_id]
    sector_label = dict(vocab.SECTORS)[person.sector_id]

    traction_level = rng.choice(vocab.TRACTION_BY_STAGE[stage_id])
    goal_level = rng.choice(vocab.GOAL_LEVEL_BY_STAGE[stage_id])
    referred = rng.random() < REFERRAL_PROBABILITY.get(person.persona, 0.2)

    referred_by = None
    if referred:
        candidates = [m for m in members if m.key != person.key]
        if candidates:
            referred_by = rng.choice(candidates).key  # rewritten to a real id later
        else:
            referred = False

    offer_topic = (person.offer_topics or [t.id for t in vocab.TOPICS])[0]
    offer = rng.choice(vocab.TOPICS_BY_ID[offer_topic].offers).format(
        sector=sector_label, stage=stage_label, city=person.city)

    application = {
        "person_id": person.key,
        "building_now": rng.choice(
            vocab.APPLICATION_BUILDING_FOUNDER if person.persona == "founder"
            else vocab.APPLICATION_BUILDING_OPERATOR).format(
            company=person.company or "a company I have not named here",
            role=person.title or "part of the team",
            sector=sector_label,
            stage=stage_label,
            a_stage=f"{article_for(stage_label)} {stage_label}",
            city=person.city,
            traction=rng.choice(vocab.TRACTION_BY_LEVEL[traction_level])),
        "why_join": rng.choice(vocab.APPLICATION_WHY_JOIN).format(
            goal=rng.choice(vocab.JOIN_GOALS[goal_level])),
        "contribution": rng.choice(vocab.APPLICATION_CONTRIBUTION).format(offer=offer),
        "referred_by": referred_by,
        "submitted_at": random_date(rng, max_days_ago=150),
    }
    return application, band_for(latent_score(rng, person, traction_level, referred))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def scaled(count: int, canonical_count: int) -> int:
    """Keep the noise plan proportional when a caller asks for a smaller set.

    Tests generate 40-60 people; the demo generates 245. Without this, the
    fixed plan would try to duplicate more records than exist.
    """
    if canonical_count >= DEFAULT_CANONICAL:
        return count
    return max(1, round(count * canonical_count / DEFAULT_CANONICAL))


def generate(seed: int, canonical_count: int) -> tuple[list[dict], list[dict], dict]:
    rng = random.Random(seed)
    duplicate_plan = {k: scaled(v, canonical_count) for k, v in DUPLICATE_PLAN.items()}
    triple_clusters = scaled(TRIPLE_CLUSTERS, canonical_count)
    near_miss_pairs = scaled(NEAR_MISS_PAIRS, canonical_count)
    applicant_count = min(scaled(APPLICANT_COUNT, canonical_count), canonical_count)

    # 1. The network. Nobody is an applicant yet -- membership applications are
    #    layered on in step 6, from the people who actually exist.
    people: list[Person] = [build_person(rng, f"k{i:04d}") for i in range(canonical_count)]

    next_key = canonical_count

    # 2. Duplicates. Originals are chosen without replacement so one human never
    #    ends up in two different clusters.
    clusters: list[dict] = []
    total_dupes = sum(duplicate_plan.values())
    dup_targets = rng.sample(people, total_dupes)
    plan: list[str] = []
    for kind, count in duplicate_plan.items():
        plan.extend([kind] * count)
    rng.shuffle(plan)

    for original, kind in zip(dup_targets, plan):
        dup, applied = make_duplicate(rng, original, f"k{next_key:04d}", kind)
        next_key += 1
        people.append(dup)
        clusters.append({
            "cluster_id": f"c{len(clusters):04d}",
            "primary_kind": kind,
            "record_keys": [original.key, dup.key],
            "noise": applied,
        })

    # 3. A few clusters get a third record -- three copies of one human is a
    #    normal outcome of importing the same list twice.
    for cluster in rng.sample(clusters, triple_clusters):
        original = next(p for p in people if p.key == cluster["record_keys"][0])
        third, applied = make_duplicate(rng, original, f"k{next_key:04d}",
                                        rng.choice(list(duplicate_plan)))
        next_key += 1
        people.append(third)
        cluster["record_keys"].append(third.key)
        cluster["noise"] = sorted(set(cluster["noise"] + applied))

    # 4. Near misses: distinct humans engineered to look like duplicates.
    clustered_keys = {k for c in clusters for k in c["record_keys"]}
    anchor_pool = [p for p in people if p.key not in clustered_keys]
    near_misses: list[dict] = []
    for anchor in rng.sample(anchor_pool, near_miss_pairs):
        other, kind = make_near_miss(rng, anchor, f"k{next_key:04d}")
        next_key += 1
        people.append(other)
        near_misses.append({"record_keys": [anchor.key, other.key], "kind": kind})

    # 5. Two different humans must not share an identifier. Done before the
    #    noise pass so the guarantee survives whatever the noise does to case
    #    and spacing.
    collisions_fixed = enforce_identifier_uniqueness(people, clusters)

    # 6. Dataset-wide mess.
    apply_formatting_noise(rng, people)
    missing = apply_missing_fields(rng, people)

    # 7. Membership applications, on their own RNG.
    #    Separate stream on purpose: the dedupe evaluation is measured against
    #    the clusters built above, and reworking applications must not reshuffle
    #    them. Anything below this line can change without invalidating it.
    app_rng = random.Random(seed * 7919 + 13)
    applicants = select_applicants(app_rng, people, applicant_count)
    members = [p for p in people if p not in applicants and not p.is_duplicate]

    raw_applications: list[dict] = []
    for person in applicants:
        application, band = build_application(app_rng, person, members)
        person.applicant_band = band
        person.source = vocab.APPLICANT_SOURCE
        raw_applications.append(application)

    # 8. Shuffle, then assign ids. Ids must not encode creation order.
    rng.shuffle(people)
    key_to_id = {p.key: f"p-{i + 1:04d}" for i, p in enumerate(people)}

    records = [p.as_record(key_to_id[p.key]) for p in people]
    applications = sorted(
        ({**a,
          "person_id": key_to_id[a["person_id"]],
          "referred_by": key_to_id[a["referred_by"]] if a["referred_by"] else None}
         for a in raw_applications),
        key=lambda a: a["person_id"],
    )

    def ids(keys: list[str]) -> list[str]:
        return sorted(key_to_id[k] for k in keys)

    ground_truth = {
        "meta": {
            "seed": seed,
            "reference_date": REFERENCE_DATE.isoformat(),
            "canonical_people": canonical_count + near_miss_pairs,
            "total_records": len(records),
            "duplicate_records": len(records) - canonical_count - near_miss_pairs,
            "identifier_collisions_fixed": collisions_fixed,
            "note": "Evaluation only. Application code must never read this file.",
        },
        "duplicate_clusters": sorted(
            ({
                "cluster_id": c["cluster_id"],
                "primary_kind": c["primary_kind"],
                "record_ids": ids(c["record_keys"]),
                "noise": sorted(set(c["noise"])),
            } for c in clusters),
            key=lambda c: c["record_ids"][0],
        ),
        "near_miss_pairs": sorted(
            ({"record_ids": ids(n["record_keys"]), "kind": n["kind"]} for n in near_misses),
            key=lambda n: n["record_ids"][0],
        ),
        "incomplete_records": {k: ids(v) for k, v in sorted(missing.items())},
        # The intended band lives here and nowhere else. It is the answer key
        # for Phase 4 scoring and must never appear on a record.
        "applicants": [
            {"id": key_to_id[p.key], "band": p.applicant_band}
            for p in sorted((p for p in people if p.applicant_band),
                            key=lambda p: key_to_id[p.key])
        ],
        "topic_assignments": {
            key_to_id[p.key]: {"needs": p.need_topics, "offers": p.offer_topics}
            for p in sorted(people, key=lambda p: key_to_id[p.key])
        },
    }
    return records, applications, ground_truth


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def summarise(records: list[dict], ground_truth: dict) -> str:
    gt = ground_truth
    dup_records = sum(len(c["record_ids"]) - 1 for c in gt["duplicate_clusters"])
    lines = [
        f"records written        : {len(records)}",
        f"distinct real people   : {len(records) - dup_records}",
        f"duplicate clusters     : {len(gt['duplicate_clusters'])} covering {dup_records + len(gt['duplicate_clusters'])} records",
        f"near-miss pairs        : {len(gt['near_miss_pairs'])}",
        f"applicants             : {len(gt['applicants'])}",
    ]
    kinds: dict[str, int] = {}
    for cluster in gt["duplicate_clusters"]:
        kinds[cluster["primary_kind"]] = kinds.get(cluster["primary_kind"], 0) + 1
    lines.append("duplicate kinds        : " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    lines.append("incomplete records     : " + ", ".join(
        f"{k.replace('missing_', '')}={len(v)}" for k, v in gt["incomplete_records"].items()))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--canonical", type=int, default=DEFAULT_CANONICAL)
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    records, applications, ground_truth = generate(args.seed, args.canonical)

    raw_dir = args.out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "people_raw.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    (raw_dir / "applications.json").write_text(
        json.dumps(applications, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out_dir / "ground_truth.json").write_text(
        json.dumps(ground_truth, indent=2, ensure_ascii=False), encoding="utf-8")

    print(summarise(records, ground_truth))
    print(f"\nwrote {raw_dir / 'people_raw.json'}")
    print(f"wrote {raw_dir / 'applications.json'}")
    print(f"wrote {args.out_dir / 'ground_truth.json'}")


if __name__ == "__main__":
    main()
