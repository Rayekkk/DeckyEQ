import copy
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckyeq.graph import STAGE_LIBRARY, controls, control_payload, filter_config, headroom, read_controls, same_controls
from deckyeq.model import (EQError, create_preset, decode_import, export_document, flat_eq,
                          initial_state, portable_state, selected_preset, validate_eq, validate_state)
from deckyeq.storage import PresetFiles, Store


class PresetTests(unittest.TestCase):
    def test_default_is_identity(self):
        eq = flat_eq()
        self.assertEqual(validate_eq(eq), eq)
        self.assertEqual(headroom(eq)["effective_preamp"], 0)
        self.assertEqual(controls(eq)["stage:level"], 0)

    def test_portable_round_trip_preserves_all_settings(self):
        state = initial_state()
        state["working"]["bands"][3] = {"frequency": 374.5, "gain": -3.5, "q": 1.2, "type": "highshelf"}
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
        self.assertEqual(len(graph["nodes"]), 65)  # 32 native filters per channel and the stereo stage.
        self.assertEqual([n["label"] for n in graph["nodes"][:32]],
                         ["bq_lowshelf"] + ["bq_peaking", "bq_lowshelf", "bq_highshelf"] * 10 + ["bq_highshelf"])
        external = [n for n in graph["nodes"] if n["type"] != "builtin"]
        self.assertEqual(external, [{"type": "ladspa", "name": "stage", "plugin": str(STAGE_LIBRARY),
                                     "label": "deckyeq_stage", "control": {"level": 0.0, "width": 1.0, "gain": 0.0,
                                                                           "ceiling": -1.0, "release": 100.0}}])
        from deckyeq.graph import neutral
        self.assertEqual({k: v for k, v in neutral().items() if k.endswith(":Gain") or k.startswith("stage:")},
                         {**{k: 0.0 for k in neutral() if k.endswith(":Gain")},
                          "stage:level": 0.0, "stage:width": 1.0, "stage:gain": 0.0})  # Leaves the sound as it is.
        self.assertEqual(args["audio.position"], ["FL", "FR"])
        self.assertEqual(args["capture.props"]["filter.smart.target"], {"node.name": 'alsa_output.target"quoted'})
        self.assertTrue(args["capture.props"]["filter.smart"])
        self.assertNotIn("target.object", args["playback.props"])  # WirePlumber links the filter itself.
        self.assertNotIn("node.always-process", args["capture.props"])  # Suspends with its output.
        self.assertEqual(graph["inputs"], ["bass_l:In", "bass_r:In"])
        self.assertEqual(graph["outputs"], ["stage:out_l", "stage:out_r"])
        self.assertEqual(len(graph["links"]), 2 * 32)  # Each channel's chain, ending in the stage.

    def test_tone_filters_are_independent_from_ten_bells(self):
        eq = flat_eq()
        eq.update(bass=5, treble=-3)
        values = controls(eq)
        self.assertEqual(values["bass_l:Gain"], 5)
        self.assertEqual(values["treble_l:Gain"], -3)
        self.assertEqual(sum(values[f"band{i}_l:Gain"] for i in range(10)), 0)

    def test_runtime_payload_and_readback_shapes(self):
        expected = controls(flat_eq())
        for params in (expected, control_payload(expected)["params"]):
            node = {"info": {"params": {"Props": [{"params": params}]}}}
            self.assertTrue(same_controls(read_controls(node), expected))
        incomplete = dict(expected)
        incomplete.pop("band8_l:Gain")
        self.assertFalse(same_controls(incomplete, expected))

    def test_headroom_follows_the_combined_response(self):
        from deckyeq.graph import ALLOWANCE, peak_gain
        eq = flat_eq()
        eq.update(bass=9, treble=9)  # Different frequency ranges: the peak is ~9 dB, not 18.
        self.assertAlmostEqual(peak_gain(eq), 9, places=1)
        self.assertAlmostEqual(headroom(eq)["compensation"], -(peak_gain(eq) - ALLOWANCE), places=4)
        eq = flat_eq()
        for b in eq["bands"]: b.update(frequency=1000, gain=2)  # Stacked on one frequency: they do add up.
        eq.update(bass=3, treble=2, preamp=-1)
        self.assertGreater(peak_gain(eq), 20)
        self.assertAlmostEqual(headroom(eq)["compensation"], -(peak_gain(eq) - 1 - ALLOWANCE), places=3)
        eq["auto_headroom"] = False  # An old preset field, no longer read: headroom is always on.
        self.assertAlmostEqual(headroom(eq)["compensation"], -(peak_gain(eq) - 1 - ALLOWANCE), places=3)

    def test_the_limiter_keeps_moderate_boosts_loud(self):
        from deckyeq.graph import ALLOWANCE, peak_gain
        eq = flat_eq()
        eq.update(bass=6)  # Up to ALLOWANCE dB the limiter catches peaks: no level drop at all.
        self.assertEqual(headroom(eq)["compensation"], 0)
        eq.update(bass=9)  # Beyond it, only the excess is taken off.
        self.assertAlmostEqual(headroom(eq)["compensation"], -(peak_gain(eq) - ALLOWANCE), places=4)
        eq.update(limiter=False)  # An old preset field, no longer used: same result.
        self.assertAlmostEqual(headroom(eq)["compensation"], -(peak_gain(eq) - ALLOWANCE), places=4)

    def test_input_level_boost_and_width_are_never_taken_back(self):
        # The reported problem: Go S at +12 dB input, 200% width, "make room" played at -13 dB.
        from deckyeq.graph import ALLOWANCE, peak_gain
        eq = flat_eq()
        eq.update(bass=9, preamp=12, width=2.0)
        self.assertAlmostEqual(headroom(eq, boost=6)["compensation"], -(peak_gain(eq) - ALLOWANCE), places=4)
        self.assertGreater(headroom(eq)["effective_preamp"], 8)
        eq.update(preamp=-12)  # A lowered input level is room: no extra drop on top of it.
        self.assertEqual(headroom(eq)["compensation"], 0)

    def test_boost_sets_the_stage_gain_and_extreme_cuts_stay_in_range(self):
        eq = flat_eq()
        self.assertEqual((controls(eq)["stage:gain"], controls(eq, 6)["stage:gain"]), (0.0, 6.0))
        eq.update(preamp=-36, limiter=False, auto_headroom=True)
        for band in eq["bands"]:
            band.update(frequency=1000, gain=12)  # Compensation far below the stage's range.
        self.assertEqual(controls(eq)["stage:level"], -96.0)
        self.assertEqual(headroom(eq)["effective_preamp"], -96.0)  # The panel shows what is applied.

    def test_width_never_lowers_the_level(self):
        eq = flat_eq()
        eq.update(width=2.0)  # The limiter catches rare opposite-phase peaks instead.
        self.assertEqual(headroom(eq)["compensation"], 0)
        eq.update(width=0.5)
        self.assertEqual(headroom(eq)["compensation"], 0)
        self.assertEqual(controls(eq)["stage:width"], 0.5)

    def test_settings_carry_the_boost_only_when_raised(self):
        state = initial_state()
        self.assertNotIn("boost", portable_state(state))
        state["boost"] = 4.5
        self.assertEqual(portable_state(state)["boost"], 4.5)
        self.assertEqual(validate_state(portable_state(state))["boost"], 4.5)
        state["boost"] = 13
        with self.assertRaises(EQError):
            validate_state(state)

    def test_files_carry_width_and_limiter_only_when_changed(self):
        # DeckyEQ 1.0 accepts exactly the older keys: unchanged presets and settings stay readable to it.
        state = initial_state()
        self.assertNotIn("width", export_document(state)["preset"]["eq"])
        state["working"] = {**flat_eq(), "width": 1.5, "limiter": False}
        exported = export_document(state)["preset"]["eq"]
        self.assertEqual((exported["width"], exported["limiter"]), (1.5, False))
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "settings")
            store.save(state)
            written = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(written["working"]["width"], 1.5)
            state["working"] = flat_eq()
            store.save(state)
            self.assertNotIn("width", json.loads(store.path.read_text(encoding="utf-8"))["working"])
            self.assertEqual(json.loads(store.backup.read_text(encoding="utf-8"))["working"]["width"], 1.5)
            self.assertEqual(store.load()["working"], flat_eq())

    def test_shelf_q_is_capped_where_it_would_resonate(self):
        eq = flat_eq()
        eq["bands"][0].update(type="lowshelf", q=5.0, gain=-12)
        eq["bands"][1].update(q=5.0)  # A bell may stay narrow.
        cleaned = validate_eq(eq)
        self.assertEqual((cleaned["bands"][0]["q"], cleaned["bands"][1]["q"]), (1.4, 5.0))

    def test_preset_ids_must_be_text(self):
        state = initial_state()
        state["presets"] = [{"id": int("1" * 32), "name": "Numbers", "eq": flat_eq()}]
        with self.assertRaises(EQError):
            validate_state(state)

    def test_presets_without_width_or_limiter_get_the_defaults(self):
        eq = flat_eq()
        del eq["width"], eq["limiter"]
        self.assertEqual(validate_eq(eq), flat_eq())
        eq["width"] = 3
        with self.assertRaises(EQError):
            validate_eq(eq)
        eq.update(width=1, limiter="yes")
        with self.assertRaises(EQError):
            validate_eq(eq)

    def test_pw_dump_rounding_at_low_input_level(self):
        expected = {"level_l:Mult": 0.008358531817878144}
        self.assertTrue(same_controls({"level_l:Mult": 0.008359}, expected))
        self.assertFalse(same_controls({"level_l:Mult": 0.0084}, expected))
        self.assertFalse(same_controls({"level_l:Mult": 0}, expected))


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

    def test_links_above_home_are_the_systems_business(self):
        # Fedora Atomic links /home to /var/home; an SD card can hold ~/homebrew. Only our own
        # folders, and links inside the home folder for file exchange, are refused.
        real = self.home / "var-home"
        (real / "deck" / "Documents").mkdir(parents=True)
        linked = self.home / "home"
        try: linked.symlink_to(real, target_is_directory=True)
        except OSError: self.skipTest("Host cannot create symbolic links")
        files = PresetFiles(str(linked / "deck"))
        preset = real / "deck" / "Documents" / "mine.json"
        preset.write_text("{}")
        self.assertEqual(files.read(str(linked / "deck" / "Documents" / "mine.json")), "{}")
        store = Store(linked / "deck" / "settings")
        store.save(initial_state())
        self.assertTrue((real / "deck" / "settings").is_dir())
        (real / "deck" / "elsewhere").mkdir()
        (real / "deck" / "own").symlink_to(real / "deck" / "elsewhere", target_is_directory=True)
        with self.assertRaises(EQError): Store(linked / "deck" / "own")
        (real / "deck" / "Documents" / "alias.json").symlink_to(preset)
        with self.assertRaises(EQError): files.read(str(linked / "deck" / "Documents" / "alias.json"))


if __name__ == "__main__": unittest.main()
