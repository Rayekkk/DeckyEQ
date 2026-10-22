import copy
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckyeq.graph import controls, control_payload, filter_config, headroom, read_controls, same_controls
from deckyeq.model import (EQError, create_preset, decode_import, export_document, flat_eq,
                          initial_state, selected_preset, validate_eq, validate_state)
from deckyeq.storage import PresetFiles, Store


class PresetTests(unittest.TestCase):
    def test_default_is_identity(self):
        eq = flat_eq()
        self.assertEqual(validate_eq(eq), eq)
        self.assertEqual(headroom(eq)["effective_preamp"], 0)
        self.assertEqual(controls(eq)["level:Mult"], 1)

    def test_portable_round_trip_preserves_all_settings(self):
        state = initial_state()
        state["working"]["bands"][3] = {"frequency": 374.5, "gain": -3.5, "q": 2.1, "type": "highshelf"}
        state["working"].update(bass=4, treble=-2.5, preamp=-7, auto_headroom=False)
        preset = create_preset(state, "My sound 🎧", state["working"])
        state["selected"] = preset["id"]
        text = json.dumps(export_document(state))
        self.assertEqual(decode_import(text)[0]["eq"], state["working"])
        self.assertEqual(decode_import(text)[0]["name"], "My sound 🎧")
        self.assertNotIn(preset["id"], text)
        self.assertNotIn("target", text)

    def test_bank_exports_saved_presets_current_exports_draft(self):
        state = initial_state()
        p = create_preset(state, "Warm", flat_eq())
        state["selected"] = p["id"]
        state["working"]["bass"] = 4
        self.assertEqual(export_document(state)["preset"]["eq"]["bass"], 4)
        self.assertEqual(export_document(state, True)["presets"][0]["eq"]["bass"], 0)

    def test_new_names_are_unique_without_overwriting(self):
        state = initial_state()
        first = create_preset(state, "Flat", flat_eq())
        second = create_preset(state, "flat", flat_eq())
        self.assertEqual(first["name"], "Flat (2)")
        self.assertEqual(second["name"], "flat (3)")
        self.assertNotEqual(first["id"], second["id"])

    def test_numeric_rejection(self):
        for value in (float("nan"), float("inf"), True, "3", None, 13, -13):
            with self.subTest(value=value):
                eq = flat_eq()
                eq["bands"][0]["gain"] = value
                with self.assertRaises(EQError): validate_eq(eq)

    def test_bad_width_frequency_and_shape(self):
        for field, value in (("q", 0), ("q", 6), ("frequency", 48001), ("frequency", -1), ("type", "notch")):
            eq = flat_eq()
            eq["bands"][0][field] = value
            with self.assertRaises(EQError): validate_eq(eq)
        eq = flat_eq()
        eq["bands"].pop()
        with self.assertRaises(EQError): validate_eq(eq)
        eq = flat_eq()
        eq["command"] = "anything"
        with self.assertRaises(EQError): validate_eq(eq)

    def test_bad_json_versions_and_nested_inputs(self):
        samples = ['{}', '{"version":true}', '{"version":1,"version":1}', 'NaN', '[' * 2000,
                   '{"version":2,"format":"deckyeq-bank","presets":[]}', 'x' * (256 * 1024 + 1)]
        for sample in samples:
            with self.subTest(sample=sample[:80]):
                with self.assertRaises(EQError): decode_import(sample)

    def test_bank_is_fully_validated_before_import(self):
        bank = {"format": "deckyeq-bank", "version": 2, "presets": [
            {"name": "Valid", "eq": flat_eq()}, {"name": "Bad", "eq": {}}]}
        with self.assertRaises(EQError): decode_import(json.dumps(bank))

    def test_settings_reject_unknown_selection_and_duplicate_ids(self):
        state = initial_state()
        state["selected"] = "missing"
        with self.assertRaises(EQError): validate_state(state)
        state = initial_state()
        p = create_preset(state, "A", flat_eq())
        state["presets"].append(copy.deepcopy(p))
        with self.assertRaises(EQError): validate_state(state)

    def test_limit(self):
        state = initial_state()
        for i in range(64): create_preset(state, f"P{i}", flat_eq())
        with self.assertRaises(EQError): create_preset(state, "Too many", flat_eq())


class GraphTests(unittest.TestCase):
    def test_no_external_dsp_or_shell_in_graph(self):
        config = filter_config(flat_eq(), 'alsa_output.target"quoted', "owner")
        reloaded = json.loads(json.dumps(config))
        args = reloaded["context.modules"][-1]["args"]
        graph = args["filter.graph"]
        self.assertEqual(len(graph["nodes"]), 33)
        self.assertTrue(all(n["type"] == "builtin" for n in graph["nodes"]))
        self.assertEqual(args["audio.position"], ["FL", "FR"])
        self.assertEqual(args["playback.props"]["target.object"], 'alsa_output.target"quoted')
        self.assertTrue(args["playback.props"]["node.dont-fallback"])
        self.assertTrue(args["capture.props"]["node.always-process"])
        self.assertEqual(len(graph["inputs"]), 1)  # auto duplicate graph per channel
        self.assertEqual(len(graph["links"]), 32)

    def test_tone_filters_are_independent_from_ten_bells(self):
        eq = flat_eq()
        eq.update(bass=5, treble=-3)
        values = controls(eq)
        self.assertEqual(values["bass:Gain"], 5)
        self.assertEqual(values["treble:Gain"], -3)
        self.assertEqual(sum(values[f"band{i}:Gain"] for i in range(10)), 0)

    def test_runtime_payload_and_readback_shapes(self):
        expected = controls(flat_eq())
        for params in (expected, control_payload(expected)["params"]):
            node = {"info": {"params": {"Props": [{"params": params}]}}}
            self.assertTrue(same_controls(read_controls(node), expected))
        incomplete = dict(expected)
        incomplete.pop("band8:Gain")
        self.assertFalse(same_controls(incomplete, expected))

    def test_overlapping_band_headroom_bound(self):
        eq = flat_eq()
        for b in eq["bands"]: b.update(frequency=1000, gain=2)
        eq.update(bass=3, treble=2, preamp=-1)
        self.assertAlmostEqual(headroom(eq)["effective_preamp"], -26.5)
        self.assertLess(10 ** (25 / 20) * controls(eq)["level:Mult"], 1)
        eq["auto_headroom"] = False
        self.assertAlmostEqual(headroom(eq)["effective_preamp"], -2)  # 25 dB summed boost, capped at 24.

    def test_pw_dump_rounding_at_low_input_level(self):
        expected = {"level:Mult": 0.008358531817878144}
        self.assertTrue(same_controls({"level:Mult": 0.008359}, expected))
        self.assertFalse(same_controls({"level:Mult": 0.0084}, expected))
        self.assertFalse(same_controls({"level:Mult": 0}, expected))


class HardeningTests(unittest.TestCase):
    def test_unstable_filter_frequencies_are_clamped(self):
        eq = flat_eq()
        for band, hz in zip(eq["bands"], (0, 1, 19.9, 20, 20000, 23999, 24000, 48000, 1000, 1000)):
            band["frequency"] = hz
        self.assertEqual([b["frequency"] for b in validate_eq(eq)["bands"]],
                         [0, 20, 20, 20, 20000, 20000, 24000, 48000, 1000, 1000])

    def test_huge_or_non_finite_numbers_are_rejected(self):
        for value in (10 ** 400, -(10 ** 400), float("nan"), float("inf"), True):
            eq = flat_eq()
            eq["bass"] = value
            with self.assertRaises(EQError): validate_eq(eq)

    def test_unprintable_or_unencodable_names_are_rejected(self):
        for title in ("bad\x7f", "lone \ud800 surrogate", "tab\there"):
            with self.assertRaises(EQError): create_preset(initial_state(), title, flat_eq())

    def test_malformed_selected_preset_is_a_validation_error(self):
        state = initial_state()
        state["selected"] = ["flat"]
        with self.assertRaises(EQError): validate_state(state)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = Store(self.home / "settings")
        self.files = PresetFiles(self.home)

    def test_save_and_recover_previous_good_state(self):
        state = initial_state()
        self.store.save(state)
        state["working"]["bass"] = 3
        self.store.save(state)
        self.store.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.store.load()["working"]["bass"], 0)
        self.assertIn("Recovered", self.store.warning)
        self.assertEqual(self.store.path.read_text(), "{broken")

    def test_malformed_values_fall_back_to_last_good_settings(self):
        self.store.save(initial_state())
        self.store.save(initial_state())
        good = json.dumps(initial_state())
        for text in (good.replace('"selected": "flat"', '"selected": ["flat"]'),
                     good.replace('"bass": 0.0', '"bass": ' + "9" * 400, 1)):
            self.assertNotEqual(text, good)
            self.store.path.write_text(text, encoding="utf-8")
            self.store.warning = None
            self.assertEqual(self.store.load(), initial_state())
            self.assertIn("Recovered", self.store.warning)

    def test_symlink_loop_is_a_validation_error(self):
        loop = self.home / "loop"
        try: loop.symlink_to(loop)
        except OSError: self.skipTest("Host cannot create symbolic links")
        with self.assertRaises(EQError): self.files.read(str(loop / "preset.json"))

    def test_corruption_is_not_silently_reset(self):
        self.store.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(EQError): self.store.load()
        self.assertTrue(self.store.path.exists())

    def test_failed_atomic_commit_keeps_original(self):
        state = initial_state()
        self.store.save(state)
        old = self.store.path.read_bytes()
        state["enabled"] = True
        original = os.replace
        def fail_primary(src, dst):
            if Path(dst) == self.store.path: raise OSError("disk error")
            return original(src, dst)
        with patch("deckyeq.storage.os.replace", side_effect=fail_primary):
            with self.assertRaises(OSError): self.store.save(state)
        self.assertEqual(self.store.path.read_bytes(), old)
        self.assertEqual(len(list(self.store.directory.glob(".settings*"))), 0)

    def test_export_does_not_overwrite_and_is_importable(self):
        document = export_document(initial_state())
        first = self.files.write(document, "../my sound")
        second = self.files.write(document, "../my sound")
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).parent, self.home / "Documents" / "DeckyEQ")
        self.assertEqual(decode_import(self.files.read(first))[0]["eq"], flat_eq())

    def test_file_boundaries(self):
        with self.assertRaises(EQError): self.files.read(str(self.home.parent / "outside.json"))
        with self.assertRaises(EQError): self.files.read("relative.json")
        with self.assertRaises(EQError): self.files.read(str(self.home / "bad.txt"))
        huge = self.home / "huge.json"
        huge.write_bytes(b" " * (256 * 1024 + 1))
        with self.assertRaises(EQError): self.files.read(str(huge))

    def test_symbolic_link_rejected(self):
        actual = self.home / "actual.json"
        actual.write_text("{}")
        link = self.home / "link.json"
        try: link.symlink_to(actual)
        except OSError: self.skipTest("Host cannot create symbolic links")
        with self.assertRaises(EQError): self.files.read(str(link))


if __name__ == "__main__": unittest.main()
