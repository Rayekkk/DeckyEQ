import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { bandHz, flatPair, pairs, shown, withBand, withPairGain } from "../src/pairs";
import { EQ, flatEQ, limits, sameEQ } from "../src/types";
import goS from "../presets/legion-go-s-original.json";

const withGains = (gains: number[]): EQ => ({ ...flatEQ(), bands: flatEQ().bands.map((band, i) => ({ ...band, gain: gains[i] })) });
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

it("pairs neighbouring bands, showing their range and average gain to 0.5 dB", () => {
  const five = pairs(withGains([1, 2, -3, 2, 1, 1.5, 0.5, 0, -0.5, 0]));
  expect(five.map(p => p.label)).toEqual(["31.5 Hz – 63 Hz", "125 Hz – 250 Hz", "500 Hz – 1 kHz", "2 kHz – 4 kHz", "8 kHz – 16 kHz"]);
  expect(five.map(p => p.gain)).toEqual([1.5, -0.5, 1.5, 0.5, -0.5]);  // 1.25 rounds to 1.5; ±0.25 away from 0 alike.
  expect(five[2].frequency).toBeCloseTo(Math.sqrt(500 * 1000));
  expect(five.map(p => p.bands)).toEqual([[0, 1], [2, 3], [4, 5], [6, 7], [8, 9]]);
});

const goSEQ = () => ({ width: 1, limiter: true, ...goS.preset.eq }) as EQ;  // Band 2 at 0 Hz, band 8 at 40 kHz, band 9 a 48 kHz shelf.

it("shows a band that does nothing as a flat band at its usual frequency, and makes it real only when edited", () => {
  const eq = goSEQ();
  expect([1, 7, 8].map(i => shown(eq, i))).toEqual([63, 4000, 8000].map(frequency =>
    ({ type: "peaking", frequency, q: limits.flat_q, gain: 0 })));
  expect(shown(eq, 0)).toBe(eq.bands[0]);  // Real bands are shown as they are.
  const edited = withBand(eq, 1, { gain: 3 });
  expect(edited.bands[1]).toEqual({ type: "peaking", frequency: 63, q: limits.flat_q, gain: 3 });
  expect(eq.bands[1].frequency).toBe(0);  // The original is untouched.
  expect(withBand(eq, 1, { gain: 0, q: 2, type: "lowshelf" })).toEqual(eq);  // Left flat, it stays as it is: nothing is edited.
  const off = flatEQ();
  off.bands[8] = { type: "highshelf", frequency: 48000, gain: 0, q: 1 };  // The Go S file's: above the audio, it does nothing.
  expect(shown(off, 8)).toEqual({ type: "peaking", frequency: 8000, q: limits.flat_q, gain: 0 });
});

it("gives every preset five pairs of two bands, Go S included", () => {
  const eq = goSEQ();
  const five = pairs(eq);
  expect(five.map(p => p.bands)).toEqual([[0, 1], [2, 3], [4, 5], [6, 7], [8, 9]]);
  expect(five.map(p => p.label)).toEqual(["31.5 Hz – 63 Hz", "125 Hz – 250 Hz", "500 Hz – 1 kHz", "2 kHz – 4 kHz", "8 kHz – 16 kHz"]);
  expect(five.map(p => p.gain)).toEqual([-5, 5, -5, -2.5, 0]);
  // One step up moves both bands of every pair by the step, from where they are: the -10 dB shelf does not jump to -4.5.
  const moved = five.reduce((next, pair, p) => withPairGain(next, p, pair.gain + 0.5), eq);
  expect(moved.bands.map(b => b.gain)).toEqual([-9.5, 0.5, 5.5, 5.5, -4.5, -4.5, -4.5, 0.5, 0.5, 0.5]);
  expect(moved.bands.map(b => b.frequency)).toEqual(limits.frequencies);  // Edited, every band sits at its usual frequency.
  expect(pairs(moved).map(p => p.gain)).toEqual([-4.5, 5.5, -4.5, -2, 0.5]);  // Still the average.
  expect(eq.bands[0].gain).toBe(-10);
  expect(flatPair(eq, 0).bands.slice(0, 2)).toEqual([{ ...eq.bands[0], frequency: 31.5, gain: 0 }, eq.bands[1]]);  // X: both flat.
  expect(flatPair(withGains([0, 0, 3, -4, 0, 0, 0, 0, 0, 0]), 1).bands.map(b => b.gain)).toEqual(flatEQ().bands.map(() => 0));
});

it("moves a pair to the gain asked for when one of its bands is at a limit: the other makes up the rest", () => {
  const gains = (eq: EQ, p = 0) => eq.bands.slice(2 * p, 2 * p + 2).map(b => b.gain);
  let eq = withGains([12, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
  for (const gain of [6.5, 7, 7.5]) {  // Every step shows, none is swallowed by the band at +12.
    eq = withPairGain(eq, 0, gain);
    expect(pairs(eq)[0].gain).toBe(gain);
  }
  expect(gains(eq)).toEqual([12, 3]);
  expect(sameEQ(withPairGain(eq, 0, 7.5), eq)).toBe(true);  // The same position again moves nothing: a drag does not creep.
  const apart = withPairGain(withGains([12, -12, 0, 0, 0, 0, 0, 0, 0, 0]), 0, 12);
  expect([gains(apart), pairs(apart)[0].gain]).toEqual([[12, 12], 12]);  // Released at +12, it stays at +12.
  expect(gains(withPairGain(withGains([12, 6, 0, 0, 0, 0, 0, 0, 0, 0]), 0, 10))).toEqual([12, 8]);
  expect(gains(withPairGain(withGains([12, 6, 0, 0, 0, 0, 0, 0, 0, 0]), 0, -12))).toEqual([-12, -12]);
});

it("names every band by its usual frequency and moves one near it there once edited; one far away keeps its own", () => {
  const eq = goSEQ();
  expect(bandHz(eq, 0)).toBe(31.5);  // Its 31.25 Hz shelf is band 1, like everywhere.
  expect(bandHz(eq, 1)).toBe(63);  // The 0 Hz placeholder.
  expect(withBand(eq, 0, { gain: -9.5 }).bands[0]).toEqual({ type: "lowshelf", frequency: 31.5, gain: -9.5, q: 1 });
  expect(eq.bands.map((_, i) => bandHz(eq, i))).toEqual(limits.frequencies);  // Go S: the ten usual bands.
  const moved = flatEQ();
  moved.bands[2] = { ...moved.bands[2], frequency: 3000, gain: 6 };  // Moved in DeckyEQ 1.0 from 125 Hz, nearer 4 kHz now.
  expect([bandHz(moved, 2), shown(moved, 2).frequency]).toEqual([3000, 3000]);  // Named where it plays.
  expect(withBand(moved, 2, { gain: 6.5 }).bands[2]).toEqual({ ...moved.bands[2], gain: 6.5 });  // Its gain only, still 3 kHz.
  expect(withBand(moved, 2, { q: 2, type: "highshelf" }).bands[2]).toMatchObject({ frequency: 3000, q: 2, type: "highshelf" });
  expect(pairs(moved)[1].label).toBe("250 Hz – 3 kHz");
  expect(withPairGain(moved, 1, 4).bands[2]).toMatchObject({ frequency: 3000, gain: 7 });
  moved.bands[6] = { ...moved.bands[6], frequency: 2500, gain: 4 };  // Nearer its own 2 kHz than 4 kHz: moves there.
  expect(bandHz(moved, 6)).toBe(2000);
  expect(withBand(moved, 6, { gain: 5 }).bands[6]).toMatchObject({ frequency: 2000, gain: 5 });
});

it("keeps a shelf that acts as a level (a low shelf above 20 kHz, a high shelf at 0 Hz) as it is, with its own name", () => {
  const eq = flatEQ();
  eq.bands[9] = { ...eq.bands[9], frequency: 48000, type: "lowshelf", gain: 4 };  // Moves every frequency by 4 dB.
  eq.bands[1] = { ...eq.bands[1], frequency: 0, type: "highshelf", gain: -6 };
  expect(shown(eq, 9)).toBe(eq.bands[9]);
  expect(shown(eq, 1)).toBe(eq.bands[1]);
  expect([bandHz(eq, 9), bandHz(eq, 1)]).toEqual([48000, 0]);
  expect(pairs(eq).map(p => p.label)).toEqual(["0 Hz – 31.5 Hz", "125 Hz – 250 Hz", "500 Hz – 1 kHz", "2 kHz – 4 kHz", "8 kHz – 48 kHz"]);
  expect(withBand(eq, 9, { gain: 6 }).bands[9]).toEqual({ ...eq.bands[9], gain: 6 });  // It stays where it is.
  expect(withPairGain(eq, 4, pairs(eq)[4].gain + 0.5).bands.slice(8)).toEqual([{ ...eq.bands[8], gain: 0.5 }, { ...eq.bands[9], gain: 4.5 }]);
});

it("keeps a level a level through 0 dB, so a drag across 0 never turns it into a bell", () => {
  let eq = flatEQ();
  eq.bands[9] = { type: "lowshelf", frequency: 48000, gain: -1, q: 1 };
  for (const gain of [-0.5, 0, 0.5, 1]) eq = withBand(eq, 9, { gain });
  expect(eq.bands[9]).toEqual({ type: "lowshelf", frequency: 48000, gain: 1, q: 1 });
  eq = withPairGain(eq, 4, 0);  // The pair as well: 0 and +1 (+0.5) move down by 0.5.
  expect(eq.bands[9]).toEqual({ type: "lowshelf", frequency: 48000, gain: 0.5, q: 1 });
  eq = withBand(eq, 9, { gain: 0 });
  expect([shown(eq, 9), bandHz(eq, 9)]).toEqual([eq.bands[9], 48000]);  // Flat, still its own.
});

it("counts a shelf at the edge of 20 Hz–20 kHz as a level, as the backend snaps 20–24 kHz and 0–20 Hz there", () => {
  const eq = flatEQ();  // In the slots nearest them, so only being a level keeps them where they are.
  eq.bands[9] = { type: "lowshelf", frequency: 20000, gain: 6, q: 1 };  // Imported at 22 kHz: +6 dB everywhere.
  eq.bands[0] = { type: "highshelf", frequency: 20, gain: -6, q: 1 };
  expect([bandHz(eq, 9), bandHz(eq, 0)]).toEqual([20000, 20]);
  expect(withBand(eq, 9, { gain: 5.5 }).bands[9]).toEqual({ ...eq.bands[9], gain: 5.5 });  // Not moved to 16 kHz.
  expect(withBand(eq, 0, { gain: -5.5 }).bands[0]).toEqual({ ...eq.bands[0], gain: -5.5 });  // Not moved to 31.5 Hz.
  eq.bands[9] = { type: "lowshelf", frequency: 19999, gain: 3, q: 1 };  // Just inside: a real shelf at its band.
  expect([bandHz(eq, 9), withBand(eq, 9, { gain: 2 }).bands[9].frequency]).toEqual([16000, 16000]);
});

async function fresh() {
  vi.resetModules();  // The preference is read once per session.
  return import("../src/pairs");
}

it("keeps five sliders as this device's preference, off by default", async () => {
  let module = await fresh();
  const hook = renderHook(() => module.useFiveBands());
  expect(hook.result.current).toBe(false);
  act(() => module.setFiveBands(true));
  expect(hook.result.current).toBe(true);  // Every view follows at once.
  expect(localStorage.getItem("deckyeq.fiveBands")).toBe("1");
  hook.unmount();
  module = await fresh();
  expect(renderHook(() => module.useFiveBands()).result.current).toBe(true);
});

it("works for the session when storage is blocked", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  const module = await fresh();
  const hook = renderHook(() => module.useFiveBands());
  expect(hook.result.current).toBe(false);
  act(() => module.setFiveBands(true));
  expect(hook.result.current).toBe(true);
});
