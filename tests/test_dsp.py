"""The native stereo stage (bin/deckyeq_dsp.so), driven through its LADSPA ABI."""
import ctypes
import json
import math
import operator
import random
import sys
import unittest
from pathlib import Path

LIBRARY = Path(__file__).resolve().parent.parent / "bin" / "deckyeq_dsp.so"
RATE = 48000
IN_L, IN_R, OUT_L, OUT_R, LEVEL, WIDTH, CEILING, RELEASE, GAIN, REDUCTION, LATENCY = range(11)


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
    def __init__(self, width=1.0, ceiling=-1.0, release=100.0, rate=RATE, gain=0.0, level=0.0):
        lib = ctypes.CDLL(str(LIBRARY))
        lib.ladspa_descriptor.restype = ctypes.POINTER(Descriptor)
        self.d = lib.ladspa_descriptor(0).contents
        self.lib = lib
        self.handle = self.d.instantiate(ctypes.byref(self.d), rate)
        self.controls = {i: ctypes.c_float(v) for i, v in
                         ((LEVEL, level), (WIDTH, width), (CEILING, ceiling), (RELEASE, release), (GAIN, gain),
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


def latency(rate=RATE):
    """Two look-ahead limiters in series, the second with the true-peak detector's DETECT_LAG."""
    size = round(0.0015 * rate)
    return (size - 1) + (size - 1 + 4)


def rms_db(xs):
    return 10 * math.log10(sum(x * x for x in xs) / len(xs))


def tone_level(signal, freq):
    """Amplitude of one frequency (a DFT bin), whatever its phase."""
    w = 2 * math.pi * freq / RATE
    re = sum(x * math.cos(w * i) for i, x in enumerate(signal))
    im = sum(x * math.sin(w * i) for i, x in enumerate(signal))
    return 2 * math.hypot(re, im) / len(signal)


def thd(signal, freq):
    def power(f):
        w = 2 * math.pi * f / RATE
        re = sum(x * math.cos(w * i) for i, x in enumerate(signal))
        im = sum(x * math.sin(w * i) for i, x in enumerate(signal))
        return re * re + im * im
    return math.sqrt(sum(power(freq * k) for k in range(2, 6)) / power(freq))


def lanczos(x, a):
    return 1.0 if abs(x) < 1e-12 else 0.0 if abs(x) >= a else a * math.sin(math.pi * x) * math.sin(math.pi * x / a) / (math.pi * x) ** 2


# 64 taps (Lanczos, a = 32) for every eighth of the way between two samples: shorter kernels miss
# most of the peaks of bright material, whose energy sits near Nyquist.
INTERPOLATE = [[lanczos(f / 8 - k, 32) for k in range(-31, 33)] for f in range(1, 8)]


def between(signal):
    """The magnitudes a DAC reconstructs between samples, every eighth of the way."""
    for n in range(31, len(signal) - 32):
        window = signal[n - 31:n + 33]
        for weights in INTERPOLATE:
            yield abs(sum(map(operator.mul, weights, window)))


def true_peak_db(signal):
    return 20 * math.log10(max(max(map(abs, signal)), max(between(signal))))


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

    def test_quiet_audio_keeps_its_level_after_a_fixed_delay(self):
        # The bass split only turns the phase of the lowest notes: levels are untouched.
        stage = self.stage()
        rng = random.Random(1)
        left = [rng.uniform(-0.5, 0.5) for _ in range(9600)]
        right = [rng.uniform(-0.5, 0.5) for _ in range(9600)]
        out_l, out_r = stage.process(left, right)
        self.assertEqual(int(stage.controls[LATENCY].value), latency())
        self.assertAlmostEqual(rms_db(out_l[2000:]), rms_db(left[2000:]), delta=0.05)
        self.assertAlmostEqual(rms_db(out_r[2000:]), rms_db(right[2000:]), delta=0.05)
        tone = sine(1000, 0.5, 0.3)
        out_l, _ = stage.process(tone, tone)
        self.assertAlmostEqual(tone_level(out_l[4800:4800 + 480], 1000), 0.5, places=3)
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
        # Far over full scale at low frequencies: the hold keeps the gain from tracing each cycle.
        # (CAPS measured 8–58% here.) A bass note alone is held 3 dB below the ceiling, leaving
        # room for everything above the bass.
        for freq, amplitude, limit in ((70, 2.0, 0.002), (40, 2.0, 0.002), (40, 4.0, 0.003), (25, 2.0, 0.002), (20, 2.0, 0.002)):
            stage = self.stage()
            tone = sine(freq, amplitude, 1.5)
            out_l, _ = stage.process(tone, tone)
            whole = round(int(0.5 * freq) * RATE / freq)  # Whole cycles only: no DFT leakage.
            steady = out_l[RATE // 2:RATE // 2 + whole]
            self.assertLessEqual(max(map(abs, steady)), 10 ** (-1 / 20) + 1e-6)
            self.assertLess(thd(steady, freq), limit, (freq, amplitude))
            self.assertGreater(max(map(abs, steady)), 10 ** (-4.5 / 20))  # Loud, not squashed.

    def test_a_muted_start_is_silent_from_the_first_frame(self):
        # A host that starts the stage muted (as DeckyEQ 1.0 did) gets no full-level blip first.
        stage = self.stage(level=-96.0)
        out_l, _ = stage.process([0.9] * 256, [0.9] * 256)
        self.assertLess(max(map(abs, out_l)), 1e-4)

    def test_level_glides_and_fades_to_silence(self):
        stage = self.stage(level=-6.0)
        tone = sine(1000, 0.5, 0.2)
        out_l, _ = stage.process(tone, tone)
        self.assertAlmostEqual(max(map(abs, out_l[RATE // 10:])), 0.5 * 10 ** (-6 / 20), places=3)
        stage.controls[LEVEL].value = -96.0  # Muting fades, it does not cut.
        out_l, _ = stage.process(tone, tone)
        steps = [abs(b - a) for a, b in zip(out_l, out_l[1:])]
        self.assertLess(max(steps), 0.2)  # A fade, not a cut.
        self.assertLess(max(map(abs, out_l[int(0.08 * RATE):])), 0.002)

    def test_width_scales_the_side_signal(self):
        # Steady levels pass the bass split unchanged once it settles, so mid/side maths shows exactly.
        for width in (0.0, 1.0, 2.0):
            stage = self.stage(width=width)
            out_l, out_r = stage.process([0.4] * 9600, [0.1] * 9600)
            mid, side = 0.25, 0.15 * width
            self.assertAlmostEqual(out_l[-1], mid + side, places=4)
            self.assertAlmostEqual(out_r[-1], mid - side, places=4)

    def test_opposite_phase_grows_by_the_full_width(self):
        # The case headroom must budget for: L = -R has no mid, so widening scales all of it.
        stage = self.stage(width=2.0, ceiling=0.0)
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

    def test_dropping_the_boost_never_clips(self):
        stage = self.stage(gain=12.0, ceiling=0.0)
        tone = sine(200, 0.9, 0.3)
        stage.process(tone, tone)
        stage.controls[GAIN].value = 0.0
        tone = sine(200, 0.9, 0.8)  # Long enough for the 100 ms release to recover fully.
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
        tone = sine(1000, 0.2, 0.2)  # +12 dB stays under full scale, so only the glide shapes it.
        stage.process(tone, tone)
        stage.controls[GAIN].value = 12.0
        out_l, _ = stage.process(tone, tone)
        envelope = [tone_level(out_l[i:i + 48], 1000) for i in range(0, len(out_l) - 48, 48)]
        steps = [abs(b - a) for a, b in zip(envelope, envelope[1:])]
        self.assertLess(max(steps), 0.08)  # Rises over several milliseconds, not in one step.
        self.assertAlmostEqual(envelope[-1], 0.2 * 10 ** (12 / 20), places=2)

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
        out_l, _ = stage.process([0.2] * 4800, [0.2] * 4800)
        self.assertTrue(all(math.isfinite(x) for x in out_l))
        self.assertAlmostEqual(out_l[-1], 0.2, places=4)

    def test_other_sample_rates_and_single_frame_blocks(self):
        rng = random.Random(3)
        for rate in (44100, 96000, 192000):
            stage = self.stage(rate=rate, ceiling=-1.0)
            loud = [rng.uniform(-4, 4) for _ in range(3000)]
            out_l, out_r = stage.process(loud, loud[::-1], block=1)
            self.assertLessEqual(max(map(abs, out_l + out_r)), 10 ** (-1 / 20) + 1e-6, rate)
            self.assertEqual(int(stage.controls[LATENCY].value), latency(rate))

    def test_port_defaults_match_the_settings_dsp_relies_on(self):
        stage = self.stage()
        hints = [stage.d.PortRangeHints[i].hint & 0x3C0 for i in range(stage.d.PortCount)]
        # 0 dB level, middle of 0..2 (width 1), maximum (ceiling 0 dB), 100 (release ms), 0 dB boost.
        self.assertEqual(hints[LEVEL:GAIN + 1], [0x200, 0xC0, 0x140, 0x280, 0x200])
        # Numbers, not TOGGLED: PipeWire reports toggled ports as booleans, which the readback ignores.
        self.assertFalse(any(stage.d.PortRangeHints[i].hint & 0x4 for i in range(stage.d.PortCount)))

    def test_an_invalid_width_at_activation_falls_back_to_unchanged(self):
        stage = Stage(width=math.nan)
        self.stages.append(stage)
        stage.controls[WIDTH].value = 1.0
        out_l, _ = stage.process([0.3] * 4800, [0.1] * 4800)
        self.assertAlmostEqual(out_l[-1], 0.3, places=4)

    def test_peaks_between_samples_stay_under_the_ceiling(self):
        # A quarter-sample-rate tone at 45 degrees: every sample is 3 dB below its true peak.
        stage = self.stage(ceiling=-1.0)
        tone = [1.6 * math.sin(math.pi / 2 * n + math.pi / 4) for n in range(9600)]
        out_l, _ = stage.process(tone, tone)
        true_peak = true_peak_db(out_l[4800:6800])
        self.assertLess(true_peak, -1.0 + 0.3)  # Within the detector's interpolation error.
        self.assertGreater(true_peak, -2.0)  # Limited, not squashed.

    def test_the_right_channel_alone_is_limited_by_both_detectors(self):
        # Each detector must watch the right channel too. A bass note is held 3 dB below the ceiling;
        # a quarter-sample-rate tone at 22.5 or 67.5 degrees peaks 1/4 or 3/4 of the way between samples.
        stage = self.stage()
        _, out_r = stage.process([0.0] * RATE, sine(40, 2.0))
        self.assertLess(max(map(abs, out_r[RATE // 2:])), 10 ** (-3 / 20))
        for phase in (math.pi / 8, 3 * math.pi / 8):
            stage = self.stage()
            tone = [1.6 * math.sin(math.pi / 2 * n + phase) for n in range(9600)]
            out_l, out_r = stage.process([0.0] * 9600, tone)
            self.assertLess(true_peak_db(out_r[4800:6800]), -1.0 + 0.3, phase)
            self.assertEqual(max(map(abs, out_l)), 0.0)

    def test_bright_clipped_noise_boosted_12_db_keeps_its_peaks_between_samples_bounded(self):
        # 8 taps only approximate peaks between samples: this pins how far they go today, so a detector
        # that sees less fails. Regression bounds over measured values, not targets.
        rng = random.Random(6)
        def bright(n=24000):
            out, last = [], 0.0
            for _ in range(n):  # Clipped first differences of loud noise: most energy near Nyquist.
                now = 3 * rng.gauss(0.0, 1.0)
                out.append(max(-1.0, min(1.0, now - last)))
                last = now
            return out
        stage = self.stage(gain=12.0)
        out_l, out_r = stage.process(bright(), bright())
        points = [x for out in (out_l[4800:], out_r[4800:]) for x in between(out)]
        self.assertLess(20 * math.log10(max(points)), 2.5)  # Measured +2.0 dBTP.
        self.assertLess(sum(x > 1.0 for x in points), 1000)  # Over 0 dBFS, 1 dB over the ceiling: measured 843.

    def test_the_stage_itself_caps_the_boost_at_12_db(self):
        stage = self.stage(gain=24.0, ceiling=0.0)
        tone = sine(1000, 0.05, 0.2)
        out_l, _ = stage.process(tone, tone)
        self.assertAlmostEqual(max(map(abs, out_l[RATE // 10:])), 0.05 * 10 ** (12 / 20), places=3)

    def test_with_both_limiters_idle_the_stage_is_flat_and_delayed_by_its_latency(self):
        # The two bands must add up exactly: one delayed or filtered differently leaves a dip near 150 Hz.
        stage = self.stage()
        impulse = [0.5] + [0.0] * 8191
        for out in stage.process(impulse, impulse):
            self.assertEqual(max(range(len(out)), key=lambda n: abs(out[n])), int(stage.controls[LATENCY].value))
            for freq in (20, 50, 100, 150, 180, 250, 500, 1000, 3000, 10000, 20000):
                response = tone_level(out, freq) * len(out) / (2 * 0.5)  # The DFT of the impulse response.
                self.assertAlmostEqual(20 * math.log10(response), 0.0, delta=0.01, msg=freq)

    def test_a_loud_bass_note_does_not_turn_down_the_rest(self):
        # The reported problem: every bass hit ducked voices by up to 7.8 dB with one limiter.
        def mix(seconds=3.0):
            out = []
            for n in range(int(RATE * seconds)):
                t = n / RATE
                bass = 0.45 * math.exp(-((t * 2) % 1) * 6) * math.sin(2 * math.pi * 70 * t)
                out.append(bass + 0.1 * math.sin(2 * math.pi * 1000 * t))
            return out
        stage = self.stage(level=11.4)
        x = mix()
        out_l, _ = stage.process(x, x)
        envelope = [tone_level(out_l[i:i + 960], 1000) for i in range(RATE, len(out_l) - 960, 960)]
        reference = 0.1 * 10 ** (11.4 / 20)
        self.assertLess(20 * math.log10(reference / min(envelope)), 3.0)
        self.assertLessEqual(max(map(abs, out_l)), 10 ** (-1 / 20) + 1e-6)

    def test_gain_returns_exactly_to_unity_after_limiting(self):
        stage = self.stage(release=10)
        for _ in range(20):
            stage.process(sine(100, 4.0, 0.5), sine(100, 4.0, 0.5), block=512)
        quiet = sine(1000, 0.3, 0.5)
        out_l, _ = stage.process(quiet, quiet)
        self.assertAlmostEqual(tone_level(out_l[RATE // 4:RATE // 4 + 480], 1000), 0.3, places=4)
        self.assertEqual(stage.controls[REDUCTION].value, 0.0)

    def test_silence_after_loud_bass_has_no_subnormals_in_large_blocks(self):
        # A fading filter state must not reach the output as subnormal floats, slow on x86.
        stage = self.stage()
        tone = sine(60, 1.0) + [0.0] * (2 * RATE)
        out_l, out_r = stage.process(tone, tone, block=8192)
        self.assertFalse([x for x in out_l + out_r if 0.0 < abs(x) < 1.1754943508222875e-38])  # FLT_MIN

    def test_non_finite_controls_while_running_fall_back_safely(self):
        rng = random.Random(4)
        loud = [rng.uniform(-4, 4) for _ in range(4800)]
        for bad in (math.nan, math.inf, -math.inf):
            stage = self.stage(gain=6.0)
            stage.process(loud, loud)
            for port in (CEILING, RELEASE, LEVEL, GAIN):
                stage.controls[port].value = bad
            out_l, out_r = stage.process(loud, loud[::-1])
            self.assertTrue(all(math.isfinite(x) for x in out_l + out_r), bad)
            self.assertLessEqual(max(map(abs, out_l + out_r)), 10 ** (-1 / 20) + 1e-6, bad)  # The -1 dB default.

    def test_latency_is_reported_at_activation(self):
        # PipeWire reads the latency port right after activate(), before the first run().
        for rate in (44100, 48000, 96000):
            self.assertEqual(int(self.stage(rate=rate).controls[LATENCY].value), latency(rate))

    def test_activate_again_starts_from_a_clean_state(self):
        rng = random.Random(5)
        quiet = [rng.uniform(-0.3, 0.3) for _ in range(4800)]
        used = self.stage()
        crest = sine(1000, 4.0, 0.4)[:14400 + 12]  # Stops at a crest of a loud treble tone: the true-peak history is full.
        used.process(crest, crest)
        used.d.activate(used.handle)
        # A max-abs-difference, not assertEqual: a failing diff of two long lists takes minutes.
        for again, fresh in zip(used.process(quiet, quiet), self.stage().process(quiet, quiet)):
            self.assertEqual(max(abs(a - b) for a, b in zip(again, fresh)), 0.0)

    def test_an_absurd_finite_sample_is_dropped_instead_of_muting_the_limiter(self):
        # Unguarded, one 2e4 sample would pull the gain down by 86 dB and let it recover over about half a second.
        stage = self.stage()
        tone = sine(1000, 0.3, 0.5)
        tone[2400] = 2e4
        stage.process(tone[:9600], tone[:9600])
        out_l, _ = stage.process(tone[9600:10080], tone[9600:10080])  # 0.15 s later the tone is at full level.
        self.assertAlmostEqual(tone_level(out_l, 1000), 0.3, places=3)
        self.assertEqual(stage.controls[REDUCTION].value, 0.0)

    def test_the_limiter_not_the_final_clamp_holds_a_loud_tone(self):
        # Clamping a +12 dBFS tone at the ceiling would square it off (tens of percent THD).
        stage = self.stage()
        out_l, _ = stage.process(sine(1000, 4.0), sine(1000, 4.0))
        steady = out_l[RATE // 2:RATE // 2 + 960]
        self.assertLess(thd(steady, 1000), 0.001)
        self.assertGreater(max(map(abs, steady)), 10 ** (-1.5 / 20))  # Loud, not squashed.


if __name__ == "__main__":
    unittest.main()
