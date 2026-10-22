"""Versioned, bounded, portable presets. No process names or paths in presets."""
from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path

# One source for limits shared with the frontend (src/ imports the same file).
LIMITS = json.loads(Path(__file__).with_name("limits.json").read_text(encoding="utf-8"))
FREQUENCIES = tuple(LIMITS["frequencies"])
MAX_PRESETS = 64
MAX_IMPORT_BYTES = 256 * 1024
SCHEMA_VERSION = 2
FILTER_TYPES = ("peaking", "lowshelf", "highshelf")
FACTORY_IDS = {"flat", "go-s-original"}


class EQError(ValueError):
    pass


class ToolError(EQError):
    """A system audio tool could not be run or did not answer: the sound itself is unknown, not wrong."""


class NodeGone(EQError):
    """Our filter's node vanished (e.g. PipeWire restarted) while its process lives: start it again."""


def number(value, low, high, label):
    # The range check also rejects NaN/inf and runs before float(), which overflows on huge JSON integers.
    if type(value) not in (int, float) or not low <= value <= high:
        raise EQError(f"{label} must be a number between {low} and {high}.")
    return float(value)


def frequency(value, legacy=False):
    low, high = LIMITS["stable_frequency"]
    hz = number(value, *((low, high) if legacy else LIMITS["frequency"]), "Frequency")
    # PipeWire's float32 biquads are unstable below ~6 Hz and just under Nyquist.
    # 0 Hz and >= 24 kHz become constant gains at 44.1/48 kHz and stay valid for old presets.
    if 0 < hz < low:
        return float(low)
    if high < hz < LIMITS["passthrough_frequency"]:
        return float(high)
    return hz


def name(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= LIMITS["name_length"] or
            any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise EQError(f"Preset names must contain 1–{LIMITS['name_length']} printable characters.")
    return value.strip()


def flat_eq():
    return {"bands": [{"frequency": float(f), "gain": 0.0, "q": float(LIMITS["flat_q"]), "type": "peaking"} for f in FREQUENCIES],
            "bass": 0.0, "treble": 0.0, "preamp": 0.0, "auto_headroom": True, "width": 1.0, "limiter": True}


def go_s_eq():
    """GAZuOOT's original, using values actually parsed by PipeWire 1.6.8.

    62.5.0 parses as 0 Hz; 80000 is clamped by the native control to 48000.
    Keep 40000 as written, not an inferred 4000 Hz correction.
    """
    specs = [("lowshelf", 31.25, -10), ("peaking", 0, -4),
             ("peaking", 125, 5), ("peaking", 250, 5), ("peaking", 500, -5),
             ("highshelf", 1000, -5), ("peaking", 2000, -5), ("peaking", 40000, -5),
             ("highshelf", 48000, 0), ("peaking", 16000, 0)]
    return {"bands": [{"type": kind, "frequency": float(freq), "gain": float(gain), "q": 1.0}
                      for kind, freq, gain in specs],
            "bass": 0.0, "treble": 0.0, "preamp": 8.0, "auto_headroom": False, "width": 1.0, "limiter": True}


EQ_KEYS = {"bands", "bass", "treble", "preamp", "auto_headroom"}
STAGE_KEYS = {"width": 1.0, "limiter": True}  # Added in 1.1; older presets get these defaults.


def validate_eq(value, legacy=False):
    if not isinstance(value, dict) or not EQ_KEYS <= set(value) <= EQ_KEYS | set(STAGE_KEYS):
        raise EQError("Unsupported EQ settings. Use a DeckyEQ preset.")
    value = {**STAGE_KEYS, **value}
    bands = value["bands"]
    if not isinstance(bands, list) or len(bands) != len(FREQUENCIES):
        raise EQError(f"An EQ preset must contain exactly {len(FREQUENCIES)} bands.")
    cleaned = []
    for i, band in enumerate(bands):
        required = {"frequency", "gain", "q"} if legacy else {"frequency", "gain", "q", "type"}
        if not isinstance(band, dict) or set(band) != required:
            raise EQError(f"Band {i + 1} is invalid.")
        kind = band.get("type", "peaking")  # Version 1 bands are all bells.
        if kind not in FILTER_TYPES:
            raise EQError(f"Band {i + 1} has an unsupported filter type.")
        q = number(band["q"], *LIMITS["q"], "Band Q")
        if kind != "peaking":  # A shelf's Q above this resonates: a "cut" boosts near its corner.
            q = min(q, float(LIMITS["shelf_q_max"]))
        cleaned.append({"type": kind, "frequency": frequency(band["frequency"], legacy),
                        "gain": number(band["gain"], *LIMITS["band_gain"], "Band gain"), "q": q})
    if type(value["auto_headroom"]) is not bool:
        raise EQError("Automatic headroom must be on or off.")
    if type(value["limiter"]) is not bool:
        raise EQError("The limiter must be on or off.")
    low, high = LIMITS["preamp"]
    return {"bands": cleaned, "bass": number(value["bass"], *LIMITS["tone_gain"], "Bass"),
            "treble": number(value["treble"], *LIMITS["tone_gain"], "Treble"),
            "preamp": number(value["preamp"], low, 0 if legacy else high, "Preamp"),
            "auto_headroom": value["auto_headroom"], "width": number(value["width"], *LIMITS["width"], "Stereo width"),
            "limiter": value["limiter"]}


def initial_state():
    return {"version": SCHEMA_VERSION, "enabled": False, "target": "", "speakers_only": True,
            "boost": 0.0, "selected": "flat", "working": flat_eq(), "presets": []}


def validate_state(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") not in (1, SCHEMA_VERSION):
        raise EQError("Unsupported settings version.")
    result = initial_state()
    for key in ("enabled", "speakers_only"):
        if type(value.get(key)) is not bool:
            raise EQError(f"Invalid {key} setting.")
        result[key] = value[key]
    target = value.get("target")
    if not isinstance(target, str) or len(target) > 512 or any(ord(c) < 32 for c in target):
        raise EQError("Invalid audio output.")
    result["target"] = target
    # A global volume boost (1.1+), not part of any preset: the device is quiet, not the sound.
    result["boost"] = number(value.get("boost", 0.0), *LIMITS["boost"], "Volume boost")
    result["working"] = validate_eq(value.get("working"), legacy=value["version"] == 1)
    items = value.get("presets")
    if not isinstance(items, list) or len(items) > MAX_PRESETS:
        raise EQError("Invalid preset library.")
    ids = set(FACTORY_IDS)
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", item["id"]):
            raise EQError("Invalid preset identity.")
        if item["id"] in ids:
            raise EQError("Duplicate preset identity.")
        ids.add(item["id"])
        result["presets"].append({"id": item["id"], "name": name(item.get("name")),
                                   "eq": validate_eq(item.get("eq"), legacy=value["version"] == 1)})
    if not isinstance(value.get("selected"), str) or value["selected"] not in ids:
        raise EQError("The selected preset is missing.")
    result["selected"] = value["selected"]
    return result


def presets(state):
    return [{"id": "flat", "name": "Flat", "eq": flat_eq(), "factory": True},
            {"id": "go-s-original", "name": "Legion Go S · Original", "eq": go_s_eq(), "factory": True,
             "description": "Community speaker preset by GAZuOOT. Original +8 dB input boost; automatic headroom is off. Lower playback volume if it distorts."},
            *copy.deepcopy(state["presets"])]


def selected_preset(state, preset_id=None):
    preset_id = state["selected"] if preset_id is None else preset_id
    for preset in presets(state):
        if preset["id"] == preset_id:
            return preset
    raise EQError("Preset no longer exists. Refresh and try again.")


def create_preset(state, title, eq):
    if len(state["presets"]) >= MAX_PRESETS:
        raise EQError(f"The library is full ({MAX_PRESETS} user presets).")
    title = name(title)
    used = {p["name"].casefold() for p in presets(state)}
    candidate, index = title, 2
    while candidate.casefold() in used:
        suffix = f" ({index})"
        candidate = title[:LIMITS["name_length"] - len(suffix)] + suffix
        index += 1
    item = {"id": uuid.uuid4().hex, "name": candidate, "eq": validate_eq(eq)}
    state["presets"].append(item)
    return item


def decode_import(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise EQError(f"Preset files must be no larger than {MAX_IMPORT_BYTES // 1024} KiB.")
    def reject_constant(_):
        raise EQError("Non-finite JSON values are not allowed.")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise EQError("Duplicate JSON keys are not allowed.")
            result[key] = value
        return result
    try:
        value = json.loads(text.lstrip("\ufeff"), parse_constant=reject_constant, object_pairs_hook=unique_keys)
    except (ValueError, RecursionError) as exc:
        raise EQError("Invalid preset JSON: " + str(exc)[:160]) from None
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] not in (1, SCHEMA_VERSION):
        raise EQError("Unsupported preset file version.")
    if value.get("format") == "deckyeq-preset":
        items = [value.get("preset")]
    elif value.get("format") == "deckyeq-bank":
        items = value.get("presets")
    else:
        raise EQError("Choose a DeckyEQ preset or preset bank JSON file.")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_PRESETS:
        raise EQError(f"A preset bank must contain 1–{MAX_PRESETS} presets.")
    output = []
    for item in items:
        if not isinstance(item, dict):
            raise EQError("Invalid preset entry.")
        output.append({"name": name(item.get("name")), "eq": validate_eq(item.get("eq"), legacy=value["version"] == 1)})
    return output


def portable(eq):
    """EQ as written to disk: width and limiter only when changed, so DeckyEQ 1.0 still reads it."""
    return {k: copy.deepcopy(v) for k, v in eq.items() if k not in STAGE_KEYS or v != STAGE_KEYS[k]}


def portable_state(state):
    kept = {k: v for k, v in state.items() if k != "boost" or v}  # 1.0 never sees an unused boost.
    return {**kept, "working": portable(state["working"]),
            "presets": [{**p, "eq": portable(p["eq"])} for p in state["presets"]]}


def export_document(state, all_presets=False):
    if all_presets:
        # A bank contains the saved library; include Flat so an empty library is portable too.
        items = state["presets"] or [selected_preset(initial_state())]
        return {"format": "deckyeq-bank", "version": SCHEMA_VERSION,
                "presets": [{"name": p["name"], "eq": portable(p["eq"])} for p in items]}
    return {"format": "deckyeq-preset", "version": SCHEMA_VERSION,
            "preset": {"name": selected_preset(state)["name"], "eq": portable(state["working"])}}
