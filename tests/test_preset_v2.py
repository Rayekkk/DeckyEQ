import copy
import json
import math
import unittest
from pathlib import Path

from deckyeq.graph import controls, headroom, shelf_peak
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
        self.assertAlmostEqual(values["level:Mult"], 10 ** (8 / 20))
        self.assertEqual(headroom(eq)["compensation"], 0)
        self.assertEqual(values["band0_low:Gain"], -10)
        self.assertEqual(values["band5_high:Gain"], -5)
        self.assertEqual(values["band0:Gain"], 0)
        self.assertEqual(values["band5:Gain"], 0)

    def test_positive_preamp_is_compensated_only_when_requested(self):
        eq = flat_eq()
        eq["preamp"] = 8
        self.assertEqual(headroom(eq), {"compensation": -8.5, "effective_preamp": -0.5})
        eq["auto_headroom"] = False
        self.assertEqual(headroom(eq), {"compensation": 0, "effective_preamp": 8})

    def test_shelf_bound_covers_independent_sampled_analog_response(self):
        # Evaluate the prototype at s=j*w, independently of the extremum solver.
        # The bilinear transform maps this positive-frequency axis to the digital one.
        for gain in (-12, -5, -0.5, 0, 0.5, 5, 12):
            for q in (0.2, 0.7071067811865476, 1, 2.3, 5):
                a = 10 ** (gain / 40)
                measured = max(0, gain)
                for i in range(2001):
                    s = 1j * 10 ** (-4 + i * 8 / 2000)
                    response = a * (s * s + math.sqrt(a) / q * s + a) / (a * s * s + math.sqrt(a) / q * s + 1)
                    measured = max(measured, 20 * math.log10(abs(response)))
                bound = shelf_peak(gain, q)
                self.assertGreaterEqual(bound + 1e-10, measured, (gain, q))
                self.assertAlmostEqual(bound, measured, delta=0.012, msg=str((gain, q)))

    def test_negative_resonant_shelf_also_requires_headroom(self):
        eq = flat_eq()
        eq["bands"][0].update(type="highshelf", gain=-6, q=5)
        self.assertLess(headroom(eq)["compensation"], -0.5)


if __name__ == "__main__": unittest.main()
