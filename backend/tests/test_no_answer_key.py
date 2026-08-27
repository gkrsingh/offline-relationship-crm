"""The answer key must be unreachable from application code.

Phase 4 scores applicants from a deterministic rubric and Phase 8 evaluates that
score against ground truth. Both are worthless if the application can see the
expected answer -- directly, by import, or by reverse-lookup of generated text.

This file is the fence. It fails on three different ways the fence can be
breached, because in practice each one has a different plausible cause:

  1. someone reads data/ground_truth.json from the app          (obvious)
  2. someone names a field intended_band / is_duplicate_of       (careless)
  3. someone imports the generator, whose latent quality model IS  (subtle)
     the answer key -- it is what decides each applicant's band
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "backend" / "app"
DATA_DIR = REPO_ROOT / "data"

APP_FILES = sorted(APP_DIR.rglob("*.py"))

# Identifiers that name an expected outcome rather than a computed one.
#
# Note what is deliberately NOT here: `band`, `band_for`, `score`, `verdict`,
# `confidence`, `PERSONA_POINTS`. Those are all things the application computes
# for itself -- the Phase 4 rubric has its own `band_for` and its own persona
# weights, and banning the names would ban the feature.
#
# The banned forms are the ones that only make sense if you already know the
# answer: expected_, intended_, true_, gold_, actual_, and `latent_score`, which
# is the generator's name for the hidden truth behind an applicant's band.
#
# The real fence is the import ban below. A name collision is a lint; reaching
# into backend/scripts is how the answer would actually leak.
FORBIDDEN_IDENTIFIERS = [
    "intended_band", "expected_band", "actual_band", "true_band",
    "applicant_band", "latent_score",
    "fit_label", "gold_label", "true_label", "target_label",
    "is_strong", "is_weak", "is_promising",
    "expected_score", "true_score", "gold_score",
    "is_duplicate_of", "dup_group", "duplicate_group", "cluster_truth",
    "applicant_tier", "expected_persona", "answer_key", "ground_truth",
]

# Modules that hold generator-side knowledge. Importing any of them from the
# app is enough to recover every applicant's intended band.
FORBIDDEN_IMPORTS = ("backend.scripts", "generate_data", "vocab")

_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _identifiers(path: Path) -> set[str]:
    """Every bare word in the file, comments and strings included.

    Deliberately cruder than an AST walk: a leak pasted into a docstring or a
    SQL string is still a leak, and this catches it.
    """
    return set(_WORD.findall(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: p.name)
def test_no_answer_key_identifier_in_app_code(path):
    found = sorted(_identifiers(path) & set(FORBIDDEN_IDENTIFIERS))
    assert not found, (
        f"{path.relative_to(REPO_ROOT).as_posix()} references {found}. "
        "These name an expected outcome; application code may only compute one."
    )


def test_application_code_never_reads_ground_truth():
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in APP_FILES
        if "ground_truth" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"application code references ground truth: {offenders}"


def test_no_answer_key_reachable_from_app():
    """backend/app must not import the generator.

    generate_data.latent_score computes every applicant's band from attributes
    the application text only hints at. One import and the rubric could return
    the answer instead of deriving it.
    """
    offenders: list[str] = []
    for path in APP_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == f or name.startswith(f + ".") or name.endswith("." + f)
                       for f in FORBIDDEN_IMPORTS):
                    offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {name}")
    assert not offenders, f"application code imports generator internals: {offenders}"


def test_the_latent_model_really_is_an_answer_key():
    """Guard the guard.

    The band is computed by generate_data.latent_score from attributes the
    application never states outright. Importing that module hands you the
    answer for every applicant, which is why the import ban above exists. If
    someone removes the latent model, this test should be what tells them the
    ban has quietly become decorative.
    """
    from backend.scripts import generate_data

    assert hasattr(generate_data, "latent_score")
    assert generate_data.band_for(90) == "strong"
    assert generate_data.band_for(60) == "review"
    assert generate_data.band_for(40) == "weak"


def test_application_text_does_not_state_the_band():
    """The generated text must never contain the band word itself."""
    path = DATA_DIR / "raw" / "applications.json"
    if not path.exists():
        pytest.skip("applications not generated yet")
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        blob = " ".join(str(v) for v in row.values() if isinstance(v, str)).lower()
        for band in ("strong applicant", "weak applicant", "high potential"):
            assert band not in blob, f"{row['person_id']} states its band"


@pytest.mark.parametrize("filename", ["raw/people_raw.json", "raw/applications.json"])
def test_committed_data_carries_no_outcome_label(filename):
    """The records that ship must contain no field naming an expected outcome."""
    path = DATA_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not generated yet")

    rows = json.loads(path.read_text(encoding="utf-8"))
    keys = {key for row in rows for key in row}
    leaked = sorted(keys & set(FORBIDDEN_IDENTIFIERS))
    assert not leaked, f"{filename} carries outcome labels: {leaked}"

    # And no value that is simply a band name sitting in a differently-named field.
    band_values = {"strong", "review", "weak"}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str) and value.strip().lower() in band_values:
                pytest.fail(f"{filename} field '{key}' holds a band value: {value!r}")


def test_ground_truth_is_where_the_bands_live():
    """The bands must exist -- in ground truth, keyed by application id."""
    path = DATA_DIR / "ground_truth.json"
    if not path.exists():
        pytest.skip("ground truth not generated yet")

    gt = json.loads(path.read_text(encoding="utf-8"))
    applicants = gt["applicants"]
    assert applicants, "ground truth records no applicants"
    assert all({"id", "band"} <= set(a) for a in applicants)
    assert {a["band"] for a in applicants} == {"strong", "review", "weak"}
