import copy
import json
import math
import unittest
from pathlib import Path

from deckyeq.graph import controls, headroom, peak_gain, response_db
from deckyeq.model import (EQError, create_preset, decode_import, export_document,
                          flat_eq, go_s_eq, initial_state, validate_state)


def legacy_state():
    state = initial_state()
    state.update(enabled=True, target="alsa_output.saved", speakers_only=False)
    state["working"].update(bass=3, preamp=-6)
    state["working"]["bands"][2].update(gain=-2, q=2.3, frequency=147.5)
    item = create_preset(state, "Existing sound", state["working"])
    state["selected"] = item["id"]
    state["working"]["treble"] = 2  # Preserve the unsaved draft too.
    state["version"] = 1
    for eq in [state["working"], *(p["eq"] for p in state["presets"])]:
        for band in eq["bands"]: del band["type"]
    return state


class MigrationTests(unittest.TestCase):
    def test_v1_state_keeps_library_draft_output_and_selection(self):
        old = legacy_state()
        migrated = validate_state(old)
        self.assertEqual(migrated["version"], 2)
        for eq in [migrated["working"], *(p["eq"] for p in migrated["presets"])]:
            for band in eq["bands"]:
                self.assertEqual(band.pop("type"), "peaking")
        migrated["version"] = 1
        self.assertEqual(migrated, old)

    def test_v1_import_and_v2_export_preserve_values(self):
        old = legacy_state()
        document = {"format": "deckyeq-preset", "version": 1,
                    "preset": {"name": "Old", "eq": old["working"]}}
        imported = decode_import(json.dumps(document))[0]["eq"]
        state = validate_state(old)
        self.assertEqual(imported, state["working"])
        exported = export_document(state)
        self.assertEqual(exported["version"], 2)
        self.assertEqual(decode_import(json.dumps(exported))[0]["eq"], imported)

    def test_legacy_limits_are_not_silently_reinterpreted(self):
        for field, value in (("preamp", 8), ("frequency", 0), ("type", "lowshelf")):
            state = legacy_state()
            if field == "preamp": state["working"][field] = value
            else: state["working"]["bands"][0][field] = value
            with self.assertRaises(EQError): validate_state(state)


class OriginalPresetTests(unittest.TestCase):
    def test_distributed_preset_matches_factory_and_native_parsed_values(self):
        # Find the package, also when this test is staged elsewhere on Linux.
        import deckyeq
        path = Path(deckyeq.__file__).parent.parent / "presets/legion-go-s-original.json"
        self.assertEqual(decode_import(path.read_text(encoding="utf-8"))[0]["eq"], go_s_eq())
        eq = go_s_eq()
        self.assertEqual([b["frequency"] for b in eq["bands"]],
                         [31.25, 0, 125, 250, 500, 1000, 2000, 40000, 48000, 16000])
        self.assertEqual([b["gain"] for b in eq["bands"]], [-10, -4, 5, 5, -5, -5, -5, -5, 0, 0])
        self.assertTrue(all(b["q"] == 1 for b in eq["bands"]))
        values = controls(eq)
        self.assertAlmostEqual(values["level_l:Mult"], 10 ** (8 / 20))
        self.assertEqual(headroom(eq)["compensation"], 0)
        self.assertEqual(values["band0_low_l:Gain"], -10)
        self.assertEqual(values["band5_high_l:Gain"], -5)
        self.assertEqual(values["band0_l:Gain"], 0)
        self.assertEqual(values["band5_l:Gain"], 0)

    def test_positive_preamp_is_compensated_only_when_requested(self):
        eq = flat_eq()
        eq["preamp"] = 8  # With the limiter, the input level is deliberate gain: kept.
        self.assertEqual(headroom(eq), {"compensation": 0.0, "effective_preamp": 8.0})
        eq["limiter"] = False
        self.assertEqual(headroom(eq), {"compensation": -8.5, "effective_preamp": -0.5})
        eq["auto_headroom"] = False
        self.assertEqual(headroom(eq), {"compensation": 0, "effective_preamp": 8})

    def test_filter_response_matches_the_filter_definitions(self):
        self.assertAlmostEqual(response_db("peaking", 1000, 1.4, 6, [1000])[0], 6, places=6)
        low, high = response_db("lowshelf", 1000, 0.7071, 6, [20, 20000])
        self.assertAlmostEqual(low, 6, places=1)
        self.assertAlmostEqual(high, 0, places=1)
        low, high = response_db("highshelf", 1000, 0.7071, -6, [20, 20000])
        self.assertAlmostEqual(low, 0, places=1)
        self.assertAlmostEqual(high, -6, places=1)
        # Frequencies PipeWire clamps to 0 Hz or Nyquist become constant gains.
        self.assertEqual(response_db("lowshelf", 48000, 1, 4, [100]), [4])
        self.assertEqual(response_db("peaking", 40000, 1, 4, [100]), [0])
    def test_manual_headroom_caps_extreme_boost(self):
        eq = flat_eq()
        eq.update(auto_headroom=False, preamp=12)
        for band in eq["bands"]:
            band["gain"] = 12
        compensation = headroom(eq)["compensation"]
        self.assertLess(compensation, 0)
        from deckyeq.graph import MAX_BOOST
        self.assertAlmostEqual(eq["preamp"] + peak_gain(eq) + compensation, MAX_BOOST, places=3)

    def test_peak_matches_a_dense_brute_force_scan(self):
        import random
        from deckyeq.graph import NYQUIST, SHELF_Q, biquad, gain_db
        from deckyeq.model import FILTER_TYPES
        def brute(eq):
            filters = [(b["type"], b["frequency"], b["q"], b["gain"]) for b in eq["bands"] if b["gain"]]
            filters += [("lowshelf", 120.0, SHELF_Q, eq["bass"]), ("highshelf", 6000.0, SHELF_Q, eq["treble"])]
            designs = [biquad(*f) for f in filters]
            points = [NYQUIST * i / 6000 for i in range(6001)] + [10 * 1.002 ** i for i in range(4000)]
            return max(sum(gain_db(d, hz) for d in designs) for hz in points if hz <= NYQUIST)
        eq = flat_eq()  # The audit's case: the true peak is at 0 Hz, below the 10 Hz grid start.
        eq["bands"][0].update(type="lowshelf", gain=12, frequency=31.5)
        eq["bands"][1].update(gain=-12, q=0.2, frequency=20)
        self.assertAlmostEqual(peak_gain(eq), 12, places=3)
        rng = random.Random(11)
        for _ in range(8):
            eq = flat_eq()
            for band in eq["bands"]:
                band.update(type=rng.choice(FILTER_TYPES), gain=rng.uniform(-12, 12), q=rng.uniform(0.2, 5),
                            frequency=rng.uniform(20, 20000))
            eq.update(bass=rng.uniform(-9, 9), treble=rng.uniform(-9, 9))
            self.assertGreaterEqual(peak_gain(eq) + 0.01, brute(eq))

    def test_negative_resonant_shelf_also_requires_headroom(self):
        eq = flat_eq()
        eq["bands"][0].update(type="highshelf", gain=-6, q=5)
        self.assertLess(headroom(eq)["compensation"], -0.5)


if __name__ == "__main__": unittest.main()
