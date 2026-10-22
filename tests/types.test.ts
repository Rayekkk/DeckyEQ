import { expect, it } from "vitest";
import { cloneEQ, db, flatEQ, formatHz, sameEQ } from "../src/types";

it("compares every field that makes a preset edited", () => {
  const base = flatEQ();
  for (const change of [{ width: 1.5 }, { limiter: false }, { preamp: -3 }, { auto_headroom: false }, { bass: 1 }, { treble: 1 }]) {
    expect(sameEQ(base, { ...flatEQ(), ...change })).toBe(false);
  }
  for (const key of ["q", "gain", "frequency", "type"] as const) {
    const other = flatEQ();
    other.bands[3] = { ...other.bands[3], [key]: key === "type" ? "lowshelf" : 2.5 };
    expect(sameEQ(base, other)).toBe(false);
  }
  expect(sameEQ(base, flatEQ())).toBe(true);
});

it("clones bands deeply", () => {
  const eq = flatEQ();
  const copy = cloneEQ(eq);
  copy.bands[0].gain = 5;
  expect(eq.bands[0].gain).toBe(0);
});

it("formats frequencies and levels for the panel", () => {
  expect([formatHz(999.5), formatHz(1000), formatHz(31.25)]).toEqual(["999.5 Hz", "1 kHz", "31.25 Hz"]);
  expect([db(0), db(3), db(-2.25)]).toEqual(["0.0 dB", "+3.0 dB", "-2.3 dB"]);
});
