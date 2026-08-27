"""Unit tests for the pure normalization functions.

Every case here is a shape that actually occurs in data/raw/people_raw.json.
"""

from __future__ import annotations

import pytest

from backend.app.pipeline import normalize as n


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Priya.Nair@Vantage.com", "priya.nair@vantage.com"),
    ("  PRIYA.NAIR@VANTAGE.COM  ", "priya.nair@vantage.com"),
    ("priya.nair+events@vantage.com", "priya.nair@vantage.com"),
    ("priya.nair+newsletter+crm@vantage.com", "priya.nair@vantage.com"),
    ("<priya.nair@vantage.com>", "priya.nair@vantage.com"),
])
def test_normalize_email_cleans(raw, expected):
    assert n.normalize_email(raw).normalized == expected


def test_gmail_dots_are_insignificant():
    """Gmail ignores dots, so the dotted export variant is the same mailbox."""
    a = n.normalize_email("a.k.a.s.h.g.o.e.l@gmail.com")
    b = n.normalize_email("akashgoel@gmail.com")
    assert a.normalized == b.normalized == "akashgoel@gmail.com"


def test_dots_are_significant_everywhere_else():
    a = n.normalize_email("a.kash@vantage.com")
    b = n.normalize_email("akash@vantage.com")
    assert a.normalized != b.normalized


def test_email_splits_local_and_domain():
    email = n.normalize_email("priya.nair@vantage.com")
    assert (email.local, email.domain) == ("priya.nair", "vantage.com")


@pytest.mark.parametrize("domain,personal", [
    ("gmail.com", True), ("outlook.com", True), ("hey.com", True),
    ("proton.me", True), ("icloud.com", True), ("vantagesystems.com", False),
])
def test_personal_email_flag(domain, personal):
    assert n.normalize_email(f"someone@{domain}").is_personal is personal


@pytest.mark.parametrize("raw", [None, "", "   ", "not-an-email", "a@b@c.com", "@vantage.com"])
def test_unusable_emails_return_none(raw):
    assert n.normalize_email(raw) is None


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Northwind Labs", "Northwind Labs Inc.", "Northwind Labs, Inc.",
    "NORTHWIND LABS", "Northwind Labs Pvt Ltd", "Northwind Labs Private Limited",
    "  Northwind   Labs  ", "Northwind Labs LLC", "Northwind Labs (formerly Acme)",
])
def test_company_variants_canonicalize_together(raw):
    assert n.canonicalize_company(raw) == "northwind"


def test_company_keeps_distinguishing_words():
    assert n.canonicalize_company("Quanta Stack") == "quanta stack"
    assert n.canonicalize_company("Quanta Grid") == "quanta grid"


def test_company_ampersand_becomes_and():
    assert n.canonicalize_company("Mehta & Co") == "mehta"


def test_company_key_survives_lost_whitespace():
    """`CardinalTechnologies` is what an export looks like after a bad paste."""
    spaced = n.company_key(n.canonicalize_company("Cardinal Technologies"))
    squashed = n.company_key(n.canonicalize_company("CardinalTechnologies"))
    assert spaced == squashed == "cardinal"


def test_company_key_of_nothing_is_none():
    assert n.company_key(n.canonicalize_company(None)) is None


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "https://www.linkedin.com/in/priya-nair-2291",
    "http://linkedin.com/in/priya-nair-2291/",
    "linkedin.com/in/priya-nair-2291",
    "www.linkedin.com/in/priya-nair-2291",
    "https://www.linkedin.com/in/priya-nair-2291?utm_source=share",
    "https://linkedin.com/in/priya-nair-2291#experience",
    "HTTPS://WWW.LINKEDIN.COM/IN/PRIYA-NAIR-2291/",
])
def test_linkedin_shapes_reduce_to_the_slug(raw):
    assert n.normalize_linkedin(raw) == "priya-nair-2291"


def test_bare_slug_is_accepted():
    assert n.normalize_linkedin("priya-nair-2291") == "priya-nair-2291"


@pytest.mark.parametrize("raw", [None, "", "https://twitter.com/priya"])
def test_non_linkedin_returns_none(raw):
    assert n.normalize_linkedin(raw) is None


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Co-founder & CEO", "Cofounder and CEO", "co founder & ceo",
    "Founder & CEO", "CEO & Founder", "co-founder and chief executive officer",
])
def test_founder_ceo_titles_collapse(raw):
    assert n.normalize_title(raw) == "founder_ceo"


@pytest.mark.parametrize("a,b", [
    ("VP Sales", "Vice President of Sales"),
    ("VP Sales", "V.P. Sales"),
    ("Sr. Manager, Growth", "Senior Manager Growth"),
    ("Head of Growth", "Growth Lead"),
    ("General Partner", "GP"),
    ("Dir. of RevOps", "Director of Revenue Operations"),
    ("Staff Software Engineer", "Staff Engineer"),
    ("Head of Customer Success", "Customer Success Lead"),
])
def test_title_synonyms_agree(a, b):
    assert n.normalize_title(a) == n.normalize_title(b)


def test_title_qualifiers_are_dropped():
    assert n.normalize_title("Principal Engineer (interim)") == n.normalize_title("Principal Engineer")
    assert n.normalize_title("principal engineer") == n.normalize_title("PRINCIPAL ENGINEER")


def test_different_titles_stay_different():
    assert n.normalize_title("VP Sales") != n.normalize_title("VP Marketing")
    assert n.normalize_title("Founder & CEO") != n.normalize_title("Founder & CTO")


def test_missing_title_is_none():
    assert n.normalize_title(None) is None
    assert n.normalize_title("   ") is None


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------

def test_name_splits_first_and_last():
    name = n.normalize_name("Priya Raghavan")
    assert (name.first, name.last) == ("priya", "raghavan")
    assert name.tokens == ("priya", "raghavan")


@pytest.mark.parametrize("raw", [
    "  Priya  Raghavan ", "PRIYA RAGHAVAN", "priya raghavan", "Priya Raghavan.",
])
def test_name_formatting_noise_is_absorbed(raw):
    assert n.normalize_name(raw).normalized == "priya raghavan"


def test_accents_are_folded():
    assert n.normalize_name("Inês Bakshi").normalized == n.normalize_name("Ines Bakshi").normalized


def test_honorifics_and_suffixes_are_stripped():
    assert n.normalize_name("Dr. Priya Raghavan").normalized == "priya raghavan"
    assert n.normalize_name("Priya Raghavan Jr").normalized == "priya raghavan"


def test_initials_are_kept_as_evidence():
    """`P. Raghavan` must not silently lose the P -- the matcher uses it."""
    name = n.normalize_name("P. Raghavan")
    assert (name.first, name.last) == ("p", "raghavan")


def test_tokens_are_a_sorted_set():
    name = n.normalize_name("Raghavan Priya Raghavan")
    assert name.tokens == ("priya", "raghavan")


def test_single_token_name_has_no_last_name():
    name = n.normalize_name("Madonna")
    assert name.first == "madonna" and name.last is None


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,city", [
    ("Bengaluru, India", "bengaluru"),
    ("Bangalore", "bengaluru"),
    ("Bombay", "mumbai"),
    ("NYC", "new york"),
    ("SF Bay Area", "san francisco"),
    ("Gurgaon", "delhi ncr"),
])
def test_city_aliases_resolve(raw, city):
    assert n.normalize_location(raw)[0] == city


def test_country_is_looked_up_not_guessed():
    assert n.normalize_location("Bangalore") == ("bengaluru", "India")
    assert n.normalize_location("Someville") == ("someville", None)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_normalization_is_idempotent():
    """Normalizing twice must not change the answer, or merges would drift."""
    for fn, value in [
        (lambda v: n.normalize_email(v).normalized, "Priya.Nair+x@Vantage.com"),
        (n.canonicalize_company, "Northwind Labs Inc."),
        (n.normalize_linkedin, "https://www.linkedin.com/in/priya-nair/"),
        (lambda v: n.normalize_name(v).normalized, "  PRIYA  RAGHAVAN "),
    ]:
        once = fn(value)
        assert fn(once) == once
