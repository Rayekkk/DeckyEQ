import { EQ, limits } from "./types";

/** Mirrors the backend's analytic shelf bound for optimistic UI values. */
export function shelfPeak(gain: number, q: number): number {
  let peak = Math.max(0, gain);
  if (gain === 0 || q <= Math.SQRT1_2) return peak;
  const a = 10 ** (gain / 40), cross = a * (1 / q ** 2 - 2), b = 1 + a * a;
  const x = -cross / (b + Math.sqrt(Math.max(0, b * b - cross * cross)));
  for (const point of [x, 1 / x]) {
    const squared = a * a * ((point - a) ** 2 + a * point / q ** 2) / ((1 - a * point) ** 2 + a * point / q ** 2);
    peak = Math.max(peak, 10 * Math.log10(squared));
  }
  return peak;
}

/** Mirrors graph.MAX_BOOST: manual headroom still caps the summed peak gain. */
export const MAX_BOOST = limits.max_boost;

export function headroom(eq: EQ) {
  const boost = eq.bands.reduce((sum, band) => sum + (band.type === "peaking" ? Math.max(0, band.gain) : shelfPeak(band.gain, band.q)), 0)
    + Math.max(0, eq.bass) + Math.max(0, eq.treble) + Math.max(0, eq.preamp);
  const compensation = eq.auto_headroom && boost > 0 ? -(boost + 0.5) : Math.min(0, MAX_BOOST - boost);
  return { compensation, effective_preamp: eq.preamp + compensation };
}
