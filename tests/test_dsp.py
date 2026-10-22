"""The native stereo stage (bin/deckyeq_dsp.so), driven through its LADSPA ABI."""
import ctypes
import json
import math
import random
import sys
import unittest
from pathlib import Path

LIBRARY = Path(__file__).resolve().parent.parent / "bin" / "deckyeq_dsp.so"
RATE = 48000
IN_L, IN_R, OUT_L, OUT_R, WIDTH, LIMIT, CEILING, RELEASE, GAIN, REDUCTION, LATENCY = range(11)


class Hint(ctypes.Structure):
    _fields_ = [("hint", ctypes.c_int), ("lower", ctypes.c_float), ("upper", ctypes.c_float)]


class Descriptor(ctypes.Structure):
    _fields_ = [("UniqueID", ctypes.c_ulong), ("Label", ctypes.c_char_p), ("Properties", ctypes.c_int),
                ("Name", ctypes.c_char_p), ("Maker", ctypes.c_char_p), ("Copyright", ctypes.c_char_p),
                ("PortCount", ctypes.c_ulong), ("PortDescriptors", ctypes.POINTER(ctypes.c_int)),
                ("PortNames", ctypes.POINTER(ctypes.c_char_p)), ("PortRangeHints", ctypes.POINTER(Hint)),
                ("ImplementationData", ctypes.c_void_p),
                ("instantiate", ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)),
                ("connect_port", ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_float))),
                ("activate", ctypes.CFUNCTYPE(None, ctypes.c_void_p)),
                ("run", ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ulong)),
                ("run_adding", ctypes.c_void_p), ("set_run_adding_gain", ctypes.c_void_p),
                ("deactivate", ctypes.c_void_p),
                ("cleanup", ctypes.CFUNCTYPE(None, ctypes.c_void_p))]


class Stage:
    def __init__(self, width=1.0, limit=True, ceiling=-1.0, release=100.0, rate=RATE, gain=0.0):
        lib = ctypes.CDLL(str(LIBRARY))
        lib.ladspa_descriptor.restype = ctypes.POINTER(Descriptor)
        self.d = lib.ladspa_descriptor(0).contents
        self.lib = lib
        self.handle = self.d.instantiate(ctypes.byref(self.d), rate)
        self.controls = {i: ctypes.c_float(v) for i, v in
                         ((WIDTH, width), (LIMIT, float(limit)), (CEILING, ceiling), (RELEASE, release), (GAIN, gain),
                          (REDUCTION, 0.0), (LATENCY, 0.0))}
        for port, value in self.controls.items():
            self.d.connect_port(self.handle, port, ctypes.pointer(value))
        self.d.activate(self.handle)

    def process(self, left, right, block=256):
        out_l, out_r = [], []
        for start in range(0, len(left), block):
            n = len(left[start:start + block])
            bufs = [(ctypes.c_float * n)(*left[start:start + n]), (ctypes.c_float * n)(*right[start:start + n]),
                    (ctypes.c_float * n)(), (ctypes.c_float * n)()]
            for port, buf in zip((IN_L, IN_R, OUT_L, OUT_R), bufs):
                self.d.connect_port(self.handle, port, ctypes.cast(buf, ctypes.POINTER(ctypes.c_float)))
            self.d.run(self.handle, n)
            out_l += list(bufs[2])
            out_r += list(bufs[3])
        return out_l, out_r

    def close(self):
        self.d.cleanup(self.handle)


def sine(freq, amplitude, seconds=1.0):
    return [amplitude * math.sin(2 * math.pi * freq * n / RATE) for n in range(int(RATE * seconds))]


def thd(signal, freq):
    def power(f):
        w = 2 * math.pi * f / RATE
        re = sum(x * math.cos(w * i) for i, x in enumerate(signal))
        im = sum(x * math.sin(w * i) for i, x in enumerate(signal))
        return re * re + im * im
    return math.sqrt(sum(power(freq * k) for k in range(2, 6)) / power(freq))


@unittest.skipUnless(sys.platform == "linux", "The audio stage is a Linux shared library.")
class StageTests(unittest.TestCase):
    def setUp(self):
        if not LIBRARY.is_file():
            self.fail(f"{LIBRARY} is missing; run python scripts/build_native.py first.")
        self.stages = []

    def tearDown(self):
        for stage in self.stages:
            stage.close()

    def stage(self, **kwargs):
        stage = Stage(**kwargs)
        self.stages.append(stage)
        return stage

    def test_quiet_audio_passes_unchanged_after_a_fixed_delay(self):
        stage = self.stage()
        rng = random.Random(1)
        left = [rng.uniform(-0.5, 0.5) for _ in range(4000)]
        right = [rng.uniform(-0.5, 0.5) for _ in range(4000)]
        out_l, out_r = stage.process(left, right)
        delay = int(stage.controls[LATENCY].value)
        self.assertEqual(delay, round(0.0015 * RATE) - 1)
        for a, b in zip(out_l[delay:], left):
            self.assertAlmostEqual(a, b, places=6)
        for a, b in zip(out_r[delay:], right):
            self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(stage.controls[REDUCTION].value, 0.0)

    def test_loud_audio_never_exceeds_the_ceiling(self):
        rng = random.Random(2)
        for ceiling in (-1.0, -3.0, 0.0):
            stage = self.stage(ceiling=ceiling, release=20)
            limit = 10 ** (ceiling / 20) + 1e-6
            # Bursts up to +12 dBFS, isolated spikes and silence.
            left = [rng.uniform(-4, 4) if (n // 500) % 3 else (4.0 if n % 997 == 0 else 0.0) for n in range(20000)]
            right = [rng.uniform(-1, 1) for _ in range(20000)]
            out_l, out_r = stage.process(left, right, block=129)
            self.assertLessEqual(max(map(abs, out_l + out_r)), limit)
            self.assertLess(stage.controls[REDUCTION].value, -1.0)

    def test_a_boosted_bass_tone_stays_clean(self):
        # +6 dB over full scale at 70 Hz: the speaker-killing case CAPS distorted by 8–12%.
        stage = self.stage()
        tone = sine(70, 2.0, 1.5)
        out_l, _ = stage.process(tone, tone)
        steady = out_l[RATE // 2:RATE // 2 + RATE // 2]
        self.assertLessEqual(max(map(abs, steady)), 10 ** (-1 / 20) + 1e-6)
        self.assertLess(thd(steady, 70), 0.02)
        self.assertGreater(max(map(abs, steady)), 10 ** (-1.5 / 20))  # Loud, not squashed.

    def test_limiter_off_only_delays(self):
        stage = self.stage(limit=False)
        tone = sine(1000, 2.0, 0.1)
        out_l, _ = stage.process(tone, tone)
        self.assertAlmostEqual(max(map(abs, out_l)), 2.0, places=3)

    def test_width_scales_the_side_signal(self):
        left, right = sine(500, 0.4, 0.2), sine(700, 0.3, 0.2)
        for width in (0.0, 1.0, 2.0):
            stage = self.stage(width=width)
            out_l, out_r = stage.process(left, right)
            delay = int(stage.controls[LATENCY].value)
            for n in range(2000, 4000):  # Past the 10 ms glide.
                mid, side = (left[n] + right[n]) / 2, (left[n] - right[n]) / 2 * width
                self.assertAlmostEqual(out_l[n + delay], mid + side, places=5)
                self.assertAlmostEqual(out_r[n + delay], mid - side, places=5)

    def test_opposite_phase_grows_by_the_full_width(self):
        # The case headroom must budget for: L = -R has no mid, so widening scales all of it.
        stage = self.stage(width=2.0, limit=False)
        left = sine(300, 0.4, 0.2)
        out_l, _ = stage.process(left, [-x for x in left])
        self.assertAlmostEqual(max(map(abs, out_l[2000:])), 0.8, places=3)

    def test_boost_raises_quiet_audio_and_the_limiter_still_holds(self):
        stage = self.stage(gain=6.0, ceiling=0.0)
        quiet, loud = sine(1000, 0.2, 0.3), sine(1000, 0.9, 0.3)
        out_l, _ = stage.process(quiet, quiet)
        self.assertAlmostEqual(max(map(abs, out_l[RATE // 10:])), 0.2 * 10 ** (6 / 20), places=3)
        out_l, _ = stage.process(loud, loud)
        self.assertLessEqual(max(map(abs, out_l)), 1.0 + 1e-6)

    def test_dropping_boost_and_limiter_together_never_clips(self):
        # Reset to 0 dB on a preset whose limiter is off: both controls change in one write.
        stage = self.stage(gain=12.0, ceiling=0.0)
        tone = sine(200, 0.9, 0.3)
        stage.process(tone, tone)
        stage.controls[GAIN].value = 0.0
        stage.controls[LIMIT].value = 0.0
        out_l, out_r = stage.process(tone, tone, block=64)
        self.assertLessEqual(max(map(abs, out_l + out_r)), 1.0 + 1e-6)
        self.assertAlmostEqual(max(map(abs, out_l[-2000:])), 0.9, places=3)  # Unboosted again.

    def test_control_ranges_match_the_shared_limits(self):
        limits = json.loads((LIBRARY.parent.parent / "deckyeq" / "limits.json").read_text(encoding="utf-8"))
        stage = self.stage()
        bounds = lambda port: [stage.d.PortRangeHints[port].lower, stage.d.PortRangeHints[port].upper]
        self.assertEqual(bounds(WIDTH), limits["width"])
        self.assertEqual(bounds(GAIN), limits["boost"])

    def test_boost_changes_glide_instead_of_stepping(self):
        stage = self.stage(ceiling=0.0)
        level = [0.2] * 4800  # +12 dB stays under full scale, so only the glide shapes it.
        stage.process(level, level)
        stage.controls[GAIN].value = 12.0
        out_l, _ = stage.process(level, level)
        steps = [abs(b - a) for a, b in zip(out_l, out_l[1:])]
        self.assertLess(max(steps), 0.01)
        self.assertAlmostEqual(out_l[-1], 0.2 * 10 ** (12 / 20), places=3)

    def test_width_changes_glide_instead_of_stepping(self):
        stage = self.stage(width=1.0)
        left, right = [0.5] * 4800, [-0.5] * 4800  # Pure side signal; 100 ms is ten glide time constants.
        stage.process(left, right)
        stage.controls[WIDTH].value = 0.0
        out_l, _ = stage.process(left, right)
        steps = [abs(b - a) for a, b in zip(out_l, out_l[1:])]
        self.assertLess(max(steps), 0.01)
        self.assertAlmostEqual(out_l[-1], 0.0, places=3)

    def test_non_finite_input_does_not_poison_the_stage(self):
        stage = self.stage()
        stage.process([math.nan, math.inf] + [0.1] * 500, [0.1] * 502)
        out_l, _ = stage.process([0.2] * 500, [0.2] * 500)
        self.assertTrue(all(math.isfinite(x) for x in out_l))
        self.assertAlmostEqual(out_l[-1], 0.2, places=5)

    def test_other_sample_rates_and_single_frame_blocks(self):
        rng = random.Random(3)
        for rate in (44100, 96000, 192000):
            stage = self.stage(rate=rate, ceiling=-1.0)
            loud = [rng.uniform(-4, 4) for _ in range(3000)]
            out_l, out_r = stage.process(loud, loud[::-1], block=1)
            self.assertLessEqual(max(map(abs, out_l + out_r)), 10 ** (-1 / 20) + 1e-6, rate)
            self.assertEqual(int(stage.controls[LATENCY].value), round(0.0015 * rate) - 1)

    def test_port_defaults_match_the_settings_dsp_relies_on(self):
        stage = self.stage()
        hints = [stage.d.PortRangeHints[i].hint & 0x3C0 for i in range(stage.d.PortCount)]
        # Middle of 0..2 (width 1), 1 (limit on), maximum (ceiling 0 dB), 100 (release ms).
        self.assertEqual(hints[WIDTH:GAIN + 1], [0xC0, 0x240, 0x140, 0x280, 0x200])
        self.assertEqual(stage.d.PortRangeHints[LIMIT].hint & 0x4, 0)  # A number, not a boolean.

    def test_an_invalid_width_at_activation_falls_back_to_unchanged(self):
        stage = Stage(width=math.nan)
        self.stages.append(stage)
        stage.controls[WIDTH].value = 1.0
        out_l, _ = stage.process([0.3] * 1000, [0.1] * 1000)
        self.assertAlmostEqual(out_l[-1], 0.3, places=5)

    def test_switching_the_limiter_on_holds_the_ceiling_at_once(self):
        stage = self.stage(limit=False)
        loud = sine(200, 3.0, 0.2)
        stage.process(loud, loud)
        stage.controls[LIMIT].value = 1.0
        out_l, _ = stage.process(loud, loud, block=1)
        ceiling = 10 ** (-1 / 20)
        self.assertLessEqual(max(map(abs, out_l)), ceiling + 1e-6)
        # Turned down by the tracked gain, not flattened by the safety clamp.
        self.assertLess(sum(abs(abs(x) - ceiling) < 1e-6 for x in out_l[:200]), 3)

    def test_gain_returns_exactly_to_unity_after_limiting(self):
        stage = self.stage(release=10)
        for _ in range(20):
            stage.process(sine(100, 4.0, 0.5), sine(100, 4.0, 0.5), block=512)
        quiet = sine(1000, 0.3, 0.5)
        out_l, _ = stage.process(quiet, quiet)
        delay = int(stage.controls[LATENCY].value)
        self.assertAlmostEqual(max(map(abs, out_l[RATE // 4:])), 0.3, places=6)
        self.assertAlmostEqual(out_l[-1], quiet[-1 - delay], places=6)


if __name__ == "__main__":
    unittest.main()
