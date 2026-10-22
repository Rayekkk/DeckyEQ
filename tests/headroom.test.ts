import { expect, it } from "vitest";
import { ALLOWANCE, headroom, MAX_BOOST, peakGain } from "../src/headroom";
import { flatEQ } from "../src/types";

it("compensates the combined peak, not the sum of all boosts", () => {
  const eq = flatEQ(); eq.bass = 6; eq.treble = 6; eq.limiter = false;
  expect(headroom(eq).compensation).toBeCloseTo(-6.5, 2);
  eq.bass = 0; eq.treble = 0; eq.preamp = 8;
  expect(headroom(eq).effective_preamp).toBeCloseTo(-0.5, 6);
  eq.auto_headroom = false;
  expect(headroom(eq)).toEqual({ compensation: 0, effective_preamp: 8 });
});

it("counts a resonant shelf cut's overshoot", () => {
  const eq = flatEQ();
  eq.bands[0] = { ...eq.bands[0], type: "highshelf", gain: -6, q: 5 };
  expect(peakGain(eq)).toBeGreaterThan(8);
});

it("caps extreme manual boost like the backend", () => {
  const eq = flatEQ(); eq.auto_headroom = false; eq.preamp = 12;
  for (const band of eq.bands) band.gain = 12;
  const { compensation } = headroom(eq);
  expect(eq.preamp + peakGain(eq) + compensation).toBeCloseTo(MAX_BOOST, 6);
});

it("leaves moderate boosts to the limiter and counts widening", () => {
  const eq = flatEQ(); eq.bass = 5;
  expect(headroom(eq).compensation).toBe(0);
  eq.bass = 9;
  expect(headroom(eq).compensation).toBeCloseTo(-(peakGain(eq) - ALLOWANCE), 6);
  eq.bass = 0; eq.width = 2; eq.limiter = false;
  expect(headroom(eq).compensation).toBeCloseTo(-(20 * Math.log10(2) + 0.5), 6);  // L = -R doubles.
});
