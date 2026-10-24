/** Deliberate UI simulation; never imported by src/index.tsx. Does not process audio. */
import { cloneEQ, EQ, flatEQ, Preset, Result, sameEQ, Snapshot, Transport } from "../src/types";
import { headroom } from "../src/headroom";
import goS from "../presets/legion-go-s-original.json";
import { version } from "../package.json";

/** Flat with these band gains, like the backend's built-in intent presets. */
const shaped = (gains: number[]): EQ => ({ ...flatEQ(), bands: flatEQ().bands.map((band, i) => ({ ...band, gain: gains[i] })) });

export function seed(): Snapshot {
  return { version, revision: 0, state: { version: 2, enabled: false, target: "alsa_output.preview",
    speakers_only: true, boost: 0, selected: "flat", working: flatEQ(), scope: "", games: {} }, game: null,
    presets: [{ id: "flat", name: "Flat", eq: flatEQ(), factory: true },
      { id: "clear-voices", name: "Clear voices", eq: shaped([0, 0, -2, -2, 0, 1, 3, 3, 1, 0]), factory: true,
        description: "Brings dialogue forward: a little less boom, more presence around 2–4 kHz." },
      { id: "more-bass", name: "More bass", eq: shaped([0, 2, 5, 3, 0, 0, 0, 0, 0, 0]), factory: true,
        description: "Fuller low end where small speakers can still play it, around 100–250 Hz." },
      { id: "go-s-original", name: goS.preset.name, eq: { width: 1, limiter: true, ...goS.preset.eq } as EQ, factory: true,
        description: "Community speaker preset by GAZuOOT for the Legion Go S. If it sounds squashed, lower Input level under Equalizer." },
      { id: "legion-go-2", name: "Legion Go 2", factory: true, boost: 9,
        eq: { ...flatEQ(), bass: 2.5, treble: 1.5, preamp: 8, width: 1.6, bands: ([
          ["lowshelf", 31.5, -3.5, 1], ["peaking", 63, 1.5, 1.4], ["peaking", 125, 4.5, 1], ["peaking", 250, 4.5, 1],
          ["peaking", 500, -4, 1], ["highshelf", 1000, -4.5, 1], ["peaking", 2000, -4, 1], ["peaking", 4000, 1.5, 1.4],
          ["peaking", 8000, 1, 1.4], ["peaking", 16000, 1, 1]] as const).map(([type, frequency, gain, q]) => ({ type, frequency, gain, q })) },
        description: "Speaker tuning for the Legion Go 2. Choosing it also sets Volume boost to 9 dB." }], dirty: false,
    audio: { supported: true, phase: "off", message: "EQ is off, so changes are not heard.", error: null, game_error: null,
      outputs: [{ name: "alsa_output.preview", description: "Legion Go 2 speakers", port: "analog-output-speaker", speaker: true }],
      default: "alsa_output.preview", compensation: 0, effective_preamp: 0 },
    warning: null, home: "/home/deck", export_directory: "/home/deck/Documents/DeckyEQ" };
}

export class PreviewEngine implements Transport {
  value = seed();
  async status() { return structuredClone(this.value); }
  /** Only tracks the game and its toggle; the sounds themselves are not swapped. */
  async setGame(appid: string, name: string): Promise<Result> {
    this.value = { ...structuredClone(this.value), game: appid ? { appid, name } : null };
    return { ok: true, state: await this.status() };
  }
  async perform(action: string, payload: Record<string, any>, revision: number): Promise<Result> {
    await new Promise(resolve => setTimeout(resolve, 30));
    if (revision !== this.value.revision) return { ok: false, error: "Stale revision", state: await this.status() };
    if ("scope" in payload && payload.scope !== this.value.state.scope)  // As the backend: the game switched the sound.
      return { ok: false, error: "The running game changed. Check the sound and try again.", state: await this.status() };
    const value = structuredClone(this.value), state = value.state;
    const selected = () => value.presets.find(p => p.id === state.selected)!;
    // As the backend: built-in presets stay as they are, and a dialog's preset must still be the selected one.
    const refuse = (error: string) => ({ ok: false, error, state: structuredClone(this.value) });
    if (["delete", "revert", "rename"].includes(action) && (payload.id ?? state.selected) !== state.selected)
      return refuse("The selected preset changed. Check it and try again.");
    const builtIn = { save: "Built-in presets are read-only. Use Save as new… to keep your changes.",
      rename: "Built-in presets cannot be renamed.", delete: "Built-in presets cannot be deleted." }[action];
    if (builtIn && selected().factory) return refuse(builtIn);
    if (action === "edit") { state.working = cloneEQ(payload.eq); if ("boost" in payload) state.boost = payload.boost; }
    if (action === "enable") state.enabled = payload.enabled;
    if (action === "output") { state.target = payload.target; state.speakers_only = payload.speakers_only; }
    if (action === "save_as") {
      const p: Preset = { id: crypto.randomUUID(), name: payload.name, eq: cloneEQ(state.working) };
      value.presets.push(p); state.selected = p.id;
    }
    if (action === "select") {
      state.selected = payload.id; state.working = cloneEQ(selected().eq);
      if (selected().boost !== undefined) state.boost = selected().boost!;
    }
    if (action === "save") selected().eq = cloneEQ(state.working);
    if (action === "rename") selected().name = payload.name;
    if (action === "delete") { value.presets = value.presets.filter(p => p.id !== state.selected); state.selected = "flat"; state.working = flatEQ(); }
    if (action === "revert") state.working = cloneEQ(selected().eq);
    if (action === "reset") state.working = flatEQ();
    const game = value.game;
    if (action === "own_sound" && game && payload.enabled) {
      state.games[game.appid] = { name: game.name };
      state.scope = game.appid;
    }
    if (action === "own_sound" && game && !payload.enabled) { delete state.games[game.appid]; state.scope = ""; }
    if (action === "forget_game") { delete state.games[payload.appid]; if (state.scope === payload.appid) state.scope = ""; }
    if (action === "import") value.presets.push({ id: crypto.randomUUID(), name: "Imported preview", eq: flatEQ() });
    value.dirty = !sameEQ(state.working, selected().eq);
    value.audio.phase = state.enabled ? "active" : "off";
    value.audio.message = state.enabled ? "EQ on Legion Go 2 speakers" : "EQ is off, so changes are not heard.";
    const eq = state.working;
    Object.assign(value.audio, headroom(eq));
    value.revision++;
    this.value = value;
    return { ok: true, state: await this.status(), ...(action === "export" ? { path: `${value.export_directory}/preview-preset.json` } : {}) };
  }
}
