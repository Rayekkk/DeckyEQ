export type FilterType = "peaking" | "lowshelf" | "highshelf";
export interface Band { frequency: number; gain: number; q: number; type: FilterType }
export interface EQ {
  bands: Band[]; bass: number; treble: number; preamp: number; auto_headroom: boolean;
  /** 0 is mono, 1 unchanged, 2 twice the side signal. */
  width: number;
  /** Look-ahead peak limiter after the filters. */
  limiter: boolean;
}
export interface Preset { id: string; name: string; eq: EQ; factory?: boolean; description?: string }
export interface Settings {
  version: number; enabled: boolean; target: string; speakers_only: boolean;
  /** Global volume boost in dB, before the limiter; not part of any preset. */
  boost: number;
  selected: string; working: EQ;
}
export interface Output { name: string; description: string; port: string; speaker: boolean }
export interface Snapshot {
  version: string; revision: number; state: Settings; presets: Preset[]; dirty: boolean;
  audio: {
    supported: boolean; phase: string; message: string; error: string | null;
    outputs: Output[]; default: string; compensation: number; effective_preamp: number;
  };
  warning: string | null; home: string; export_directory: string;
}
export interface Result { ok: boolean; state?: Snapshot; error?: string; message?: string; path?: string }
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
/** Mirrors model.frequency(): unstable float32 biquad ranges snap to the audible band. */
const [stableLow, stableHigh] = limits.stable_frequency;
export const safeFrequency = (hz: number) =>
  hz > 0 && hz < stableLow ? stableLow : hz > stableHigh && hz < limits.passthrough_frequency ? stableHigh : hz;
/** Field-wise comparison: backend dictionaries do not keep one key order. */
export const sameEQ = (a: EQ, b?: EQ) => !!b && a.bass === b.bass && a.treble === b.treble &&
  a.preamp === b.preamp && a.auto_headroom === b.auto_headroom && a.width === b.width && a.limiter === b.limiter &&
  a.bands.length === b.bands.length &&
  a.bands.every((band, i) => band.type === b.bands[i].type && band.frequency === b.bands[i].frequency &&
    band.gain === b.bands[i].gain && band.q === b.bands[i].q);
export const cloneEQ = (eq: EQ): EQ => ({ ...eq, bands: eq.bands.map(band => ({ ...band })) });
export const formatHz = (hz: number) => hz >= 1000 ? `${Number((hz / 1000).toFixed(2))} kHz` : `${Number(hz.toFixed(2))} Hz`;
export const db = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(1)} dB`;
