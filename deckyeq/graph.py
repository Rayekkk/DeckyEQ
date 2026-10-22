"""One fixed stereo graph, with runtime controls instead of graph replacement."""
from __future__ import annotations

import functools
import math
from pathlib import Path
from .model import FREQUENCIES, LIMITS, flat_eq, validate_eq

SINK_NAME = "DeckyEQ"  # Steam lists a virtual sink by its node name, as it has no sound card name.
LEGACY_SINK_NAME = "deckyeq.sink"  # DeckyEQ 1.0's virtual output, still recognised for recovery.
PLAYBACK_NAME = "deckyeq.playback"
SHELF_Q = 2 ** -0.5
VARIANTS = (("peaking", ""), ("lowshelf", "_low"), ("highshelf", "_high"))
# With the limiter on, automatic headroom leaves this much boost to it: louder, and still clean.
ALLOWANCE = float(LIMITS["limiter_allowance"])
CHANNELS = ("l", "r")
LEVEL = "stage:level"  # The input level in dB, including headroom; the stage glides it.
MUTED = -96.0  # The stage's lowest level.
# The level, stereo width, volume boost and look-ahead limiter stage, built from native/deckyeq_dsp.c.
STAGE_LIBRARY = Path(__file__).resolve().parent.parent / "bin" / "deckyeq_dsp.so"
RATE = float(LIMITS["sample_rate"])  # The combined response is evaluated at SteamOS's usual rate.
NYQUIST = RATE / 2
# Check points 1/24 octave apart from 10 Hz; 0 Hz, Nyquist and each filter's frequency are added,
# and every local maximum is then refined, so narrow resonances are not missed between points.
GRID = tuple(10 * 2 ** (i / 24) for i in range(int(24 * math.log2(NYQUIST / 10)) + 1))
GOLDEN = (math.sqrt(5) - 1) / 2
REFINE_STEPS = 18


def biquad(kind, freq, q, gain):
    """PipeWire's RBJ-cookbook biquad as (numerator, denominator), or a constant gain in dB."""
    if gain == 0:
        return 0.0
    w = freq / NYQUIST
    if not 0 < w < 1:
        # PipeWire clamps the frequency; at 0 Hz or Nyquist the filter is a constant gain.
        return float(gain) if (kind == "lowshelf" and w >= 1) or (kind == "highshelf" and w <= 0) else 0.0
    a, w0 = 10 ** (gain / 40), math.pi * w
    alpha, k = math.sin(w0) / (2 * q), math.cos(w0)
    s = 2 * math.sqrt(a) * alpha
    if kind == "peaking":
        return (1 + alpha * a, -2 * k, 1 - alpha * a), (1 + alpha / a, -2 * k, 1 - alpha / a)
    if kind == "lowshelf":
        return ((a * ((a + 1) - (a - 1) * k + s), 2 * a * ((a - 1) - (a + 1) * k), a * ((a + 1) - (a - 1) * k - s)),
                ((a + 1) + (a - 1) * k + s, -2 * ((a - 1) + (a + 1) * k), (a + 1) + (a - 1) * k - s))
    return ((a * ((a + 1) + (a - 1) * k + s), -2 * a * ((a - 1) + (a + 1) * k), a * ((a + 1) + (a - 1) * k - s)),
            ((a + 1) - (a - 1) * k + s, 2 * ((a - 1) - (a + 1) * k), (a + 1) - (a - 1) * k - s))


def gain_db(design, hz):
    if isinstance(design, float):
        return design
    omega = 2 * math.pi * hz / RATE
    def power(c):
        re = c[0] + c[1] * math.cos(omega) + c[2] * math.cos(2 * omega)
        im = c[1] * math.sin(omega) + c[2] * math.sin(2 * omega)
        return re * re + im * im
    return 10 * math.log10(power(design[0]) / power(design[1]))


def response_db(kind, freq, q, gain, points):
    """Response in dB of one filter, as configured by controls(), at each point in Hz."""
    design = biquad(kind, freq, q, gain)
    return [gain_db(design, hz) for hz in points]


@functools.lru_cache(maxsize=128)
def _peak(filters):
    designs = [biquad(*f) for f in filters]
    total = lambda hz: sum(gain_db(d, hz) for d in designs)
    points = sorted({0.0, NYQUIST, *GRID, *(f[1] for f in filters if 0 < f[1] < NYQUIST)})
    values = [total(hz) for hz in points]
    best = max(values)
    for i in range(1, len(points) - 1):
        if values[i] >= values[i - 1] and values[i] >= values[i + 1]:
            # Golden-section search for the true maximum between the neighbouring points.
            lo, hi = points[i - 1], points[i + 1]
            c, d = hi - (hi - lo) * GOLDEN, lo + (hi - lo) * GOLDEN
            fc, fd = total(c), total(d)
            for _ in range(REFINE_STEPS):
                if fc < fd:
                    lo, c, fc = c, d, fd
                    d = lo + (hi - lo) * GOLDEN
                    fd = total(d)
                else:
                    hi, d, fd = d, c, fc
                    c = hi - (hi - lo) * GOLDEN
                    fc = total(c)
            best = max(best, fc, fd)
    return best


def peak_gain(eq):
    """Highest gain, in dB, of all filters together (bands and tone shelves; without the preamp)."""
    filters = tuple((b["type"], b["frequency"], b["q"], b["gain"]) for b in eq["bands"] if b["gain"])
    filters += tuple(f for f in (("lowshelf", float(LIMITS["bass_frequency"]), SHELF_Q, eq["bass"]),
                                 ("highshelf", float(LIMITS["treble_frequency"]), SHELF_Q, eq["treble"])) if f[3])
    return _peak(filters) if filters else 0.0


def headroom(eq, boost=0.0):
    # The limiter always guards the output, so headroom only keeps it from squashing large EQ boosts:
    # the level drops by the part of the filters' combined peak above ALLOWANCE. It never takes back
    # the input level, the volume boost or stereo widening, which the user chose to be louder or
    # wider (a lowered input level still counts as room). The preset field auto_headroom is kept for
    # compatibility and no longer read. boost is accepted for symmetry with controls() only.
    del boost
    excess = peak_gain(eq) + min(0.0, eq["preamp"]) - ALLOWANCE
    compensation = -excess if excess > 0 else 0.0
    # The stage cannot go below MUTED: report what is really applied.
    compensation = max(compensation, MUTED - eq["preamp"])
    return {"compensation": round(compensation, 4), "effective_preamp": round(eq["preamp"] + compensation, 4)}


def controls(eq, boost=0.0):
    """Every live control: the filters once per channel (node_l, node_r), then the stereo stage.
    boost is the global volume boost in dB."""
    eq = validate_eq(eq)
    mono = {"bass:Freq": float(LIMITS["bass_frequency"]), "bass:Q": SHELF_Q, "bass:Gain": eq["bass"],
            "treble:Freq": float(LIMITS["treble_frequency"]), "treble:Q": SHELF_Q, "treble:Gain": eq["treble"]}
    for i, band in enumerate(eq["bands"]):
        for kind, suffix in VARIANTS:
            mono[f"band{i}{suffix}:Freq"] = band["frequency"]
            mono[f"band{i}{suffix}:Q"] = band["q"]
            mono[f"band{i}{suffix}:Gain"] = band["gain"] if band["type"] == kind else 0.0
    result = {}
    for key, value in mono.items():
        node, port = key.split(":", 1)
        result.update({f"{node}_{c}:{port}": value for c in CHANNELS})
    result[LEVEL] = headroom(eq, boost)["effective_preamp"]
    result["stage:width"] = eq["width"]
    result["stage:gain"] = float(boost)
    return result


def control_payload(values):
    """SPA_PROP_params is a struct of alternating names and values, not an object."""
    return {"params": [item for pair in values.items() for item in pair]}


def neutral():
    """Controls that leave the sound as it is: the filter starts and ends here, so being linked in
    or out of the playback path is inaudible apart from its 1.6 ms delay."""
    return controls(flat_eq())


def filter_config(eq, target, owner, stage=STAGE_LIBRARY, boost=0.0):
    values = controls(eq, boost)
    # Each slot has three serial native filters; inactive types have 0 dB gain. Changing a type
    # (also by switching presets) is then a live control update, never a rebuild that would let
    # unequalized audio through. It costs about 1% of a core while playing.
    specs = [("bass", "bq_lowshelf")]
    specs += [(f"band{i}{suffix}", "bq_" + kind) for i in range(len(FREQUENCIES)) for kind, suffix in VARIANTS]
    specs += [("treble", "bq_highshelf")]
    control = lambda node: {name.split(":", 1)[1]: value for name, value in values.items() if name.startswith(node + ":")}
    # The filters run once per channel; the stage mixes both channels (width) and limits them together.
    nodes, links = [], []
    for c in CHANNELS:
        nodes += [{"type": "builtin", "name": f"{key}_{c}", "label": label, "control": control(f"{key}_{c}")}
                  for key, label in specs]
        links += [{"output": f"{a[0]}_{c}:Out", "input": f"{b[0]}_{c}:In"} for a, b in zip(specs, specs[1:])]
        links.append({"output": f"treble_{c}:Out", "input": f"stage:in_{c}"})
    nodes.append({"type": "ladspa", "name": "stage", "plugin": str(stage), "label": "deckyeq_stage",
                  "control": {**control("stage"), "ceiling": float(LIMITS["limiter_ceiling"]),
                              "release": float(LIMITS["limiter_release"])}})
    return {"context.properties": {"core.daemon": False, "log.level": 1},
            "context.spa-libs": {"audio.convert.*": "audioconvert/libspa-audioconvert", "support.*": "support/libspa-support"},
            "context.modules": [
                {"name": "libpipewire-module-rt", "flags": ["ifexists", "nofail"]},
                {"name": "libpipewire-module-protocol-native"},
                {"name": "libpipewire-module-client-node"},
                {"name": "libpipewire-module-adapter"},
                {"name": "libpipewire-module-filter-chain", "args": {
                    "node.description": "DeckyEQ", "media.name": "DeckyEQ", "audio.channels": 2,
                    "audio.position": ["FL", "FR"],
                    "filter.graph": {"nodes": nodes, "links": links,
                                     "inputs": [f"bass_{c}:In" for c in CHANNELS],
                                     "outputs": [f"stage:out_{c}" for c in CHANNELS]},
                    # A WirePlumber (0.5+) smart filter: playback aimed at the target output,
                    # directly or as the default, is linked through this sink first. The filter
                    # is never chosen as the default output itself (filter.smart.targetable).
                    "capture.props": {"node.name": SINK_NAME, "media.class": "Audio/Sink",
                                      "node.description": "DeckyEQ", "deckyeq.owner": owner,
                                      "filter.smart": True, "filter.smart.name": "deckyeq",
                                      "filter.smart.target": {"node.name": target},
                                      # No node.always-process: the filter suspends with the output
                                      # (0% CPU instead of ~3% of a core on the Legion Go 2).
                                      "priority.session": 0, "node.virtual": True},
                    "playback.props": {"node.name": PLAYBACK_NAME, "node.description": "DeckyEQ Playback",
                                       "media.name": "DeckyEQ Playback", "deckyeq.owner": owner,
                                       "node.passive": True}
                }}]}


def read_controls(node):
    result = {}
    for props in node.get("info", {}).get("params", {}).get("Props", []):
        params = props.get("params", {})
        # pw-dump versions serialize SPA structs as either a dictionary or a list.
        if isinstance(params, dict):
            items = params.items()
        elif isinstance(params, list) and len(params) % 2 == 0:
            items = zip(params[::2], params[1::2])
        else:
            continue
        for key, value in items:
            if isinstance(key, str) and type(value) in (int, float) and math.isfinite(value):
                result[key] = float(value)
    return result


def same_controls(actual, expected):
    # pw-dump prints SPA floats with six decimal places. Account for half a
    # final decimal unit, especially for small linear preamp multipliers.
    return all(k in actual and math.isclose(actual[k], v, rel_tol=2e-5, abs_tol=5.1e-7) for k, v in expected.items())
