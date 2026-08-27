"""Applicant fit scoring. The numbers are computed here, in Python, always.

Offline vets people to join a network. The question is not "can this person do a
job" but "is the network better with them in it", and the answer has to be
consistent, explainable, and identical on a re-run — three things an LLM asked
for a score is bad at.

So the split is absolute:

* **Code computes every number.** Five weighted components, a total, a band.
  Same inputs, same output, forever.
* **The LLM receives the breakdown and nothing else.** Not the bio, not the
  company, not the name. `explanation_input` is a dataclass of numbers and rule
  names, and it is the only thing that reaches the prompt. The model cannot
  introduce a figure it was not given because it never sees a figure it was not
  given.

That second point is structural rather than instructed. A prompt that says
"don't invent numbers" while handing over the whole record is a hope. A prompt
that only contains the breakdown is a guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

CONFIG = {
    # The rubric. Weights sum to 100 and live in one place.
    "WEIGHTS": {
        "persona_fit": 30,
        "seniority": 20,
        "company_stage": 20,
        "referral_signal": 15,
        "profile_signal": 15,
    },
    # strong >= 75, review 55-74, weak below.
    "BANDS": (("strong", 75), ("review", 55)),

    # Offline vets founders. Operators who have run something at scale are the
    # next best thing; an individual contributor is not what the network is for,
    # which is a statement about fit, not about the person.
    "PERSONA_POINTS": {
        "founder": 30, "operator": 20, "investor": 14,
        "service_provider": 10, "ic": 8, "unknown": 6,
    },
    # Seniority as "how much have you actually run", not job-title inflation.
    "SENIORITY_POINTS": {
        "c_level": 20, "founder": 18, "vp": 16, "director": 13,
        "head_or_lead": 12, "senior_ic": 9, "mid": 5, "junior": 2, "unknown": 4,
    },
    # An investor or an advisor has no startup stage; scoring them zero would
    # punish them for the question not applying.
    "STAGE_POINTS": {
        "growth": 20, "public": 19, "series_b": 18, "series_a": 15,
        "seed": 10, "pre_seed": 5, "not_applicable": 12, "unknown": 6,
    },
    "REFERRAL_POINTS": {
        "member": 15,        # referred by someone already in the network
        "unknown_referrer": 8,   # a name we cannot resolve
        "none": 4,
    },
    # Profile signal: how much substance there is to judge. Completeness of the
    # record, plus whether the application says anything concrete.
    "PROFILE_COMPLETENESS_MAX": 8,
    "PROFILE_TRACTION_POINTS": 4,
    "PROFILE_CONTRIBUTION_POINTS": 3,
}

COMPONENTS = tuple(CONFIG["WEIGHTS"])

# A number with a unit attached is a claim. "We grew a lot" is not.
_TRACTION = re.compile(
    r"(\d[\d,.]*\s*(?:crore|lakh|cr|k|m|mn|million|%)|[$₹]\s*\d|\d+\s*(?:customers|"
    r"people|users|employees|accounts|pilots))", re.IGNORECASE)


@dataclass
class Component:
    name: str
    points: float
    max_points: int
    signal: str          # the rule that fired, in plain words
    basis: str           # where the input came from

    @property
    def share(self) -> float:
        return self.points / self.max_points if self.max_points else 0.0


@dataclass
class ScoreBreakdown:
    """Everything the explanation is allowed to know.

    Deliberately contains no name, company, bio or email. This object is the
    entire input to the prompt, so the model physically cannot cite a detail of
    the record or invent a number that is not here.
    """

    person_id: str
    components: list[Component]
    total: float
    band: str

    @property
    def kind(self) -> str:
        return "why_not" if self.band == "weak" else "why"

    def component(self, name: str) -> Component:
        return next(c for c in self.components if c.name == name)

    def as_dict(self) -> dict:
        return {
            "person_id": self.person_id,
            "total": self.total,
            "band": self.band,
            "components": [
                {"name": c.name, "points": c.points, "out_of": c.max_points,
                 "signal": c.signal}
                for c in self.components
            ],
        }

    def strongest(self, n: int = 2) -> list[Component]:
        return sorted(self.components, key=lambda c: -c.share)[:n]

    def weakest(self, n: int = 2) -> list[Component]:
        return sorted(self.components, key=lambda c: c.share)[:n]


def band_for(total: float) -> str:
    for name, floor in CONFIG["BANDS"]:
        if total >= floor:
            return name
    return "weak"


# ---------------------------------------------------------------------------
# The components
# ---------------------------------------------------------------------------


def _persona_fit(enrichment: dict | None) -> Component:
    persona = (enrichment or {}).get("persona") or "unknown"
    points = CONFIG["PERSONA_POINTS"].get(persona, CONFIG["PERSONA_POINTS"]["unknown"])
    signal = {
        "founder": "founder — the network's core persona",
        "operator": "senior operator, not a founder",
        "investor": "investor rather than an operator",
        "service_provider": "sells services into the network",
        "ic": "individual contributor — not who this network is for",
        "unknown": "persona could not be determined from the record",
    }.get(persona, "persona could not be determined from the record")
    return Component("persona_fit", points, CONFIG["WEIGHTS"]["persona_fit"],
                     signal, f"enrichment.persona={persona}")


def _seniority(enrichment: dict | None) -> Component:
    seniority = (enrichment or {}).get("seniority") or "unknown"
    points = CONFIG["SENIORITY_POINTS"].get(
        seniority, CONFIG["SENIORITY_POINTS"]["unknown"])
    signal = ("seniority not evidenced in the record" if seniority == "unknown"
              else f"operating seniority: {seniority.replace('_', ' ')}")
    return Component("seniority", points, CONFIG["WEIGHTS"]["seniority"],
                     signal, f"enrichment.seniority={seniority}")


def _company_stage(enrichment: dict | None) -> Component:
    stage = (enrichment or {}).get("company_stage") or "unknown"
    points = CONFIG["STAGE_POINTS"].get(stage, CONFIG["STAGE_POINTS"]["unknown"])
    signal = {
        "not_applicable": "no company stage applies to this role",
        "unknown": "company stage not stated anywhere in the record",
    }.get(stage, f"company at {stage.replace('_', ' ')}")
    return Component("company_stage", points, CONFIG["WEIGHTS"]["company_stage"],
                     signal, f"enrichment.company_stage={stage}")


def _referral(application: dict | None, member_ids: set[str]) -> Component:
    referred_by = (application or {}).get("referred_by")
    if not referred_by:
        key, signal = "none", "applied cold, no referral"
    elif referred_by in member_ids:
        key, signal = "member", "referred by an existing member"
    else:
        key, signal = "unknown_referrer", "referrer named but not found in the network"
    points = CONFIG["REFERRAL_POINTS"][key]
    return Component("referral_signal", points, CONFIG["WEIGHTS"]["referral_signal"],
                     signal, f"application.referred_by={referred_by or 'null'}")


def _profile_signal(completeness: float, application: dict | None) -> Component:
    points = round(completeness * CONFIG["PROFILE_COMPLETENESS_MAX"], 2)
    parts = [f"profile {completeness:.0%} complete"]

    building = (application or {}).get("building_now") or ""
    if _TRACTION.search(building):
        points += CONFIG["PROFILE_TRACTION_POINTS"]
        parts.append("application states concrete traction")
    else:
        parts.append("no concrete traction figure given")

    if ((application or {}).get("contribution") or "").strip():
        points += CONFIG["PROFILE_CONTRIBUTION_POINTS"]
        parts.append("says what they would contribute")
    else:
        parts.append("does not say what they would contribute")

    return Component("profile_signal", round(points, 2),
                     CONFIG["WEIGHTS"]["profile_signal"],
                     "; ".join(parts), "record completeness + application text")


def score_applicant(*, person_id: str, enrichment: dict | None,
                    application: dict | None, completeness: float,
                    member_ids: set[str]) -> ScoreBreakdown:
    """Compute the fit score. Pure arithmetic over already-derived inputs."""
    components = [
        _persona_fit(enrichment),
        _seniority(enrichment),
        _company_stage(enrichment),
        _referral(application, member_ids),
        _profile_signal(completeness, application),
    ]
    total = round(sum(c.points for c in components), 2)
    return ScoreBreakdown(person_id=person_id, components=components,
                          total=total, band=band_for(total))


# ---------------------------------------------------------------------------
# The explanation. Numbers in, prose out.
# ---------------------------------------------------------------------------

EXPLANATION_PROMPT = """\
You are writing the reviewer's note for a membership application.

You are given ONLY a scoring breakdown. You do not have the applicant's name,
company, bio or any other detail, and you must not invent one. Every number you
mention must appear in the breakdown below. Do not recompute the total, do not
round it differently, and do not argue with it.

Write in the second person, addressed to a colleague reviewing the queue.

{tone}

Return JSON:
{schema}

BREAKDOWN
  band: {band}
  total: {total} out of 100
  components:
{components}
"""

TONE_WHY = """\
Write two sentences summarising why this application scores where it does, then
3 to 4 bullets naming the specific components that drove it. Lead with the
strongest components. Name the weakest one honestly -- a note that only lists
positives is useless to a reviewer.\
"""

TONE_WHY_NOT = """\
This application scored below the bar. Write two sentences a reviewer could
paste into a polite decline, then 3 to 4 bullets explaining what was missing.

Be plain and respectful. Do not be apologetic, do not pad, and do not imply a
future reconsideration that nobody has promised. Say what was thin, in terms of
the components. Never suggest the person is not good at what they do -- the
score is about fit with this network, not merit.\
"""


def explanation_input(breakdown: ScoreBreakdown) -> dict:
    """The complete, and only, input to the explanation prompt."""
    return breakdown.as_dict()


def build_explanation_prompt(breakdown: ScoreBreakdown, schema_json: str) -> str:
    """Render the prompt. Takes a breakdown -- never a record.

    The signature is the guarantee: there is no parameter through which a name,
    a company or a bio could reach the model.
    """
    lines = "\n".join(
        f"    - {c.name}: {c.points} of {c.max_points} — {c.signal}"
        for c in breakdown.components)
    return EXPLANATION_PROMPT.format(
        tone=TONE_WHY_NOT if breakdown.kind == "why_not" else TONE_WHY,
        schema=schema_json,
        band=breakdown.band,
        total=breakdown.total,
        components=lines,
    )


BATCH_PROMPT = """You are writing reviewer notes for several membership applications.

You are given ONLY scoring breakdowns. You do not have any applicant's name,
company, bio or any other detail, and you must not invent one. Every number you
mention must appear in that applicant's breakdown. Do not recompute the total,
do not round it differently, and do not argue with it.

{tone}

Return one object per applicant, keyed by person_id, as JSON:
{schema}

BREAKDOWNS
{breakdowns}
"""


def build_batch_explanation_prompt(breakdowns: Sequence[ScoreBreakdown],
                                   schema_json: str) -> str:
    """One call for several applicants of the SAME kind.

    Kind is not mixed within a batch because the tone instruction differs: a
    review note and a decline are different pieces of writing, and asking for
    both at once produces something that is neither.
    """
    kinds = {b.kind for b in breakdowns}
    if len(kinds) > 1:
        raise ValueError(f"cannot mix explanation kinds in one batch: {sorted(kinds)}")

    blocks = []
    for breakdown in breakdowns:
        lines = "\n".join(
            f"      - {c.name}: {c.points} of {c.max_points} - {c.signal}"
            for c in breakdown.components)
        blocks.append(f"  person_id: {breakdown.person_id}\n"
                      f"    band: {breakdown.band}\n"
                      f"    total: {breakdown.total} out of 100\n"
                      f"    components:\n{lines}")

    return BATCH_PROMPT.format(
        tone=TONE_WHY_NOT if kinds == {"why_not"} else TONE_WHY,
        schema=schema_json,
        breakdowns="\n\n".join(blocks),
    )


@dataclass
class ScoredApplicant:
    breakdown: ScoreBreakdown
    explanation: str = ""
    bullets: list[str] = field(default_factory=list)
    explanation_kind: str = "why"

    @property
    def person_id(self) -> str:
        return self.breakdown.person_id


def numbers_in(text: str) -> set[str]:
    """Every number appearing in a piece of prose, for the honesty check."""
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def allowed_numbers(breakdown: ScoreBreakdown) -> set[str]:
    allowed = {str(breakdown.total), str(int(breakdown.total)), "100"}
    for component in breakdown.components:
        allowed |= {str(component.points), str(int(component.points)),
                    str(component.max_points)}
        allowed |= numbers_in(component.signal)
    for _name, floor in CONFIG["BANDS"]:
        allowed.add(str(floor))
    return allowed


def unsupported_numbers(breakdown: ScoreBreakdown, text: str) -> set[str]:
    """Numbers the explanation used that the breakdown never supplied.

    Should always be empty: the model is only shown the breakdown. Checked
    anyway, because "should" is not a guarantee and this costs a regex.
    """
    return numbers_in(text) - allowed_numbers(breakdown)


def summarise(applicants: Sequence[ScoredApplicant]) -> dict:
    bands = {"strong": 0, "review": 0, "weak": 0}
    for applicant in applicants:
        bands[applicant.breakdown.band] += 1
    return {
        "applicants": len(applicants),
        "bands": bands,
        "explained": sum(1 for a in applicants if a.explanation),
        "why_not": sum(1 for a in applicants if a.explanation_kind == "why_not"),
    }
