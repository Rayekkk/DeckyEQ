import { useSyncExternalStore } from "react";
import { Band, cloneEQ, EQ, formatHz, limits } from "./types";

const [low, high] = limits.stable_frequency, [gMin, gMax] = limits.band_gain;
export const audible = (band: Band) => band.frequency >= low && band.frequency <= high;
/** A shelf at or beyond 20 Hz–20 kHz that covers every frequency there (a low shelf from 20 kHz up, a high shelf up to
 *  20 Hz), so it acts as a level: only its gain matters, and any other type there would silence it. It stays a level
 *  through 0 dB, so dragging it across 0 never turns it into a bell. */
export const level = (b: Band) => b.type === "lowshelf" ? b.frequency >= high : b.type === "highshelf" && b.frequency <= low;
/** A band that changes nothing: outside 20 Hz–20 kHz (the Go S file's 0 Hz and 24 kHz+ placeholders), unless it is a level. */
export const dead = (b: Band) => !audible(b) && !level(b);
/** Band `i` keeps its own frequency: a level, or a band nearer another band's usual frequency than its own on a log scale
 *  (moved in DeckyEQ 1.0, or imported). The Go S file's 31.25 Hz is nearest its own 31.5 Hz, so it is not kept. */
const kept = (b: Band, i: number) => level(b) || audible(b) &&
  limits.frequencies.some(f => Math.abs(Math.log(b.frequency / f)) < Math.abs(Math.log(b.frequency / limits.frequencies[i])));

/** Band `i` as the panel shows and edits it. A dead band appears as a flat bell at its usual frequency, so every preset
 *  has the same ten bands; it becomes that bell only once it gets a gain, which is when the sound changes. */
export const shown = (eq: EQ, i: number): Band => dead(eq.bands[i])
  ? { type: "peaking", frequency: limits.frequencies[i], q: limits.flat_q, gain: 0 } : eq.bands[i];

/** The name every preset uses for band `i` (31.5 Hz … 16 kHz), so all presets show the same bands, also one that sits
 *  near it (the Go S file's 31.25 Hz); a kept band (see `kept`) shows its own, the frequency it plays at. */
export const bandHz = (eq: EQ, i: number) => kept(eq.bands[i], i) ? eq.bands[i].frequency : limits.frequencies[i];

/** The EQ with band `i` (as shown) changed by `patch`; an edited band moves to its usual frequency, a kept one stays.
 *  A band that does nothing and stays flat is left as it is, so the preset is not marked edited. */
export function withBand(eq: EQ, i: number, patch: Partial<Band>): EQ {
  const b = eq.bands[i];
  const next = cloneEQ(eq), band = { ...shown(eq, i), ...(audible(b) && !kept(b, i) ? { frequency: limits.frequencies[i] } : {}), ...patch };
  if (!dead(b) || band.gain !== 0) next.bands[i] = band;
  return next;
}

/** To 0.5 dB, halves away from 0, so a cut rounds like a boost of the same size. */
const half = (gain: number) => Math.sign(gain) * Math.round(Math.abs(gain) * 2) / 2 || 0;

/** One of the five sliders: two neighbouring bands, as shown. */
export interface Pair { bands: number[]; frequency: number; gain: number; label: string }

export function pairs(eq: EQ): Pair[] {
  return Array.from({ length: eq.bands.length / 2 }, (_, p) => {
    const bands = [2 * p, 2 * p + 1], both = bands.map(i => shown(eq, i));
    const hz = bands.map(i => bandHz(eq, i)).sort((a, b) => a - b);
    const [lo, hi] = [hz[0], hz[hz.length - 1]];
    return { bands, frequency: Math.sqrt(lo * hi), label: lo === hi ? formatHz(lo) : `${formatHz(lo)} – ${formatHz(hi)}`,
      gain: half((both[0].gain + both[1].gain) / 2) };
  });
}

/** The EQ with pair `p` moved to `gain`: both bands move by the same step from where they are, within the band limits;
 *  when one stops at a limit, the other makes up the rest, so the pair shows the gain asked for (within the limits). */
export function withPairGain(eq: EQ, p: number, gain: number): EQ {
  const clamp = (g: number) => Math.min(gMax, Math.max(gMin, g));
  const step = gain - pairs(eq)[p].gain, [a, b] = [2 * p, 2 * p + 1].map(i => shown(eq, i).gain);
  const sum = a + b + 2 * step;  // Both bands together, for the average to move by the step.
  const second = clamp(sum - clamp(a + step)), first = clamp(sum - second);
  return withBand(withBand(eq, 2 * p, { gain: first }), 2 * p + 1, { gain: second });
}

/** The EQ with both bands of pair `p` flat. */
export const flatPair = (eq: EQ, p: number) => withBand(withBand(eq, 2 * p, { gain: 0 }), 2 * p + 1, { gain: 0 });

// Five sliders or ten: this device's panel preference, never part of a preset or the sound.
const KEY = "deckyeq.fiveBands";
const listeners = new Set<() => void>();
let five: boolean | undefined;  // Kept for the session when storage is unavailable.
const read = () => {
  if (five === undefined) {
    try { five = localStorage.getItem(KEY) === "1"; } catch { five = false; }
  }
  return five;
};
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export function setFiveBands(on: boolean) {
  five = on;
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* Blocked storage: this session only. */ }
  listeners.forEach(listener => listener());  // The QAM and the editor follow each other.
}
export const useFiveBands = () => useSyncExternalStore(subscribe, read);
