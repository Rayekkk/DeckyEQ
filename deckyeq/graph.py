"""One fixed stereo graph, with runtime controls instead of graph replacement."""
from __future__ import annotations

import math
from .model import validate_eq

SINK_NAME = "deckyeq.sink"
PLAYBACK_NAME = "deckyeq.playback"
SHELF_Q = 2 ** -0.5
VARIANTS = (("peaking", ""), ("lowshelf", "_low"), ("highshelf", "_high"))


def shelf_peak(gain, q):
    """Maximum shelf gain over frequency, including Q-dependent overshoot.

    The bilinear transform preserves this maximum. The squared response is a
    ratio of quadratics in x = (tan(w/2)/tan(w0/2))**2. Test its stationary
    points and endpoints; low/high shelves have the same maximum.
    """
    peak = max(0.0, gain)
    if gain == 0 or q <= SHELF_Q:
        return peak
    a = 10 ** (gain / 40)
    cross = a * (1 / q ** 2 - 2)
    b = 1 + a * a
    x = -cross / (b + math.sqrt(max(0.0, b * b - cross * cross)))
    for point in (x, 1 / x):
        squared = a * a * ((point - a) ** 2 + a * point / q ** 2) / ((1 - a * point) ** 2 + a * point / q ** 2)
        peak = max(peak, 10 * math.log10(squared))
    return peak


def headroom(eq):
    # Sum individual maxima, including resonant shelves and a positive preamp.
    # Independent of sample rate; keeps version 1 compensation unchanged.
    boost = sum(max(0, b["gain"]) if b.get("type", "peaking") == "peaking" else shelf_peak(b["gain"], b["q"])
                for b in eq["bands"]) + max(0, eq["bass"]) + max(0, eq["treble"]) + max(0, eq["preamp"])
    compensation = -(boost + 0.5) if eq["auto_headroom"] and boost > 0 else 0.0
    return {"compensation": round(compensation, 4), "effective_preamp": round(eq["preamp"] + compensation, 4)}


def controls(eq):
    eq = validate_eq(eq)
    result = {"level:Mult": 10 ** (headroom(eq)["effective_preamp"] / 20), "level:Add": 0.0,
              "bass:Freq": 120.0, "bass:Q": SHELF_Q, "bass:Gain": eq["bass"],
              "treble:Freq": 6000.0, "treble:Q": SHELF_Q, "treble:Gain": eq["treble"]}
    for i, band in enumerate(eq["bands"]):
        for kind, suffix in VARIANTS:
            result[f"band{i}{suffix}:Freq"] = band["frequency"]
            result[f"band{i}{suffix}:Q"] = band["q"]
            result[f"band{i}{suffix}:Gain"] = band["gain"] if band["type"] == kind else 0.0
    return result


def control_payload(values):
    """SPA_PROP_params is a struct of alternating names and values, not an object."""
    return {"params": [item for pair in values.items() for item in pair]}


def filter_config(eq, target, owner):
    values = controls(eq)
    specs = [("level", "linear"), ("bass", "bq_lowshelf")]
    # Each slot has three serial native filters. Inactive types have 0 dB gain,
    # so changing type is a live control update, without rebuilding the graph.
    specs += [(f"band{i}{suffix}", "bq_" + kind) for i in range(10) for kind, suffix in VARIANTS]
    specs += [("treble", "bq_highshelf")]
    nodes = [{"type": "builtin", "name": key, "label": label,
              "control": {name.split(":", 1)[1]: value for name, value in values.items()
                          if name.startswith(key + ":")}} for key, label in specs]
    links = [{"output": a[0] + ":Out", "input": b[0] + ":In"} for a, b in zip(specs, specs[1:])]
    # A mono graph is duplicated by filter-chain for FL and FR, sharing controls.
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
                                     "inputs": ["level:In"], "outputs": ["treble:Out"]},
                    "capture.props": {"node.name": SINK_NAME, "media.class": "Audio/Sink",
                                      "node.description": "DeckyEQ", "deckyeq.owner": owner,
                                      # Keep filter instances active while enabled. PipeWire
                                      # 1.6.8 otherwise leaves idle control readback stale.
                                      "node.always-process": True,
                                      "priority.session": 0, "node.virtual": True},
                    "playback.props": {"node.name": PLAYBACK_NAME, "node.description": "DeckyEQ Playback",
                                       "media.name": "DeckyEQ Playback", "deckyeq.owner": owner,
                                       "target.object": target, "node.passive": True,
                                       "node.dont-fallback": True, "node.dont-reconnect": True}
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
