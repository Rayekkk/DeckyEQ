import { Band, EQ, limits } from "./types";

// Mirrors deckyeq/graph.py (checked by tests/parity.test.ts against backend-generated vectors).
/** Headroom leaves this much EQ boost to the limiter, which always runs. */
export const ALLOWANCE = limits.limiter_allowance;
const MUTED = -96;  // The native stage's lowest level (deckyeq/graph.py).
const RATE = limits.sample_rate;
const NYQUIST = RATE / 2;
const SHELF_Q = Math.SQRT1_2;
const GRID = Array.from({ length: Math.floor(24 * Math.log2(NYQUIST / 10)) + 1 }, (_, i) => 10 * 2 ** (i / 24));
const GOLDEN = (Math.sqrt(5) - 1) / 2;
const REFINE_STEPS = 18;
type Filter = [Band["type"], number, number, number];
type Design = number | [number[], number[]];

/** PipeWire's RBJ-cookbook biquad as [numerator, denominator], or a constant gain in dB. */
function biquad([kind, freq, q, gain]: Filter): Design {
  if (gain === 0) return 0;
  const w = freq / NYQUIST;
  if (!(w > 0 && w < 1)) return (kind === "lowshelf" && w >= 1) || (kind === "highshelf" && w <= 0) ? gain : 0;
  const a = 10 ** (gain / 40), w0 = Math.PI * w, alpha = Math.sin(w0) / (2 * q), k = Math.cos(w0), s = 2 * Math.sqrt(a) * alpha;
  if (kind === "peaking") return [[1 + alpha * a, -2 * k, 1 - alpha * a], [1 + alpha / a, -2 * k, 1 - alpha / a]];
  if (kind === "lowshelf") return [
    [a * ((a + 1) - (a - 1) * k + s), 2 * a * ((a - 1) - (a + 1) * k), a * ((a + 1) - (a - 1) * k - s)],
    [(a + 1) + (a - 1) * k + s, -2 * ((a - 1) + (a + 1) * k), (a + 1) + (a - 1) * k - s]];
  return [
    [a * ((a + 1) + (a - 1) * k + s), -2 * a * ((a - 1) + (a + 1) * k), a * ((a + 1) + (a - 1) * k - s)],
    [(a + 1) - (a - 1) * k + s, 2 * ((a - 1) - (a + 1) * k), (a + 1) - (a - 1) * k - s]];
}

function gainDb(design: Design, hz: number): number {
  if (typeof design === "number") return design;
  const omega = 2 * Math.PI * hz / RATE;
  const power = (c: number[]) => {
    const re = c[0] + c[1] * Math.cos(omega) + c[2] * Math.cos(2 * omega);
    const im = c[1] * Math.sin(omega) + c[2] * Math.sin(2 * omega);
    return re * re + im * im;
  };
  return 10 * Math.log10(power(design[0]) / power(design[1]));
}

/** The bands and the tone shelves that change anything. */
const filtersOf = (eq: EQ) => [
  ...eq.bands.map((b): Filter => [b.type, b.frequency, b.q, b.gain]),
  ["lowshelf", limits.bass_frequency, SHELF_Q, eq.bass], ["highshelf", limits.treble_frequency, SHELF_Q, eq.treble],
].filter(f => f[3] !== 0) as Filter[];

/** Gain, in dB, of all filters together at each frequency (the curve the panel draws; without the preamp). */
export function response(eq: EQ, hz: number[]): number[] {
  const designs = filtersOf(eq).map(biquad);
  return hz.map(f => designs.reduce<number>((sum, d) => sum + gainDb(d, f), 0));
}

/** Highest gain, in dB, of all filters together (bands and tone shelves; without the preamp). */
export function peakGain(eq: EQ): number {
  const filters = filtersOf(eq);
  if (!filters.length) return 0;
  const designs = filters.map(biquad);
  const total = (hz: number) => designs.reduce<number>((sum, d) => sum + gainDb(d, hz), 0);
  const points = [...new Set([0, NYQUIST, ...GRID, ...filters.map(f => f[1]).filter(f => f > 0 && f < NYQUIST)])]
    .sort((x, y) => x - y);
  const values = points.map(total);
  let best = Math.max(...values);
  for (let i = 1; i < points.length - 1; i++) {
    if (values[i] < values[i - 1] || values[i] < values[i + 1]) continue;
    let lo = points[i - 1], hi = points[i + 1];
    let c = hi - (hi - lo) * GOLDEN, d = lo + (hi - lo) * GOLDEN, fc = total(c), fd = total(d);
    for (let step = 0; step < REFINE_STEPS; step++) {
      if (fc < fd) { lo = c; c = d; fc = fd; d = lo + (hi - lo) * GOLDEN; fd = total(d); }
      else { hi = d; d = c; fd = fc; c = hi - (hi - lo) * GOLDEN; fc = total(c); }
    }
    best = Math.max(best, fc, fd);
  }
  return best;
}

// Rounded like the backend (4 decimals), so the optimistic and the confirmed value agree.
const round = (value: number) => Math.round(value * 1e4) / 1e4;

export function headroom(eq: EQ) {
  // Only large EQ boosts lower the level; the input level, volume boost and widening never do.
  const excess = peakGain(eq) + Math.min(0, eq.preamp) - ALLOWANCE;
  let compensation = excess > 0 ? -excess : 0;
  compensation = Math.max(compensation, MUTED - eq.preamp);  // The stage cannot go below MUTED.
  return { compensation: round(compensation), effective_preamp: round(eq.preamp + compensation) };
}
