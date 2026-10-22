import { expect, it } from "vitest";
import { headroom, shelfPeak } from "../src/headroom";
import { flatEQ } from "../src/types";

it("compensates positive preamp and resonant shelf cuts in the optimistic UI", () => {
  const eq = flatEQ(); eq.preamp = 8;
  expect(headroom(eq).effective_preamp).toBe(-0.5);
  eq.bands[0] = {...eq.bands[0], type: "lowshelf", gain: -6, q: 5};
  expect(shelfPeak(-6, 5)).toBeGreaterThan(8);
  expect(headroom(eq).effective_preamp).toBeLessThan(-8.5);
  eq.auto_headroom = false;
  expect(headroom(eq)).toEqual({compensation: 0, effective_preamp: 8});
});

it("keeps the version 1 bound for bells and ordinary tone shelves", () => {
  const eq = flatEQ(); eq.bass = 3; eq.treble = 2; eq.preamp = -1;
  for (const band of eq.bands) band.gain = 2;
  expect(headroom(eq).effective_preamp).toBe(-26.5);
});

it("caps extreme manual boost at +24 dB like the backend", () => {
  const eq = flatEQ(); eq.auto_headroom = false; eq.preamp = 12;
  for (const band of eq.bands) band.gain = 12;
  expect(headroom(eq)).toEqual({ compensation: -108, effective_preamp: -96 });
});
