"""Deterministic normalization. Pure functions: no LLM, no database, no I/O.

Everything here is a rule someone can read and argue with. That is the point --
an LLM asked to lowercase an email would be slower, cost money, and give a
different answer on Tuesday.

The alias tables are not guesses. They cover the specific mess present in the
source export: four shapes of LinkedIn URL, `Inc.`/`Ltd`/`Pvt Ltd` suffixes,
`VP` vs `Vice President`, `Co-founder & CEO` vs `Cofounder and CEO`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk",
    "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
    "aol.com", "gmx.com", "zoho.com", "hey.com", "fastmail.com",
}

# Providers that treat dots in the local part as insignificant. Applying this
# to every domain would merge genuinely different addresses.
DOT_INSENSITIVE_DOMAINS = {"gmail.com", "googlemail.com"}


@dataclass(frozen=True)
class Email:
    normalized: str
    local: str
    domain: str
    is_personal: bool


def normalize_email(raw: str | None) -> Email | None:
    """Lowercase, trim, drop +tags, split local/domain, flag personal domains.

    Returns None for anything that is not recognisably an address, so callers
    never have to guess whether an empty string means "missing" or "invalid".
    """
    if not raw:
        return None
    value = unicodedata.normalize("NFKC", raw).strip().strip("<>").lower()
    value = re.sub(r"\s+", "", value)
    if value.count("@") != 1:
        return None
    local, domain = value.split("@")
    if not local or "." not in domain:
        return None

    local = local.split("+", 1)[0]  # drop the +tag
    if domain in DOT_INSENSITIVE_DOMAINS:
        local = local.replace(".", "")
    if not local:
        return None

    return Email(
        normalized=f"{local}@{domain}",
        local=local,
        domain=domain,
        is_personal=domain in PERSONAL_EMAIL_DOMAINS,
    )


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

LEGAL_SUFFIXES = (
    "private limited", "pvt ltd", "pvt. ltd.", "pvt", "limited", "ltd", "llc",
    "l.l.c", "inc", "incorporated", "corp", "corporation", "co", "gmbh", "bv",
    "plc", "llp", "technologies", "technology", "labs", "laboratories", "studios",
)

_PARENTHETICAL = re.compile(r"\([^)]*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def canonicalize_company(raw: str | None) -> str | None:
    """Lowercase, drop legal suffixes and parentheticals, collapse punctuation.

    `Northwind Labs Pvt Ltd`, `NORTHWIND LABS`, `Northwind, Inc.` and
    `Northwind (formerly Northwind Labs)` all land on `northwind`.
    """
    if not raw:
        return None
    value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    value = _PARENTHETICAL.sub(" ", value).lower()
    value = value.replace("&", " and ")
    value = _NON_ALNUM.sub(" ", value).strip()
    if not value:
        return None

    # Suffixes strip from the right, repeatedly: "labs pvt ltd" -> "".
    tokens = value.split()
    changed = True
    while changed and tokens:
        changed = False
        for suffix in sorted(LEGAL_SUFFIXES, key=lambda s: -len(s.split())):
            parts = suffix.split()
            if len(tokens) > len(parts) and tokens[-len(parts):] == parts:
                tokens = tokens[: -len(parts)]
                changed = True
                break

    while tokens and tokens[-1] in {"and", "the"}:
        tokens.pop()

    return " ".join(tokens) or None


def company_key(canonical: str | None) -> str | None:
    """A whitespace-insensitive comparison key for a canonicalized company.

    Exports lose spaces (`CardinalTechnologies`), which defeats token-based
    suffix stripping. Collapsing to a single token and stripping suffixes again
    makes `Cardinal Technologies`, `CardinalTechnologies` and `Cardinal, Inc.`
    all compare equal. Display still uses the readable canonical form.
    """
    if not canonical:
        return None
    key = canonical.replace(" ", "")
    changed = True
    while changed:
        changed = False
        for suffix in sorted(LEGAL_SUFFIXES, key=len, reverse=True):
            flat = suffix.replace(" ", "").replace(".", "")
            if len(key) > len(flat) + 2 and key.endswith(flat):
                key = key[: -len(flat)]
                changed = True
                break
    return key or None


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------

_LINKEDIN_SLUG = re.compile(r"linkedin\.com/(?:in|pub)/([^/?#]+)", re.IGNORECASE)


def normalize_linkedin(raw: str | None) -> str | None:
    """Return the vanity slug only: no protocol, host, trailing slash or query."""
    if not raw:
        return None
    value = raw.strip().lower()
    match = _LINKEDIN_SLUG.search(value)
    if match:
        slug = match.group(1)
    elif "/" not in value and "linkedin" not in value:
        slug = value  # a bare slug was stored
    else:
        return None
    slug = slug.strip("/").split("?")[0].split("#")[0]
    slug = unicodedata.normalize("NFKD", slug).encode("ascii", "ignore").decode()
    return slug or None


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------

TITLE_ABBREVIATIONS = {
    "vp": "vice president",
    "v p": "vice president",
    "svp": "senior vice president",
    "evp": "executive vice president",
    "avp": "associate vice president",
    "sr": "senior",
    "jr": "junior",
    "dir": "director",
    "mgr": "manager",
    "gp": "general partner",
    "ceo": "chief executive officer",
    "cto": "chief technology officer",
    "coo": "chief operating officer",
    "cfo": "chief financial officer",
    "cpo": "chief product officer",
    "cmo": "chief marketing officer",
    "gm": "general manager",
    "revops": "revenue operations",
    "ops": "operations",
    "eng": "engineering",
    "cos": "chief of staff",
}

# Cleaned string -> canonical role. Keys are post-cleaning, so `Co-founder & CEO`,
# `Cofounder and CEO`, `co founder & ceo` and `CEO & Founder` all arrive here as
# some ordering of "co founder and chief executive officer".
TITLE_ALIASES = {
    "founder": "founder",
    "co founder": "founder",
    "cofounder": "founder",
    "founder and chief executive officer": "founder_ceo",
    "co founder and chief executive officer": "founder_ceo",
    "cofounder and chief executive officer": "founder_ceo",
    "chief executive officer and founder": "founder_ceo",
    "chief executive officer and co founder": "founder_ceo",
    "founder and chief technology officer": "founder_cto",
    "co founder and chief technology officer": "founder_cto",
    "cofounder and chief technology officer": "founder_cto",
    "founder and chief operating officer": "founder_coo",
    "co founder and chief operating officer": "founder_coo",
    "cofounder and chief operating officer": "founder_coo",
    "founder and chief product officer": "founder_cpo",
    "chief executive officer": "ceo",
    "chief technology officer": "cto",
    "chief operating officer": "coo",
    "chief of staff": "chief_of_staff",
    "chief of staff to the chief executive officer": "chief_of_staff",
    "head of growth": "head_of_growth",
    "growth lead": "head_of_growth",
    "head of product": "head_of_product",
    "product lead": "head_of_product",
    "head of customer success": "head_of_customer_success",
    "customer success lead": "head_of_customer_success",
    "head of people": "head_of_people",
    "general partner": "general_partner",
    "managing partner": "managing_partner",
    "partner": "partner",
    "principal": "principal",
    "angel investor": "angel_investor",
    "director of revenue operations": "director_revops",
    "staff software engineer": "staff_engineer",
    "staff engineer": "staff_engineer",
    "principal engineer": "principal_engineer",
    "solutions architect": "solutions_architect",
    "fractional chief financial officer": "fractional_cfo",
    "fractional chief marketing officer": "fractional_cmo",
}

_TITLE_NOISE = re.compile(r"\((?:[^)]*)\)|\b(interim|acting|contract)\b", re.IGNORECASE)

# `V.P.` cleans to the two tokens "v p", so it has to be substituted before the
# per-token expansion runs.
_MULTI_TOKEN_ABBREVIATIONS = tuple(
    (abbrev, expansion) for abbrev, expansion in TITLE_ABBREVIATIONS.items()
    if " " in abbrev
)


def clean_title(raw: str | None) -> str | None:
    """Lowercase, expand abbreviations, drop qualifiers. Not yet a role."""
    if not raw:
        return None
    value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    value = _TITLE_NOISE.sub(" ", value).lower()
    value = value.replace("&", " and ")
    value = _NON_ALNUM.sub(" ", value).strip()
    if not value:
        return None

    # Multi-token abbreviations first ("v p" from "V.P."), then single tokens.
    for abbrev, expansion in _MULTI_TOKEN_ABBREVIATIONS:
        value = re.sub(rf"\b{re.escape(abbrev)}\b", expansion, value)
    value = " ".join(TITLE_ABBREVIATIONS.get(t, t) for t in value.split())
    # "vice president of sales" and "vice president sales" are the same job.
    value = re.sub(r"\b(of|the|to|for|at|a|an)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip() or None


# The alias table is written the way a person writes a title. Cleaning the keys
# through the same function as the input keeps one set of rules, so adding a
# stopword to clean_title cannot silently break half the aliases.
_CLEANED_ALIASES: dict[str, str] = {}


def _cleaned_aliases() -> dict[str, str]:
    if not _CLEANED_ALIASES:
        for alias, role in TITLE_ALIASES.items():
            cleaned = clean_title(alias)
            if cleaned:
                _CLEANED_ALIASES[cleaned] = role
    return _CLEANED_ALIASES


def normalize_title(raw: str | None) -> str | None:
    """Collapse a title to a canonical role token.

    Falls through to the cleaned string when no alias applies, so two records
    with the same unusual title still compare equal.
    """
    cleaned = clean_title(raw)
    if not cleaned:
        return None
    aliases = _cleaned_aliases()
    if cleaned in aliases:
        return aliases[cleaned]

    # Order-insensitive retry: "chief executive officer and founder" is the
    # same role as "founder and chief executive officer".
    parts = sorted(p.strip() for p in cleaned.split(" and ") if p.strip())
    for alias, role in aliases.items():
        if sorted(p.strip() for p in alias.split(" and ") if p.strip()) == parts:
            return role
    return cleaned.replace(" ", "_")


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------

HONORIFICS = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "professor", "sir", "madam",
    "shri", "smt", "sri", "er", "ca",
}

NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "mba", "md"}


@dataclass(frozen=True)
class Name:
    normalized: str
    first: str | None
    last: str | None
    tokens: tuple[str, ...]  # sorted, deduplicated


def normalize_name(raw: str | None) -> Name | None:
    """NFKD, strip honorifics and suffixes, return first, last and a token set.

    Initials are kept as single-character tokens rather than dropped -- `P.` in
    `P. Raghavan` is evidence, and the matcher treats it as compatible with any
    first name starting with p.
    """
    if not raw:
        return None
    value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z\s'-]", " ", value).lower()
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return None

    tokens = [t.strip("'-") for t in value.split()]
    tokens = [t for t in tokens if t and t not in HONORIFICS and t not in NAME_SUFFIXES]
    if not tokens:
        return None

    return Name(
        normalized=" ".join(tokens),
        first=tokens[0],
        last=tokens[-1] if len(tokens) > 1 else None,
        tokens=tuple(sorted(set(tokens))),
    )


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

CITY_ALIASES = {
    "bangalore": "bengaluru",
    "bombay": "mumbai",
    "gurgaon": "delhi ncr",
    "gurugram": "delhi ncr",
    "new delhi": "delhi ncr",
    "dxb": "dubai",
    "nyc": "new york",
    "new york city": "new york",
    "sf": "san francisco",
    "sf bay area": "san francisco",
    "bay area": "san francisco",
    "london uk": "london",
}

CITY_COUNTRY = {
    "bengaluru": "India", "mumbai": "India", "delhi ncr": "India",
    "hyderabad": "India", "pune": "India", "chennai": "India",
    "singapore": "Singapore", "dubai": "United Arab Emirates",
    "london": "United Kingdom", "berlin": "Germany", "amsterdam": "Netherlands",
    "paris": "France", "san francisco": "United States", "new york": "United States",
    "austin": "United States", "toronto": "Canada", "sydney": "Australia",
    "sao paulo": "Brazil", "lagos": "Nigeria", "tokyo": "Japan",
}


def normalize_location(raw: str | None) -> tuple[str | None, str | None]:
    """Return (city, country). Country is inferred from a known-city table only,
    never guessed."""
    if not raw:
        return None, None
    value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return None, None

    parts = [p.strip() for p in value.split(",") if p.strip()]
    city_raw = parts[0].lower()
    city = CITY_ALIASES.get(city_raw, city_raw)
    country = parts[1] if len(parts) > 1 else None

    if city in CITY_ALIASES:
        city = CITY_ALIASES[city]
    known_country = CITY_COUNTRY.get(city)
    if known_country:
        country = known_country
    return city or None, country
