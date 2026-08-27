"""Calibrate the generator's unobserved component.

    python backend/scripts/calibrate_bands.py

Answers one question: if a Phase 4 rubric perfectly recovered every observable
dimension, how often would it agree with the ground-truth band?

That number is the ceiling on band agreement, and it must not be near 100. A
ceiling of 100 would mean the band is an exact function of the rubric's own
inputs, and any accuracy figure reported against it would be measuring the
generator rather than the rubric.

This script is a calibration tool, not an evaluation. It does not read
ground_truth.json and it produces no metric that belongs in a report.
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.scripts import generate_data as gen  # noqa: E402


def main() -> None:
    rng = random.Random(1234)
    records, _apps, _gt = gen.generate(seed=42, canonical_count=gen.DEFAULT_CANONICAL)

    agree = 0
    trials = 20000
    bands: Counter = Counter()
    perfect_bands: Counter = Counter()

    people = [gen.build_person(rng, f"c{i:04d}") for i in range(400)]
    for _ in range(trials):
        person = rng.choice(people)
        traction = rng.choice(("high", "mid", "low"))
        referred = rng.random() < 0.3

        observable = gen.observable_score(person, traction, referred)
        true_band = gen.band_for(observable + gen.unobserved_component(rng))
        perfect_band = gen.band_for(observable)

        bands[true_band] += 1
        perfect_bands[perfect_band] += 1
        agree += true_band == perfect_band

    ceiling = agree / trials
    print(f"trials                              {trials:,}")
    print(f"unobserved sd                       {gen.UNOBSERVED_SD}")
    print(f"flag probability                    {gen.UNOBSERVED_FLAG_PROBABILITY}")
    print()
    print(f"ceiling on band agreement           {ceiling:.3f}")
    print("  (a rubric that recovers every observable dimension perfectly")
    print("   still disagrees with the band this often)")
    print()
    print(f"band mix, with the unobserved part  {dict(bands)}")
    print(f"band mix, observable only           {dict(perfect_bands)}")

    if ceiling > 0.88:
        print("\n!! too little noise: the band is nearly a function of the rubric's inputs")
    elif ceiling < 0.65:
        print("\n!! too much noise: the observable signal barely predicts the band")
    else:
        print("\nOK: agreement in the 70s-80s is what a real rubric should reach")


if __name__ == "__main__":
    main()
