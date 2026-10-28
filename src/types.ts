export type FilterType = "peaking" | "lowshelf" | "highshelf";
export interface Band { frequency: number; gain: number; q: number; type: FilterType }
export interface EQ {
  bands: Band[]; bass: number; treble: number; preamp: number;
  /** Kept for preset compatibility; no longer used (headroom is always automatic). */
  auto_headroom: boolean;
  /** 0 is mono, 1 unchanged, 2 twice the side signal. */
  width: number;
  /** Kept for preset compatibility; no longer used (the limiter always runs, boosts stay loud). */
  limiter: boolean;
}
/** `boost`: a built-in tuned with a Volume boost sets it when chosen. */
export interface Preset { id: string; name: string; eq: EQ; factory?: boolean; description?: string; boost?: number }
export interface Settings {
  version: number; enabled: boolean; target: string; speakers_only: boolean;
  /** Global volume boost in dB, before the limiter; not part of any preset. */
  boost: number;
  selected: string; working: EQ;
  /** "" for the everyday sound, or the app id of the running game whose own sound is active. */
  scope: string;
  /** The games with their own sound; the backend keeps their sounds, the panel needs the names. */
  games: Record<string, { name: string }>;
}
export interface Output { name: string; description: string; port: string; speaker: boolean }
export interface Snapshot {
  version: string; revision: number; state: Settings; presets: Preset[]; dirty: boolean;
  /** The running game, as the panel last reported it. */
  game: { appid: string; name: string } | null;
  audio: {
    supported: boolean; phase: string; message: string; error: string | null; game_error: string | null;
    outputs: Output[]; default: string; compensation: number; effective_preamp: number;
  };
  warning: string | null; home: string; export_directory: string;
}
/** Every RPC's answer (DeckyKit D2). `starting`: refused only because the backend is still starting. */
export interface Result {
  ok: boolean; state?: Snapshot; error?: string; message?: string | null; path?: string; starting?: boolean;
}
export interface Transport {
  status(): Promise<Snapshot>;
  perform(action: string, payload: Record<string, unknown>, revision: number): Promise<Result>;
}
export interface View {
  snapshot: Snapshot | null; error: string | null; note: string | null;
  busy: boolean; saving: boolean; connected: boolean;
}
import limits from "../deckyeq/limits.json";

/** Shared with the Python backend, which validates against the same file. */
export { limits };
export const frequencies = limits.frequencies;
export const flatEQ = (): EQ => ({
  bands: frequencies.map(frequency => ({ frequency, gain: 0, q: limits.flat_q, type: "peaking" })),
  bass: 0, treble: 0, preamp: 0, auto_headroom: true, width: 1, limiter: true,
});
/** Field-wise comparison, independent of key order (presets may come from other tools). */
export const sameEQ = (a: EQ, b?: EQ) => !!b && a.bass === b.bass && a.treble === b.treble &&
  a.preamp === b.preamp && a.auto_headroom === b.auto_headroom && a.width === b.width && a.limiter === b.limiter &&
  a.bands.length === b.bands.length &&
  a.bands.every((band, i) => band.type === b.bands[i].type && band.frequency === b.bands[i].frequency &&
    band.gain === b.bands[i].gain && band.q === b.bands[i].q);
export const cloneEQ = (eq: EQ): EQ => ({ ...eq, bands: eq.bands.map(band => ({ ...band })) });
export const formatHz = (hz: number) => hz >= 1000 ? `${Number((hz / 1000).toFixed(2))} kHz` : `${Number(hz.toFixed(2))} Hz`;
export const db = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(1)} dB`;
