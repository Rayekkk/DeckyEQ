import { expect, it, vi } from "vitest";
import { Controller } from "../src/controller";
import { cloneEQ, Snapshot } from "../src/types";
import fixture from "./fixtures/snapshot.json";

// The fixture is the real Python Service.snapshot() (kept in sync by tests/test_contract.py).
const snapshot = fixture as unknown as Snapshot;

it("does not mark an untouched backend draft as edited", async () => {
  const controller = new Controller({ status: async () => structuredClone(snapshot), perform: vi.fn(() => new Promise(() => {})) });
  await controller.refresh();
  controller.edit(cloneEQ(snapshot.state.working));
  expect(controller.getSnapshot().snapshot!.dirty).toBe(false);
  const changed = cloneEQ(snapshot.state.working);
  changed.bass = 2;
  controller.edit(changed);
  expect(controller.getSnapshot().snapshot!.dirty).toBe(true);
  controller.dispose();
});

it("matches the shape the panel reads", () => {
  expect(snapshot.presets.map(p => p.id).slice(0, 2)).toEqual(["flat", "go-s-original"]);
  expect(Object.keys(snapshot.audio).sort()).toEqual(
    ["compensation", "default", "effective_preamp", "error", "message", "outputs", "phase", "supported"]);
  expect("presets" in snapshot.state).toBe(false);
});
