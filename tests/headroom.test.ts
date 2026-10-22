import { expect, it } from "vitest";
import { ALLOWANCE, headroom, peakGain } from "../src/headroom";
import { flatEQ } from "../src/types";

it("compensates the combined peak, not the sum of all boosts", () => {
  const eq = flatEQ(); eq.bass = 9; eq.treble = 9;
  expect(peakGain(eq)).toBeCloseTo(9, 1);
  expect(headroom(eq).compensation).toBeCloseTo(-(peakGain(eq) - ALLOWANCE), 6);
  eq.bass = 0; eq.treble = 0; eq.preamp = 8;
  expect(headroom(eq).effective_preamp).toBe(8);  // A raised input level is kept.
});

it("counts a resonant shelf cut's overshoot", () => {
  const eq = flatEQ();
  eq.bands[0] = { ...eq.bands[0], type: "highshelf", gain: -6, q: 5 };
  expect(peakGain(eq)).toBeGreaterThan(8);
});

it("takes back only extreme EQ boost, whatever the old manual field says", () => {
  const eq = flatEQ(); eq.auto_headroom = false; eq.preamp = 12;
  for (const band of eq.bands) band.gain = 12;
  expect(headroom(eq).compensation).toBeCloseTo(-(peakGain(eq) - ALLOWANCE), 6);
});

it("leaves moderate boosts to the limiter and never lowers for width or boost", () => {
  const eq = flatEQ(); eq.bass = 5;
  expect(headroom(eq).compensation).toBe(0);
  eq.bass = 0; eq.width = 2; eq.preamp = 12;
  expect(headroom(eq, 12).compensation).toBe(0);
});
