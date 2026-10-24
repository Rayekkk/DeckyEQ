import copy
import json
import math
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from deckyeq.graph import STAGE_LIBRARY, controls, control_payload, filter_config, headroom, read_controls, same_controls
from deckyeq.model import (EQError, name, create_preset, decode_import, export_document, flat_eq, game_name,
                          initial_state, portable_state, presets, selected_preset, validate_eq, validate_state)
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

    def test_clear_voices_and_more_bass_are_flat_with_their_gains_and_need_no_headroom(self):
        expected = {"clear-voices": ("Clear voices", [0, 0, -2, -2, 0, 1, 3, 3, 1, 0],
                                     "Brings dialogue forward: a little less boom, more presence around 2–4 kHz."),
                    "more-bass": ("More bass", [0, 2, 5, 3, 0, 0, 0, 0, 0, 0],
                                  "Fuller low end where small speakers can still play it, around 100–250 Hz.")}
        listed = presets(initial_state())
        self.assertEqual([p["id"] for p in listed], ["flat", "clear-voices", "more-bass", "go-s-original", "legion-go-2"])
        for preset in listed[1:3]:
            title, gains, description = expected[preset["id"]]
            want = flat_eq()
            for band, gain in zip(want["bands"], gains): band["gain"] = gain
            with self.subTest(preset=preset["id"]):
                self.assertEqual((preset["name"], preset["description"], preset["factory"]), (title, description, True))
                self.assertEqual(preset["eq"], want)
                self.assertEqual(validate_eq(preset["eq"]), preset["eq"])
                self.assertEqual(headroom(preset["eq"]), {"compensation": 0.0, "effective_preamp": 0.0})

    def test_user_presets_may_share_a_factory_name_but_never_a_factory_id(self):
        state = initial_state()
        state["presets"].append({"id": "a" * 32, "name": "More bass", "eq": flat_eq()})  # Saved before it was built in.
        loaded = validate_state(state)
        self.assertEqual(loaded["presets"], state["presets"])
        self.assertEqual([p["name"] for p in presets(loaded)].count("More bass"), 2)
        self.assertEqual(create_preset(loaded, "more bass", flat_eq())["name"], "more bass (2)")
        for factory_id in ("clear-voices", "more-bass"):
            state["presets"][0]["id"] = factory_id
            with self.subTest(id=factory_id), self.assertRaises(EQError): validate_state(state)

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

    def test_a_file_of_another_kind_is_not_a_preset_file(self):
        with self.assertRaisesRegex(EQError, r"^This is not a DeckyEQ preset file\.$"):
            decode_import('{"version":2,"format":"something-else"}')

    def test_go_s_description_points_to_input_level(self):
        description = next(p["description"] for p in presets(initial_state()) if p["id"] == "go-s-original")
        self.assertEqual(description, "Community speaker preset by GAZuOOT for the Legion Go S. If it sounds squashed, lower Input level under Equalizer.")

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
        with self.assertRaisesRegex(EQError, r"^The preset library is full\. Delete some presets first\.$"): create_preset(state, "Too many", flat_eq())


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
        self.assertAlmostEqual(headroom(eq)["compensation"], -(peak_gain(eq) - ALLOWANCE), places=4)
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

    def test_game_sounds_round_trip_and_are_validated(self):
        state = initial_state()
        self.assertFalse({"scope", "games", "outside"} & set(portable_state(state)))  # 1.0 files stay as they were.
        state["games"]["1145360"] = {"name": "Hades", "selected": "flat", "working": flat_eq()}
        state["scope"], state["outside"] = "1145360", {"selected": "flat", "working": flat_eq()}
        self.assertEqual(validate_state(json.loads(json.dumps(portable_state(state)))), state)
        for key, value in (("scope", "730"), ("scope", ["x"]), ("outside", None), ("games", {"x1": state["games"]["1145360"]}),
                           ("games", {"1": {"name": "", "selected": "flat", "working": flat_eq()}})):
            with self.subTest(key=key, value=value), self.assertRaises(EQError):
                validate_state({**state, key: value})
        odd = {**state, "games": {"1": {"name": "x", "selected": ["flat"], "working": flat_eq()}}, "scope": "", "outside": None}
        self.assertEqual(validate_state(odd)["games"]["1"]["selected"], "flat")  # Not a TypeError.

    def test_a_full_library_of_presets_and_games_is_still_readable(self):
        state = initial_state()
        eq = flat_eq()
        for band in eq["bands"]:
            band["gain"], band["q"] = 3.3000000000000003, 1.2345678901234567
        state["presets"] = [{"id": f"{i:032x}", "name": f"{i} " + "ゲ" * 36, "eq": eq} for i in range(64)]
        state["games"] = {str(10 ** 15 + i): {"name": "ゲ" * 128, "selected": f"{i % 64:032x}", "working": eq} for i in range(200)}
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            store.save(state)
            self.assertGreater((Path(temp) / "settings.json").stat().st_size, 512 * 1024)
            store.save(state)  # Refreshes the backup from the large file.
            self.assertEqual(store.load()["games"], state["games"])

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

    def test_a_name_with_del_saved_by_1_0_still_loads(self):
        # DeckyEQ 1.0 accepted DEL; refusing it on load would make the whole settings file unreadable.
        self.assertEqual(name("Old\x7f", stored=True), "Old\x7f")

    def test_invisible_and_bidi_characters_are_refused_in_new_names_only(self):
        disguised = ["Fl​at", "‮Flat", "Flat⁦", "﻿Flat", "Flat‏", "C1 \x85 control",
                     # Drawn as nothing: Hangul fillers, the Braille blank, combining marks alone.
                     "\u3164", "\u2800", "\u115f", "\u1160", "\uffa0", "\u0301\u0301", " \u3164 \u20dd",
                     "Line\u2028break", "Para\u2029break", "Private \ue000", "Unassigned \u0378"]
        for title in disguised:
            with self.assertRaises(EQError): create_preset(initial_state(), title, flat_eq())
            document = export_document(initial_state())
            document["preset"]["name"] = title
            with self.assertRaises(EQError): decode_import(json.dumps(document))
        self.assertEqual(create_preset(initial_state(), "My sound 🎧 ünï", flat_eq())["name"], "My sound 🎧 ünï")
        self.assertEqual(create_preset(initial_state(), "Cafe\u0301 ㄱ", flat_eq())["name"], "Cafe\u0301 ㄱ")
        flag = "Night \U0001f3f3\ufe0f\u200d\U0001f308"  # An emoji joined by a ZWJ, as the Steam keyboard types it.
        self.assertEqual(create_preset(initial_state(), flag, flat_eq())["name"], flag)
        for joined in ("Flat\u200d", "\u200dFlat", "Fl\u200d\u200dat", "Fl \u200d at"):  # Joining nothing drawn.
            with self.assertRaises(EQError): create_preset(initial_state(), joined, flat_eq())
        # A library saved before the rule still loads, names unchanged.
        state = initial_state()
        state["presets"] = [{"id": f"{i:032x}", "name": title, "eq": flat_eq()} for i, title in enumerate(disguised)]
        self.assertEqual([p["name"] for p in validate_state(state)["presets"]], [t.strip() for t in disguised])

    def test_game_titles_lose_disguising_characters_and_saved_ones_still_load(self):
        self.assertEqual(game_name("\u202eHades\u200b"), "Hades")
        self.assertEqual(game_name("Fam \U0001f468\u200d\U0001f469"), "Fam \U0001f468\u200d\U0001f469")
        with self.assertRaises(EQError): game_name("\u2066\u200b")  # Nothing drawn is left.
        state = initial_state()
        state["games"]["7"] = {"name": "\u202eHades", "selected": "flat", "working": flat_eq()}
        self.assertEqual(validate_state(state)["games"]["7"]["name"], "\u202eHades")

    def test_an_output_name_that_cannot_be_saved_is_refused(self):
        with self.assertRaisesRegex(EQError, "audio output"):
            validate_state({**initial_state(), "target": "alsa_output.\ud800"})  # A lone surrogate: no UTF-8.

    def test_duplicate_keys_in_a_preset_file_are_refused(self):
        document = json.dumps(export_document(initial_state()))
        with self.assertRaisesRegex(EQError, "Duplicate"):
            decode_import(document[:-1] + ', "format": "deckyeq-bank"}')

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

    def test_a_saved_user_preset_named_like_a_factory_preset_loads_untouched(self):
        state = initial_state()
        state["presets"].append({"id": "b" * 32, "name": "More bass", "eq": flat_eq()})
        state["selected"] = "b" * 32
        self.store.save(state)
        self.assertEqual(self.store.load(), state)
        self.assertIsNone(self.store.warning)

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
            with self.assertRaisesRegex(EQError, r"^Settings could not be saved\. Check free space\.$"): self.store.save(state)
        self.assertEqual(self.store.path.read_bytes(), old)
        self.assertEqual(len(list(self.store.directory.glob(".settings*"))), 0)

    def test_export_does_not_overwrite_and_is_importable(self):
        document = export_document(initial_state())
        first = self.files.write(document, "../my sound")
        second = self.files.write(document, "../my sound")
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).parent, self.home / "Documents" / "DeckyEQ")
        self.assertEqual(decode_import(self.files.read(first))[0]["eq"], flat_eq())

    def test_file_errors_are_short_and_name_no_path(self):
        # Toasts can be seen on a stream: not "[Errno 13] Permission denied: '/home/deck/...'".
        document = export_document(initial_state())
        for error in (PermissionError(13, "Permission denied", str(self.home / "Flat.json")),
                      OSError(30, "Read-only file system", str(self.home / "Flat.json"))):
            with patch("deckyeq.storage.os.open", side_effect=error):
                with self.assertRaisesRegex(EQError, r"^Cannot save there\. Choose another folder\.$"):
                    self.files.write(document, "Flat")
        with patch("deckyeq.storage.os.open", side_effect=PermissionError(13, "Permission denied", str(self.home / "x.json"))):
            with self.assertRaisesRegex(EQError, r"^That file cannot be read\. Choose another one\.$"):
                self.files.read(str(self.home / "x.json"))
        (self.home / "file").write_text("x")
        for gone in (self.home / "gone.json", self.home / "file" / "x.json"):  # ENOENT, ENOTDIR
            with self.assertRaisesRegex(EQError, r"^That file is no longer there\. Choose another one\.$"):
                self.files.read(str(gone))
        (self.home / "folder.json").mkdir()
        with self.assertRaisesRegex(EQError, r"^Choose a regular preset file\.$" if os.name == "posix" else
                                    r"^That file cannot be read\. Choose another one\.$"):  # Windows: EACCES.
            self.files.read(str(self.home / "folder.json"))

    @unittest.skipUnless(os.name == "posix", "file modes and FIFOs are POSIX")
    def test_exports_are_private_and_a_fifo_never_blocks_an_import(self):
        path = Path(self.files.write(export_document(initial_state()), "Flat"))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        fifo = self.home / "pipe.json"
        os.mkfifo(fifo)
        done = []
        reader = threading.Thread(target=lambda: done.append(self.assertRaises(EQError, self.files.read, str(fifo))))
        reader.start()
        reader.join(5)
        blocked = reader.is_alive()
        if blocked:  # Opened without O_NONBLOCK: it waits for a writer. Give it one so the test ends.
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            reader.join(5)
        self.assertFalse(blocked, "reading a FIFO blocked")
        self.assertTrue(done)

    def test_a_linked_settings_file_is_not_followed(self):
        elsewhere = self.home / "elsewhere.json"
        elsewhere.write_text(json.dumps({**initial_state(), "enabled": True}))
        try: self.store.path.symlink_to(elsewhere)
        except OSError: self.skipTest("Host cannot create symbolic links")
        with self.assertRaisesRegex(EQError, "could not be read"): self.store.load()

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
