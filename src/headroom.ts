import { Band, EQ, limits } from "./types";

// Mirrors deckyeq/graph.py (checked by tests/parity.test.ts against backend-generated vectors).
/** Manual headroom still caps the peak gain at this many dB (Go S original: ~14.6). */
export const MAX_BOOST = limits.max_boost;
const MARGIN = limits.headroom_margin;
/** With the limiter on, automatic headroom leaves this much boost to it. */
export const ALLOWANCE = limits.limiter_allowance;
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

/** Response in dB of one filter at each point in Hz. */
export function responseDb(filter: Filter, points: number[]): number[] {
  const design = biquad(filter);
  return points.map(hz => gainDb(design, hz));
}

/** Highest gain, in dB, of all filters together (bands and tone shelves; without the preamp). */
export function peakGain(eq: EQ): number {
  const filters: Filter[] = [
    ...eq.bands.map((b): Filter => [b.type, b.frequency, b.q, b.gain]),
    ["lowshelf", limits.bass_frequency, SHELF_Q, eq.bass], ["highshelf", limits.treble_frequency, SHELF_Q, eq.treble],
  ].filter(f => f[3] !== 0) as Filter[];
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

export function headroom(eq: EQ, boost = 0) {
  // Widening multiplies the side signal by width: opposite-phase content (L = -R) grows by all of it.
  // With the limiter on (also when a volume boost forces it), a raised input level is deliberate gain:
  // only filter boost beyond ALLOWANCE counts, and a lowered input level still counts as room.
  const shaped = peakGain(eq) + (eq.width > 1 ? 20 * Math.log10(eq.width) : 0);
  const peak = eq.preamp + shaped;
  const excess = shaped + Math.min(0, eq.preamp) - ALLOWANCE;
  const compensation = !eq.auto_headroom ? Math.min(0, MAX_BOOST - peak)
    : eq.limiter || boost > 0 ? (excess > 0 ? -excess : 0)
    : peak > 0 ? -(peak + MARGIN) : 0;
  return { compensation, effective_preamp: eq.preamp + compensation };
}
