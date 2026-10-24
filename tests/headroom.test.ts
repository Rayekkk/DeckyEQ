import { expect, it } from "vitest";
import { ALLOWANCE, headroom, peakGain } from "../src/headroom";
import { flatEQ, limits } from "../src/types";

it("compensates the combined peak, not the sum of all boosts", () => {
  const eq = flatEQ(); eq.bass = 9; eq.treble = 9;
  expect(peakGain(eq)).toBeCloseTo(9, 1);
  expect(headroom(eq).compensation).toBeCloseTo(-(peakGain(eq) - ALLOWANCE), 3);
  eq.bass = 0; eq.treble = 0; eq.preamp = 8;
  expect(headroom(eq).effective_preamp).toBe(8);  // A raised input level is kept.
});

it("counts a resonant shelf cut's overshoot", () => {
  const eq = flatEQ();
  eq.bands[0] = { ...eq.bands[0], type: "highshelf", gain: -6, q: limits.shelf_q_max };
  expect(peakGain(eq)).toBeGreaterThan(1);  // A plain 6 dB cut never rises above 0 dB.
});

it("takes back only extreme EQ boost, whatever the old manual field says", () => {
  const eq = flatEQ(); eq.auto_headroom = false; eq.preamp = 12;
  for (const band of eq.bands) band.gain = 12;
  expect(headroom(eq).compensation).toBeCloseTo(-(peakGain(eq) - ALLOWANCE), 3);
});

it("leaves moderate boosts to the limiter and never lowers for width or boost", () => {
  const eq = flatEQ(); eq.bass = 5;
  expect(headroom(eq).compensation).toBe(0);
  eq.bass = 0; eq.width = 2; eq.preamp = 12;
  expect(headroom(eq).compensation).toBe(0);
});

it("rounds to 4 decimals like the backend, so the panel and the backend agree", () => {
  const eq = flatEQ(); eq.bass = 9; eq.preamp = 3.33333;
  const { compensation, effective_preamp } = headroom(eq);
  expect(compensation).toBe(Math.round(compensation * 1e4) / 1e4);
  expect(effective_preamp).toBe(Math.round(effective_preamp * 1e4) / 1e4);
  expect(compensation).not.toBe(-(peakGain(eq) - ALLOWANCE));
});

it("never lowers the level below the native stage's -96 dB floor", () => {
  const eq = flatEQ(); eq.bass = 9; eq.preamp = 4;
  eq.bands = eq.bands.map(band => ({ ...band, type: "lowshelf", frequency: 16000, gain: 12 }));  // About +129 dB at the top.
  expect(peakGain(eq) - ALLOWANCE).toBeGreaterThan(100);
  expect(headroom(eq)).toEqual({ compensation: -100, effective_preamp: -96 });
});
