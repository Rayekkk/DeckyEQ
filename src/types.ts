export type FilterType = "peaking" | "lowshelf" | "highshelf";
export interface Band { frequency: number; gain: number; q: number; type: FilterType }
export interface EQ {
  bands: Band[]; bass: number; treble: number; preamp: number; auto_headroom: boolean;
}
export interface Preset { id: string; name: string; eq: EQ; factory?: boolean; description?: string }
export interface Settings {
  version: number; enabled: boolean; target: string; speakers_only: boolean;
  selected: string; working: EQ; presets: Preset[];
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
export const frequencies = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
export const flatEQ = (): EQ => ({
  bands: frequencies.map(frequency => ({ frequency, gain: 0, q: 1.4, type: "peaking" })),
  bass: 0, treble: 0, preamp: 0, auto_headroom: true,
});
/** Mirrors model.frequency(): unstable float32 biquad ranges snap to the audible band. */
export const safeFrequency = (hz: number) => hz > 0 && hz < 20 ? 20 : hz > 20000 && hz < 24000 ? 20000 : hz;
export const cloneEQ =(eq: EQ): EQ => ({ ...eq, bands: eq.bands.map(band => ({ ...band })) });
export const formatHz = (hz: number) => hz >= 1000 ? `${Number((hz / 1000).toFixed(2))} kHz` : `${Number(hz.toFixed(2))} Hz`;
export const db = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(1)} dB`;
