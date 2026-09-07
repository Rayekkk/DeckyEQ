import { afterEach, describe, expect, it, vi } from "vitest";
import { Controller } from "../src/controller";
import { flatEQ, Result, Snapshot, Transport } from "../src/types";
import { seed } from "../preview/engine";

function deferred<T>() { let resolve!: (v: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
function harness() {
  let state = seed();
  const transport: Transport = {
    status: vi.fn(async () => structuredClone(state)),
    perform: vi.fn(async (action, payload, revision) => {
      expect(revision).toBe(state.revision);
      if (action === "edit") state.state.working = structuredClone(payload.eq) as any;
      state.revision++;
      return { ok: true, state: structuredClone(state) };
    }),
  };
  return { transport, controller: new Controller(transport), get state() { return state; }, set state(value) { state = value; } };
}
afterEach(() => vi.useRealTimers());

describe("serialized live edits", () => {
  it("coalesces a burst into one final write", async () => {
    const h = harness(); await h.controller.refresh();
    for (const bass of [1, 2, 4]) h.controller.edit({ ...flatEQ(), bass });
    await h.controller.flush();
    expect(h.transport.perform).toHaveBeenCalledTimes(1);
    expect(h.state.state.working.bass).toBe(4);
    expect(h.controller.getSnapshot().saving).toBe(false);
    h.controller.dispose();
  });

  it("keeps newer slider edits visible while an older write completes", async () => {
    const h = harness(); await h.controller.refresh();
    const first = deferred<Result>();
    vi.mocked(h.transport.perform).mockImplementationOnce(() => first.promise);
    h.controller.edit({ ...flatEQ(), bass: 1 });
    const flushing = h.controller.flush();
    h.controller.edit({ ...flatEQ(), bass: 6 });
    const accepted = seed(); accepted.revision = 1; accepted.state.working.bass = 1;
    h.state = structuredClone(accepted);
    first.resolve({ ok: true, state: accepted });
    await flushing;
    expect(h.state.state.working.bass).toBe(6);
    expect(h.controller.getSnapshot().snapshot?.state.working.bass).toBe(6);
    expect(h.transport.perform).toHaveBeenCalledTimes(2);
    h.controller.dispose();
  });

  it("flushes the latest draft before exporting or saving", async () => {
    const h = harness(); await h.controller.refresh();
    h.controller.edit({ ...flatEQ(), treble: 3 });
    await h.controller.action("export", { all: false });
    expect(vi.mocked(h.transport.perform).mock.calls.map(args => args[0])).toEqual(["edit", "export"]);
    expect(vi.mocked(h.transport.perform).mock.calls[1][2]).toBe(1);
    h.controller.dispose();
  });

  it("discards a stale status response after a mutation", async () => {
    const h = harness(); await h.controller.refresh();
    const late = deferred<Snapshot>();
    vi.mocked(h.transport.status).mockImplementationOnce(() => late.promise);
    const pending = h.controller.refresh();
    h.controller.edit({ ...flatEQ(), bass: 4 }); await h.controller.flush();
    late.resolve(seed()); await pending;
    expect(h.controller.getSnapshot().snapshot?.state.working.bass).toBe(4);
    h.controller.dispose();
  });

  it("does not export after a rejected unconfirmed edit", async () => {
    const h = harness(); await h.controller.refresh();
    vi.mocked(h.transport.perform).mockResolvedValueOnce({ ok: false, error: "Readback failed", state: seed() });
    h.controller.edit({ ...flatEQ(), bass: 7 });
    const result = await h.controller.action("export");
    expect(result.ok).toBe(false);
    expect(h.transport.perform).toHaveBeenCalledTimes(1);
    expect(h.controller.getSnapshot().snapshot?.state.working.bass).toBe(0);
    expect(h.controller.getSnapshot().error).toBe("Readback failed");
    h.controller.dispose();
  });

  it("locks edits after transport failure until a status refresh", async () => {
    const h = harness(); await h.controller.refresh();
    vi.mocked(h.transport.perform).mockRejectedValueOnce(new Error("Disconnected"));
    h.controller.edit({ ...flatEQ(), bass: 3 }); await h.controller.flush();
    expect(h.controller.getSnapshot().connected).toBe(false);
    h.controller.edit({ ...flatEQ(), bass: 8 });
    await h.controller.refresh();
    expect(h.controller.getSnapshot().connected).toBe(true);
    expect(h.controller.getSnapshot().snapshot?.state.working.bass).toBe(0);
    h.controller.dispose();
  });

  it("cancels pending timers on plugin unload", async () => {
    vi.useFakeTimers();
    const h = harness(); await h.controller.refresh();
    h.controller.edit({ ...flatEQ(), bass: 3 }); h.controller.dispose();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.transport.perform).not.toHaveBeenCalled();
  });
});
