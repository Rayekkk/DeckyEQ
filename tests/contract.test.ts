import { expect, it, vi } from "vitest";
import { Controller } from "../src/controller";
import { Band, cloneEQ, EQ, Output, Result, Settings, Snapshot } from "../src/types";
import fixture from "./fixtures/snapshot.json";

// The fixture is the real Python Service.snapshot() (kept in sync by tests/test_contract.py).
const snapshot = fixture as unknown as Snapshot;

it("does not mark an untouched backend draft as edited", async () => {
  const controller = new Controller({ status: async () => structuredClone(snapshot), perform: vi.fn(() => new Promise<Result>(() => {})) });
  await controller.refresh();
  controller.edit(cloneEQ(snapshot.state.working));
  expect(controller.getSnapshot().snapshot!.dirty).toBe(false);
  const changed = cloneEQ(snapshot.state.working);
  changed.bass = 2;
  controller.edit(changed);
  expect(controller.getSnapshot().snapshot!.dirty).toBe(true);
  controller.dispose();
});

// Every key the panel's types declare; `satisfies` fails the typecheck when a type gains or loses one.
const zero = 0 as const;
const snapshotKeys = { version: zero, revision: zero, state: zero, presets: zero, dirty: zero, game: zero, audio: zero,
  warning: zero, home: zero, export_directory: zero } satisfies Record<keyof Snapshot, 0>;
const stateKeys = { version: zero, enabled: zero, target: zero, speakers_only: zero, boost: zero, selected: zero,
  working: zero, scope: zero, games: zero } satisfies Record<keyof Settings, 0>;
const audioKeys = { supported: zero, phase: zero, message: zero, error: zero, game_error: zero, outputs: zero, default: zero,
  compensation: zero, effective_preamp: zero } satisfies Record<keyof Snapshot["audio"], 0>;
const outputKeys = { name: zero, description: zero, port: zero, speaker: zero } satisfies Record<keyof Output, 0>;
const eqKeys = { bands: zero, bass: zero, treble: zero, preamp: zero, auto_headroom: zero, width: zero, limiter: zero } satisfies Record<keyof EQ, 0>;
const bandKeys = { frequency: zero, gain: zero, q: zero, type: zero } satisfies Record<keyof Band, 0>;
const gameKeys = { appid: zero, name: zero } satisfies Record<keyof NonNullable<Snapshot["game"]>, 0>;
const keysOf = (value: object) => Object.keys(value).sort();

it("has exactly the keys the panel's types declare", () => {
  expect(keysOf(snapshot)).toEqual(keysOf(snapshotKeys));
  expect(keysOf(snapshot.state)).toEqual(keysOf(stateKeys));
  expect(keysOf(snapshot.audio)).toEqual(keysOf(audioKeys));
  expect(keysOf(snapshot.audio.outputs[0])).toEqual(keysOf(outputKeys));
  expect(keysOf(snapshot.state.working)).toEqual(keysOf(eqKeys));
  expect(keysOf(snapshot.state.working.bands[0])).toEqual(keysOf(bandKeys));
  expect(keysOf(snapshot.game!)).toEqual(keysOf(gameKeys));  // The fixture has a running game ...
  expect(Object.values(snapshot.state.games).map(keysOf)).toEqual([["name"]]);  // ... with a saved sound.
});

it("matches the shape the panel reads", () => {
  expect(snapshot.presets.map(p => p.id).slice(0, 4)).toEqual(["flat", "clear-voices", "more-bass", "go-s-original"]);
  expect(snapshot.state.scope).toBe(snapshot.game!.appid in snapshot.state.games ? snapshot.game!.appid : "");
  expect("presets" in snapshot.state).toBe(false);
});
