"""Invariants the synthetic dataset must hold before any pipeline runs on it.

These are not tests of the generator's prose -- they check the things later
phases depend on: stable ids, every noise class present, and ground truth that
actually describes the file next to it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.scripts import generate_data as gen

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = REPO_ROOT / "data" / "raw" / "people_raw.json"
GT_PATH = REPO_ROOT / "data" / "ground_truth.json"

RECORD_FIELDS = {
    "id", "full_name", "email", "linkedin_url", "company", "title",
    "location", "bio", "source", "needs", "offers", "created_at",
}


@pytest.fixture(scope="module")
def generated():
    return gen.generate(seed=42, canonical_count=gen.DEFAULT_CANONICAL)


def test_same_seed_produces_identical_output():
    first = gen.generate(seed=7, canonical_count=60)
    second = gen.generate(seed=7, canonical_count=60)
    assert json.dumps(first) == json.dumps(second)


def test_different_seeds_produce_different_output():
    a = gen.generate(seed=7, canonical_count=60)[0]
    b = gen.generate(seed=8, canonical_count=60)[0]
    assert a != b


def test_dataset_size_in_expected_range(generated):
    records, _apps, _gt = generated
    assert 250 <= len(records) <= 310


def test_records_expose_only_the_agreed_fields(generated):
    records, _apps, _gt = generated
    for record in records:
        assert set(record) == RECORD_FIELDS, record["id"]


def test_ids_are_unique_and_sequential(generated):
    records, _apps, _gt = generated
    ids = [r["id"] for r in records]
    assert len(set(ids)) == len(ids)
    assert sorted(ids) == [f"p-{i + 1:04d}" for i in range(len(ids))]


def test_ids_do_not_leak_duplicate_adjacency(generated):
    """Ids are assigned after shuffling, so a duplicate must not sit next to
    its original. If it did, dedupe could 'win' by comparing id numbers."""
    _records, _apps, gt = generated
    adjacent = 0
    for cluster in gt["duplicate_clusters"]:
        numbers = sorted(int(i.split("-")[1]) for i in cluster["record_ids"])
        adjacent += sum(1 for a, b in zip(numbers, numbers[1:]) if b - a == 1)
    assert adjacent <= 2, "duplicate ids are suspiciously adjacent"


def test_every_noise_class_is_present(generated):
    _records, _apps, gt = generated
    kinds = {c["primary_kind"] for c in gt["duplicate_clusters"]}
    assert kinds == {"exact", "email_variant", "name_variant", "company_variant"}

    all_noise = {n for c in gt["duplicate_clusters"] for n in c["noise"]}
    for expected in ("title_variant", "linkedin_format"):
        assert expected in all_noise

    missing = gt["incomplete_records"]
    for key in ("missing_email", "missing_company", "missing_linkedin_url"):
        assert missing[key], f"no records with {key}"


def test_ground_truth_ids_all_exist(generated):
    records, _apps, gt = generated
    known = {r["id"] for r in records}
    referenced = (
        [i for c in gt["duplicate_clusters"] for i in c["record_ids"]]
        + [i for n in gt["near_miss_pairs"] for i in n["record_ids"]]
        + [i for ids in gt["incomplete_records"].values() for i in ids]
        + [a["id"] for a in gt["applicants"]]
    )
    assert set(referenced) <= known


def test_duplicate_clusters_are_disjoint(generated):
    _records, _apps, gt = generated
    seen: set[str] = set()
    for cluster in gt["duplicate_clusters"]:
        ids = set(cluster["record_ids"])
        assert len(ids) == len(cluster["record_ids"])
        assert not (ids & seen), f"{cluster['cluster_id']} overlaps another cluster"
        seen |= ids


def test_near_miss_pairs_are_not_duplicates(generated):
    """A near-miss pair is two different humans. It must never also appear as a
    duplicate cluster, or the evaluation would be scoring against itself."""
    _records, _apps, gt = generated
    dup_pairs = {
        frozenset((a, b))
        for cluster in gt["duplicate_clusters"]
        for a in cluster["record_ids"]
        for b in cluster["record_ids"]
        if a != b
    }
    for near in gt["near_miss_pairs"]:
        assert frozenset(near["record_ids"]) not in dup_pairs


def test_near_miss_records_are_distinguishable(generated):
    """Every near-miss pair must differ on at least one hard identifier,
    otherwise it is not genuinely resolvable and the label is unfair."""
    records, _apps, gt = generated
    by_id = {r["id"]: r for r in records}
    for near in gt["near_miss_pairs"]:
        a, b = (by_id[i] for i in near["record_ids"])
        differs = any(
            (a[f] or "").strip().lower() != (b[f] or "").strip().lower()
            for f in ("email", "linkedin_url", "company", "full_name")
        )
        assert differs, near["record_ids"]


def test_near_miss_pairs_never_share_a_hard_identifier(generated):
    """A near miss must be separable in principle.

    Two different people sharing one email address is not a hard case, it is an
    impossible one: stage 1 merges on identical email and is right to. Labelling
    such a pair `different people` would score the pipeline down for behaving
    correctly. Same for a shared LinkedIn slug.
    """
    records, _apps, gt = generated
    by_id = {r["id"]: r for r in records}
    for near in gt["near_miss_pairs"]:
        a, b = (by_id[i] for i in near["record_ids"])
        for field in ("email", "linkedin_url"):
            va = (a[field] or "").strip().lower()
            vb = (b[field] or "").strip().lower()
            assert not (va and va == vb),                 f"{near['record_ids']} share {field}={va!r}"


@pytest.mark.parametrize("seed", [42, 777, 2027, 2028, 31337])
def test_no_seed_produces_an_unseparable_near_miss(seed):
    """Checked across seeds, because the collision only shows up on some draws
    and a held-out evaluation is worthless if its ground truth is wrong."""
    records, _apps, gt = gen.generate(seed=seed, canonical_count=gen.DEFAULT_CANONICAL)
    by_id = {r["id"]: r for r in records}
    for near in gt["near_miss_pairs"]:
        a, b = (by_id[i] for i in near["record_ids"])
        email_a = (a["email"] or "").strip().lower()
        email_b = (b["email"] or "").strip().lower()
        assert not (email_a and email_a == email_b),             f"seed {seed}: {near['record_ids']} share {email_a!r}"


def test_applicants_have_one_application_each(generated):
    _records, applications, gt = generated
    applicant_ids = [a["id"] for a in gt["applicants"]]
    assert len(applicant_ids) == len(set(applicant_ids))
    assert sorted(a["person_id"] for a in applications) == sorted(applicant_ids)
    assert {a["band"] for a in gt["applicants"]} <= {"strong", "review", "weak"}


APPLICATION_FIELDS = {"person_id", "building_now", "why_join", "contribution",
                      "referred_by", "submitted_at"}


def test_applications_describe_a_membership_not_a_job(generated):
    _records, applications, _gt = generated
    for application in applications:
        assert set(application) == APPLICATION_FIELDS, application["person_id"]
        assert application["building_now"] and application["why_join"]
        assert application["contribution"]


def test_referrals_point_at_real_records_and_never_at_self(generated):
    records, applications, _gt = generated
    known = {r["id"] for r in records}
    referred = [a for a in applications if a["referred_by"]]
    assert referred, "nobody was referred"
    for application in referred:
        assert application["referred_by"] in known
        assert application["referred_by"] != application["person_id"]


def test_roughly_a_third_of_applicants_are_referred(generated):
    _records, applications, _gt = generated
    share = sum(1 for a in applications if a["referred_by"]) / len(applications)
    assert 0.15 <= share <= 0.50, share


def test_applicants_are_marked_in_the_source_field(generated):
    records, _applications, gt = generated
    applicant_ids = {a["id"] for a in gt["applicants"]}
    for record in records:
        assert (record["source"] == "applicant_form") == (record["id"] in applicant_ids)


def test_a_duplicate_record_never_carries_a_second_application(generated):
    """One human, one application. A duplicated contact record must not become
    a second scored applicant."""
    _records, applications, gt = generated
    applicant_ids = {a["person_id"] for a in applications}
    for cluster in gt["duplicate_clusters"]:
        assert len(applicant_ids & set(cluster["record_ids"])) <= 1


def test_every_band_is_represented(generated):
    _records, _applications, gt = generated
    bands = [a["band"] for a in gt["applicants"]]
    for band in ("strong", "review", "weak"):
        assert bands.count(band) >= 2, f"only {bands.count(band)} {band} applicants"


def test_needs_and_offers_are_lists_of_strings(generated):
    records, _apps, _gt = generated
    for record in records:
        for key in ("needs", "offers"):
            assert isinstance(record[key], list)
            assert all(isinstance(v, str) and v for v in record[key])


def test_committed_data_matches_the_default_seed(generated):
    """The files in data/ are the ones the demo ships with. If someone edits
    the generator without regenerating, this fails loudly."""
    if not RAW_PATH.exists():
        pytest.skip("dataset not generated yet")
    records, _apps, gt = generated
    on_disk = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    assert on_disk == records
    assert json.loads(GT_PATH.read_text(encoding="utf-8")) == gt
