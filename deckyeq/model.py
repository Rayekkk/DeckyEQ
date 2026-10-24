"""Versioned, bounded, portable presets. No process names or paths in presets."""
from __future__ import annotations

import copy
import json
import re
import unicodedata
import uuid
from pathlib import Path

# One source for limits shared with the frontend (src/ imports the same file).
LIMITS = json.loads(Path(__file__).with_name("limits.json").read_text(encoding="utf-8"))
FREQUENCIES = tuple(LIMITS["frequencies"])
MAX_PRESETS = 64
MAX_IMPORT_BYTES = 256 * 1024
SCHEMA_VERSION = 2
FILTER_TYPES = ("peaking", "lowshelf", "highshelf")
FACTORY_IDS = {"flat", "clear-voices", "more-bass", "go-s-original", "legion-go-2"}


class EQError(ValueError):
    pass


class ToolError(EQError):
    """A system audio tool could not be run or did not answer: the sound itself is unknown, not wrong."""


class NodeGone(EQError):
    """Our filter's node vanished (e.g. PipeWire restarted) while its process lives: start it again."""


class OutputGone(EQError):
    """The output left (unplugged, or off its speaker port) while the filter started: wait for it, no error."""


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


BLANKS = {"\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"}  # Hangul fillers and the Braille blank: drawn as nothing.


ZWJ = "‍"  # Joins emoji such as a rainbow flag: allowed between two drawn characters.


def disguising(value, i):
    """True for a control, invisible/bidi formatting, private-use, unassigned or line-break character at
    value[i], which can disguise a name as another or break the layout."""
    drawn = lambda j: 0 <= j < len(value) and not value[j].isspace() and unicodedata.category(value[j]) not in ("Cc", "Cf")
    return (unicodedata.category(value[i]) in ("Cc", "Cf", "Co", "Cn", "Zl", "Zp")
            and not (value[i] == ZWJ and drawn(i - 1) and drawn(i + 1)))


def name(value, stored=False):
    # New names also refuse disguising characters, and names that draw as nothing (only blanks or
    # combining marks); names already saved keep loading.
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= LIMITS["name_length"] or
            any(ord(c) < 32 or ord(c) == 127 and not stored or 0xD800 <= ord(c) <= 0xDFFF for c in value) or  # 1.0 saved DEL
            not stored and (any(disguising(value, i) for i in range(len(value))) or
                            all(c.isspace() or c in BLANKS or unicodedata.category(c) in ("Zs", "Mn", "Me")
                                for c in value))):
        raise EQError(f"Preset names must contain 1–{LIMITS['name_length']} printable characters.")
    return value.strip()


def flat_eq():
    return {"bands": [{"frequency": float(f), "gain": 0.0, "q": float(LIMITS["flat_q"]), "type": "peaking"} for f in FREQUENCIES],
            "bass": 0.0, "treble": 0.0, "preamp": 0.0, "auto_headroom": True, "width": 1.0, "limiter": True}


def shaped_eq(gains):
    """Flat, with these gains on the ten default frequencies."""
    eq = flat_eq()
    for band, gain in zip(eq["bands"], gains):
        band["gain"] = float(gain)
    return eq


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


def legion_go_2_eq():
    """The owner's speaker tuning for the Legion Go 2, made from the Go S preset on the device."""
    specs = [("lowshelf", 31.5, -3.5, 1.0), ("peaking", 63, 1.5, 1.4), ("peaking", 125, 4.5, 1.0),
             ("peaking", 250, 4.5, 1.0), ("peaking", 500, -4, 1.0), ("highshelf", 1000, -4.5, 1.0),
             ("peaking", 2000, -4, 1.0), ("peaking", 4000, 1.5, 1.4), ("peaking", 8000, 1.0, 1.4),
             ("peaking", 16000, 1.0, 1.0)]
    return {"bands": [{"type": kind, "frequency": float(freq), "gain": float(gain), "q": q}
                      for kind, freq, gain, q in specs],
            "bass": 2.5, "treble": 1.5, "preamp": 8.0, "auto_headroom": False, "width": 1.6, "limiter": True}


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


MAX_GAMES = 200
APPID = re.compile(r"[0-9]{1,20}")  # Steam app ids, including the large ones of non-Steam shortcuts.


def initial_state():
    # scope: "" for the everyday sound, or the app id of the running game that has its own sound.
    # The active sound always lives in selected/working; while a game's is active, the everyday
    # sound waits in "outside", and every game's own sound in "games".
    return {"version": SCHEMA_VERSION, "enabled": False, "target": "", "speakers_only": True,
            "boost": 0.0, "selected": "flat", "working": flat_eq(), "presets": [],
            "scope": "", "games": {}, "outside": None}


def game_name(value, stored=False):
    if (not isinstance(value, str) or len(value) > 128 or
            any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise EQError("Invalid game name.")
    if not stored:  # Steam's title: cleaned like a preset name rather than refused; saved names keep loading.
        value = "".join(c for i, c in enumerate(value) if not disguising(value, i))
    if not value.strip():
        raise EQError("Invalid game name.")
    return value.strip()


def validate_state(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") not in (1, SCHEMA_VERSION):
        raise EQError("Unsupported settings version.")
    result = initial_state()
    for key in ("enabled", "speakers_only"):
        if type(value.get(key)) is not bool:
            raise EQError(f"Invalid {key} setting.")
        result[key] = value[key]
    target = value.get("target")
    if not isinstance(target, str) or len(target) > 512 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in target):
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
        result["presets"].append({"id": item["id"], "name": name(item.get("name"), stored=True),
                                   "eq": validate_eq(item.get("eq"), legacy=value["version"] == 1)})
    if not isinstance(value.get("selected"), str) or value["selected"] not in ids:
        raise EQError("The selected preset is missing.")
    result["selected"] = value["selected"]
    legacy = value["version"] == 1
    sound = lambda item: {"selected": item["selected"] if isinstance(item, dict) and isinstance(item.get("selected"), str)
                          and item["selected"] in ids else "flat",
                          "working": validate_eq(item.get("working") if isinstance(item, dict) else None, legacy=legacy)}
    games = value.get("games", {})
    if not isinstance(games, dict) or len(games) > MAX_GAMES:
        raise EQError("Invalid game sounds.")
    for appid, item in games.items():
        if not isinstance(appid, str) or not APPID.fullmatch(appid) or not isinstance(item, dict):
            raise EQError("Invalid game sound.")
        result["games"][appid] = {"name": game_name(item.get("name"), stored=True), **sound(item)}
    scope = value.get("scope", "")
    if not isinstance(scope, str) or (scope != "" and scope not in result["games"]):
        raise EQError("The active game sound is missing.")
    result["scope"] = scope
    outside = value.get("outside")
    if (outside is None) != (scope == ""):
        raise EQError("The everyday sound is missing.")
    result["outside"] = None if outside is None else sound(outside)
    return result


def switch_scope(state, scope):
    """Make scope's sound the active one, putting the current one back where it belongs."""
    if state["scope"] == scope:
        return
    current = {"selected": state["selected"], "working": copy.deepcopy(state["working"])}
    if state["scope"] == "":
        state["outside"] = current
    elif state["scope"] in state["games"]:
        state["games"][state["scope"]].update(current)
    sound = state["outside"] if scope == "" else state["games"][scope]
    state["selected"], state["working"] = sound["selected"], copy.deepcopy(sound["working"])
    state["scope"] = scope
    if scope == "":
        state["outside"] = None


def presets(state):
    return [{"id": "flat", "name": "Flat", "eq": flat_eq(), "factory": True},
            {"id": "clear-voices", "name": "Clear voices", "eq": shaped_eq([0, 0, -2, -2, 0, 1, 3, 3, 1, 0]), "factory": True,
             "description": "Brings dialogue forward: a little less boom, more presence around 2–4 kHz."},
            {"id": "more-bass", "name": "More bass", "eq": shaped_eq([0, 2, 5, 3, 0, 0, 0, 0, 0, 0]), "factory": True,
             "description": "Fuller low end where small speakers can still play it, around 100–250 Hz."},
            {"id": "go-s-original", "name": "Legion Go S", "eq": go_s_eq(), "factory": True,
             "description": "Community speaker preset by GAZuOOT for the Legion Go S. If it sounds squashed, lower Input level under Equalizer."},
            # Also sets the (global) Volume boost it was tuned with, when chosen.
            {"id": "legion-go-2", "name": "Legion Go 2", "eq": legion_go_2_eq(), "factory": True, "boost": 9.0,
             "description": "Speaker tuning for the Legion Go 2. Choosing it also sets Volume boost to 9 dB."},
            *copy.deepcopy(state["presets"])]


def selected_preset(state, preset_id=None):
    preset_id = state["selected"] if preset_id is None else preset_id
    for preset in presets(state):
        if preset["id"] == preset_id:
            return preset
    raise EQError("Preset no longer exists. Try again.")


def create_preset(state, title, eq):
    if len(state["presets"]) >= MAX_PRESETS:
        raise EQError("The preset library is full. Delete some presets first.")
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
        raise EQError("This is not a DeckyEQ preset file.")
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
    # 1.0 ignores unknown keys; unused ones are left out anyway.
    kept = {k: v for k, v in state.items() if (k != "boost" or v) and (k != "scope" or v)
            and (k != "games" or v) and (k != "outside" or v is not None)}
    result = {**kept, "working": portable(state["working"]),
              "presets": [{**p, "eq": portable(p["eq"])} for p in state["presets"]]}
    if state.get("games"):
        result["games"] = {k: {**g, "working": portable(g["working"])} for k, g in state["games"].items()}
    if state.get("outside"):
        result["outside"] = {**state["outside"], "working": portable(state["outside"]["working"])}
    return result


def export_document(state, all_presets=False):
    if all_presets:
        # A bank contains the saved library; include Flat so an empty library is portable too.
        items = state["presets"] or [selected_preset(initial_state())]
        return {"format": "deckyeq-bank", "version": SCHEMA_VERSION,
                "presets": [{"name": p["name"], "eq": portable(p["eq"])} for p in items]}
    return {"format": "deckyeq-preset", "version": SCHEMA_VERSION,
            "preset": {"name": selected_preset(state)["name"], "eq": portable(state["working"])}}
