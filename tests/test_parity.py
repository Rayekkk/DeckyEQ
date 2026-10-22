"""Vectors that the TypeScript tests replay (tests/parity.test.ts), so the panel's optimistic
values match the backend. Regenerate after an intended change with:
DECKYEQ_UPDATE_FIXTURES=1 python -m unittest tests.test_parity"""
import json
import os
import random
import unittest
from pathlib import Path

from deckyeq.graph import headroom
from deckyeq.model import FILTER_TYPES, LIMITS, flat_eq, frequency

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "parity.json"


def random_eq(rng):
    eq = flat_eq()
    value = lambda span: round(rng.uniform(*span), 2)
    for band in eq["bands"]:
        # Any valid frequency, including the clamped 0 Hz / >= Nyquist values the Go S preset uses.
        frequency = rng.choice([value(LIMITS["stable_frequency"]), rng.choice([0, 24000, 40000, 48000])])
        band.update(type=rng.choice(FILTER_TYPES), gain=value(LIMITS["band_gain"]), q=value(LIMITS["q"]),
                    frequency=float(frequency))
    eq.update(bass=value(LIMITS["tone_gain"]), treble=value(LIMITS["tone_gain"]),
              preamp=value(LIMITS["preamp"]), auto_headroom=rng.random() < 0.5,
              width=rng.choice([1.0, value(LIMITS["width"])]), limiter=rng.random() < 0.5)
    return eq


def vectors():
    rng = random.Random(7)
    eqs = [random_eq(rng) for _ in range(60)]
    hz = [0, 0.5, 6, 19.99, 20, 31.25, 1000, 19999, 20000, 20000.5, 23999, 24000, 40000, 48000]
    boosts = [rng.choice([0.0, 0.0, 6.0]) for _ in eqs]
    return {"headroom": [{"eq": eq, "boost": b, "expected": headroom(eq, b)} for eq, b in zip(eqs, boosts)],
            "frequency": [{"hz": value, "expected": frequency(value)} for value in hz],
            "flat": flat_eq()}


class ParityTests(unittest.TestCase):
    def test_frontend_parity_vectors_match_the_backend(self):
        data = json.loads(json.dumps(vectors()))
        if os.environ.get("DECKYEQ_UPDATE_FIXTURES"):
            FIXTURE.write_bytes((json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8"))
        self.assertEqual(data, json.loads(FIXTURE.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
