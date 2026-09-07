/** Deliberate UI simulation; never imported by src/index.tsx. Does not process audio. */
import { cloneEQ, EQ, flatEQ, Preset, Result, Snapshot, Transport } from "../src/types";
import { headroom } from "../src/headroom";
import goS from "../presets/legion-go-s-original.json";

export function seed(): Snapshot {
  return { version: "1.0.0", revision: 0, state: { version: 2, enabled: false, target: "alsa_output.preview",
    speakers_only: true, selected: "flat", working: flatEQ(), presets: [] },
    presets: [{ id: "flat", name: "Flat", eq: flatEQ(), factory: true },
      { id: "go-s-original", name: goS.preset.name, eq: goS.preset.eq as EQ, factory: true,
        description: "Community speaker preset by GAZuOOT. Original +8 dB input boost; automatic headroom is off. Lower playback volume if it distorts." }], dirty: false,
    audio: { supported: true, phase: "off", message: "EQ is off. Your presets are kept.", error: null,
      outputs: [{ name: "alsa_output.preview", description: "Legion Go 2 speakers", port: "analog-output-speaker", speaker: true }],
      default: "alsa_output.preview", compensation: 0, effective_preamp: 0 },
    warning: null, home: "/home/deck", export_directory: "/home/deck/Documents/DeckyEQ" };
}

export class PreviewEngine implements Transport {
  value = seed();
  async status() { return structuredClone(this.value); }
  async perform(action: string, payload: Record<string, any>, revision: number): Promise<Result> {
    await new Promise(resolve => setTimeout(resolve, 30));
    if (revision !== this.value.revision) return { ok: false, error: "Stale revision", state: await this.status() };
    const value = structuredClone(this.value), state = value.state;
    const selected = () => value.presets.find(p => p.id === state.selected)!;
    if (action === "edit") state.working = cloneEQ(payload.eq);
    if (action === "enable") state.enabled = payload.enabled;
    if (action === "output") { state.target = payload.target; state.speakers_only = payload.speakers_only; }
    if (action === "save_as") {
      const p: Preset = { id: crypto.randomUUID(), name: payload.name, eq: cloneEQ(state.working) };
      value.presets.push(p); state.selected = p.id;
    }
    if (action === "select") { state.selected = payload.id; state.working = cloneEQ(selected().eq); }
    if (action === "save") selected().eq = cloneEQ(state.working);
    if (action === "rename") selected().name = payload.name;
    if (action === "delete") { value.presets = value.presets.filter(p => p.id !== state.selected); state.selected = "flat"; state.working = flatEQ(); }
    if (action === "revert") state.working = cloneEQ(selected().eq);
    if (action === "reset") state.working = flatEQ();
    if (action === "import") value.presets.push({ id: crypto.randomUUID(), name: "Imported preview", eq: flatEQ() });
    state.presets = value.presets.filter(p => !p.factory);
    value.dirty = JSON.stringify(state.working) !== JSON.stringify(selected().eq);
    value.audio.phase = state.enabled ? "active" : "off";
    value.audio.message = state.enabled ? "Live EQ · Legion Go 2 speakers" : "EQ is off. Your presets are kept.";
    const eq = state.working;
    Object.assign(value.audio, headroom(eq));
    value.revision++;
    this.value = value;
    return { ok: true, state: await this.status(), ...(action === "export" ? { path: `${value.export_directory}/preview-preset.json` } : {}) };
  }
}
