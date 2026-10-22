"""Versioned, bounded, portable presets. No process names or paths in presets."""
from __future__ import annotations

import copy
import json
import re
import uuid

FREQUENCIES = (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
MAX_PRESETS = 64
MAX_IMPORT_BYTES = 256 * 1024
SCHEMA_VERSION = 2
FILTER_TYPES = ("peaking", "lowshelf", "highshelf")
FACTORY_IDS = {"flat", "go-s-original"}


class EQError(ValueError):
    pass


def number(value, low, high, label):
    # The range check also rejects NaN/inf and runs before float(), which overflows on huge JSON integers.
    if type(value) not in (int, float) or not low <= value <= high:
        raise EQError(f"{label} must be a number between {low} and {high}.")
    return float(value)


def frequency(value, legacy=False):
    hz = number(value, 20 if legacy else 0, 20000 if legacy else 48000, "Frequency")
    # PipeWire's float32 biquads are unstable below ~6 Hz and just under Nyquist.
    # 0 Hz and >= 24 kHz become constant gains at 44.1/48 kHz and stay valid for old presets.
    if 0 < hz < 20:
        return 20.0
    if 20000 < hz < 24000:
        return 20000.0
    return hz


def name(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 64 or
            any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise EQError("Preset names must contain 1–64 printable characters.")
    return value.strip()


def flat_eq():
    return {"bands": [{"frequency": float(f), "gain": 0.0, "q": 1.4, "type": "peaking"} for f in FREQUENCIES],
            "bass": 0.0, "treble": 0.0, "preamp": 0.0, "auto_headroom": True}


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
            "bass": 0.0, "treble": 0.0, "preamp": 8.0, "auto_headroom": False}


def validate_eq(value, legacy=False):
    if not isinstance(value, dict) or set(value) != {"bands", "bass", "treble", "preamp", "auto_headroom"}:
        raise EQError("Unsupported EQ settings. Use a DeckyEQ preset.")
    bands = value["bands"]
    if not isinstance(bands, list) or len(bands) != 10:
        raise EQError("An EQ preset must contain exactly 10 bands.")
    cleaned = []
    for i, band in enumerate(bands):
        required = {"frequency", "gain", "q"} if legacy else {"frequency", "gain", "q", "type"}
        if not isinstance(band, dict) or set(band) != required:
            raise EQError(f"Band {i + 1} is invalid.")
        kind = band.get("type", "peaking")  # Version 1 bands are all bells.
        if kind not in FILTER_TYPES:
            raise EQError(f"Band {i + 1} has an unsupported filter type.")
        cleaned.append({"type": kind, "frequency": frequency(band["frequency"], legacy),
                        "gain": number(band["gain"], -12, 12, "Band gain"),
                        "q": number(band["q"], 0.2, 5, "Band Q")})
    if type(value["auto_headroom"]) is not bool:
        raise EQError("Automatic headroom must be on or off.")
    return {"bands": cleaned, "bass": number(value["bass"], -9, 9, "Bass"),
            "treble": number(value["treble"], -9, 9, "Treble"),
            "preamp": number(value["preamp"], -36, 0 if legacy else 12, "Preamp"),
            "auto_headroom": value["auto_headroom"]}


def initial_state():
    return {"version": SCHEMA_VERSION, "enabled": False, "target": "", "speakers_only": True,
            "selected": "flat", "working": flat_eq(), "presets": []}


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
    result["working"] = validate_eq(value.get("working"), legacy=value["version"] == 1)
    items = value.get("presets")
    if not isinstance(items, list) or len(items) > MAX_PRESETS:
        raise EQError("Invalid preset library.")
    ids = set(FACTORY_IDS)
    for item in items:
        if not isinstance(item, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(item.get("id", ""))):
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
        candidate = title[:64 - len(suffix)] + suffix
        index += 1
    item = {"id": uuid.uuid4().hex, "name": candidate, "eq": validate_eq(eq)}
    state["presets"].append(item)
    return item


def decode_import(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise EQError("Preset files must be no larger than 256 KiB.")
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
        raise EQError("A preset bank must contain 1–64 presets.")
    output = []
    for item in items:
        if not isinstance(item, dict):
            raise EQError("Invalid preset entry.")
        output.append({"name": name(item.get("name")), "eq": validate_eq(item.get("eq"), legacy=value["version"] == 1)})
    return output


def export_document(state, all_presets=False):
    if all_presets:
        # A bank contains the saved library; include Flat so an empty library is portable too.
        items = state["presets"] or [selected_preset(initial_state())]
        return {"format": "deckyeq-bank", "version": SCHEMA_VERSION,
                "presets": [{"name": p["name"], "eq": copy.deepcopy(p["eq"])} for p in items]}
    return {"format": "deckyeq-preset", "version": SCHEMA_VERSION,
            "preset": {"name": selected_preset(state)["name"], "eq": copy.deepcopy(state["working"])}}
