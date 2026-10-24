import { afterEach, describe, expect, it, vi } from "vitest";
import { Controller } from "../src/controller";
import { flatEQ, Result, Snapshot, Transport } from "../src/types";
import { headroom } from "../src/headroom";
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

describe("polling", () => {
  it("does not re-render when the backend state is unchanged", async () => {
    const h = harness(); await h.controller.refresh();
    const listener = vi.fn();
    h.controller.subscribe(listener);
    await h.controller.refresh();
    expect(listener).not.toHaveBeenCalled();
    h.state = { ...h.state, revision: h.state.revision + 1 };
    await h.controller.refresh();
    expect(listener).toHaveBeenCalledTimes(1);
    h.controller.dispose();
  });
});

describe("failed writes", () => {
  it("keeps telling the user that a change was not confirmed after reconnecting", async () => {
    const h = harness(); await h.controller.refresh();
    vi.mocked(h.transport.perform).mockRejectedValueOnce(new Error("socket closed"));
    h.controller.edit({ ...flatEQ(), bass: 5 });
    await h.controller.flush();
    expect(h.controller.getSnapshot().error).toBe("Change not confirmed. socket closed");
    await h.controller.refresh();
    expect(h.controller.getSnapshot().connected).toBe(true);
    expect(h.controller.getSnapshot().error).toContain("not confirmed");
    expect(h.controller.getSnapshot().snapshot!.state.working.bass).toBe(0);
    h.controller.dispose();
  });

  it("says a change was not confirmed only once, not on every later reconnect", async () => {
    const h = harness(); await h.controller.refresh();
    vi.mocked(h.transport.perform).mockRejectedValueOnce(new Error("socket closed"));
    h.controller.edit({ ...flatEQ(), bass: 5 });
    await h.controller.flush();
    await h.controller.refresh();
    expect(h.controller.getSnapshot().error).toContain("not confirmed");
    vi.mocked(h.transport.status).mockRejectedValueOnce(new Error("gone"));
    await h.controller.refresh();
    expect(h.controller.getSnapshot().connected).toBe(false);
    await h.controller.refresh();
    expect(h.controller.getSnapshot().connected).toBe(true);
    expect(h.controller.getSnapshot().error).toBeNull();
    h.controller.dispose();
  });

  it("takes the backend's null message as no note", async () => {
    const h = harness(); await h.controller.refresh();
    const result: Result = { ok: true, state: structuredClone(h.state), message: null };
    vi.mocked(h.transport.perform).mockResolvedValueOnce(result);
    await h.controller.action("save");
    expect(h.controller.getSnapshot().note).toBeNull();
    h.controller.dispose();
  });
});

describe("refused edits", () => {
  it("tells the listener once per refused write, not for a lost connection", async () => {
    const h = harness(); await h.controller.refresh();
    const refused = vi.fn();
    h.controller.onEditRejected = refused;
    vi.mocked(h.transport.perform).mockResolvedValueOnce({ ok: false, error: "Readback failed", state: seed() });
    h.controller.edit({ ...flatEQ(), bass: 4 });
    await h.controller.flush();
    expect(refused).toHaveBeenCalledExactlyOnceWith("Readback failed");
    await h.controller.refresh();
    expect(refused).toHaveBeenCalledOnce();  // Polling the same error again does not repeat it.
    vi.mocked(h.transport.perform).mockRejectedValueOnce(new Error("socket closed"));
    h.controller.edit({ ...flatEQ(), bass: 5 });
    await h.controller.flush();
    expect(refused).toHaveBeenCalledOnce();
    h.controller.dispose();
  });
});

describe("edits during an action", () => {
  it("ignores a slider edit while an action runs, so two writes are never in flight", async () => {
    const h = harness(); await h.controller.refresh();
    const reset = deferred<Result>();
    vi.mocked(h.transport.perform).mockImplementationOnce(() => reset.promise);
    const acting = h.controller.action("reset");
    await vi.waitFor(() => expect(h.transport.perform).toHaveBeenCalledOnce());
    h.controller.edit({ ...flatEQ(), bass: 5 });
    expect(h.controller.getSnapshot().snapshot!.state.working.bass).toBe(0);  // Not shown as if it were applied.
    await h.controller.flush();
    expect(h.transport.perform).toHaveBeenCalledOnce();
    reset.resolve({ ok: true, state: structuredClone(h.state) });
    await acting;
    h.controller.dispose();
  });
});

describe("disconnected actions", () => {
  it("tells the user when an action cannot run", async () => {
    const h = harness(); await h.controller.refresh();
    vi.mocked(h.transport.status).mockRejectedValueOnce(new Error("backend gone"));
    await h.controller.refresh();
    h.controller.clearMessage();
    const result = await h.controller.action("reset");
    expect(result.ok).toBe(false);
    expect(h.controller.getSnapshot().error).toContain("disconnected");
    h.controller.dispose();
  });
});

describe("audit regressions", () => {
  it("shows the newer edit, not the older write's result, while the newer one is in flight", async () => {
    const h = harness(); await h.controller.refresh();
    const first = deferred<Result>(), second = deferred<Result>();
    vi.mocked(h.transport.perform).mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    h.controller.edit({ ...flatEQ(), bass: 1 });
    const flushing = h.controller.flush();
    h.controller.edit({ ...flatEQ(), bass: 6 });
    const accepted = seed(); accepted.revision = 1; accepted.state.working.bass = 1;
    first.resolve({ ok: true, state: accepted });
    await vi.waitFor(() => expect(h.transport.perform).toHaveBeenCalledTimes(2));
    expect(h.controller.getSnapshot().snapshot?.state.working.bass).toBe(6);  // No snap back to 1.
    const done = seed(); done.revision = 2; done.state.working.bass = 6;
    second.resolve({ ok: true, state: done });
    await flushing;
    h.controller.dispose();
  });

  it("keeps a failed action's error after the backend answers again", async () => {
    const h = harness(); await h.controller.refresh();
    vi.mocked(h.transport.perform).mockRejectedValueOnce(new Error("socket closed"));
    await h.controller.action("save");
    expect(h.controller.getSnapshot().connected).toBe(false);
    await h.controller.refresh();
    expect(h.controller.getSnapshot().connected).toBe(true);
    expect(h.controller.getSnapshot().error).toContain("not confirmed");
    h.controller.dispose();
  });

  it("reads the status once while a read is already in flight", async () => {
    const h = harness(); await h.controller.refresh();
    const late = deferred<Snapshot>();
    vi.mocked(h.transport.status).mockImplementationOnce(() => late.promise);
    const first = h.controller.refresh(), second = h.controller.refresh();
    late.resolve(seed());
    await Promise.all([first, second]);
    expect(h.transport.status).toHaveBeenCalledTimes(2);  // The initial read and one poll.
    h.controller.dispose();
  });

  it("clears its debounce timer on unload", async () => {
    const h = harness(); await h.controller.refresh();
    const clear = vi.spyOn(globalThis, "clearTimeout");
    h.controller.edit({ ...flatEQ(), bass: 3 });
    h.controller.dispose();
    expect(clear).toHaveBeenCalled();
  });
});

describe("boost and polling", () => {
  it("previews headroom with the boost that is about to be applied", async () => {
    const h = harness(); await h.controller.refresh();
    const eq = { ...flatEQ(), bass: 9 };
    h.controller.edit(eq, 6);
    const audio = h.controller.getSnapshot().snapshot!.audio;
    expect(audio.compensation).toBeCloseTo(headroom(eq).compensation, 6);
    expect(audio.compensation).toBeLessThan(0);  // Updated at once, before the backend answers.
    expect(h.controller.getSnapshot().snapshot!.state.boost).toBe(6);
    h.controller.dispose();
  });

  it("does not poll while a write is in flight", async () => {
    const h = harness(); await h.controller.refresh();
    const write = deferred<Result>();
    vi.mocked(h.transport.perform).mockImplementationOnce(() => write.promise);
    h.controller.edit({ ...flatEQ(), bass: 2 });
    const flushing = h.controller.flush();
    await h.controller.refresh();
    expect(h.transport.status).toHaveBeenCalledTimes(1);  // Only the initial read.
    const done = seed(); done.revision = 1; done.state.working.bass = 2;
    write.resolve({ ok: true, state: done });
    await flushing;
    h.controller.dispose();
  });
});

describe("queue promises (audit mutations)", () => {
  it("never has two writes in flight", async () => {
    const h = harness(); await h.controller.refresh();
    let inFlight = 0, most = 0;
    const real = vi.mocked(h.transport.perform).getMockImplementation()!;
    vi.mocked(h.transport.perform).mockImplementation(async (...args) => {
      most = Math.max(most, ++inFlight);
      await new Promise(r => setTimeout(r, 5));
      try { return await real(...args); } finally { inFlight--; }
    });
    h.controller.edit({ ...flatEQ(), bass: 1 });
    const first = h.controller.flush();
    h.controller.edit({ ...flatEQ(), bass: 2 });
    const second = h.controller.flush();
    await Promise.all([first, second]);
    expect(most).toBe(1);
    expect(h.state.state.working.bass).toBe(2);
    h.controller.dispose();
  });

  it("does not poll while an edit waits for its debounce", async () => {
    const h = harness(); await h.controller.refresh();
    h.controller.edit({ ...flatEQ(), bass: 3 });
    await h.controller.refresh();
    expect(h.transport.status).toHaveBeenCalledTimes(1);
    expect(h.controller.getSnapshot().snapshot!.state.working.bass).toBe(3);  // No snap back.
    h.controller.dispose();
  });

  it("ignores a poll that started before an action", async () => {
    const h = harness(); await h.controller.refresh();
    const late = deferred<Snapshot>();
    vi.mocked(h.transport.status).mockImplementationOnce(() => late.promise);
    const polling = h.controller.refresh();
    await h.controller.action("reset");  // Changes the backend state.
    late.resolve(seed());  // The old answer arrives afterwards.
    await polling;
    expect(h.controller.getSnapshot().snapshot!.revision).toBe(1);
    h.controller.dispose();
  });

  it("keeps a moved boost when an EQ edit follows before the write", async () => {
    const h = harness(); await h.controller.refresh();
    h.controller.edit(flatEQ(), 6);
    h.controller.edit({ ...flatEQ(), bass: 2 });
    await h.controller.flush();
    expect(vi.mocked(h.transport.perform).mock.calls.at(-1)![1]).toMatchObject({ boost: 6, eq: { bass: 2 } });
    h.controller.dispose();
  });

  it("drops edits queued behind a rejected write and shows the backend's state", async () => {
    const h = harness(); await h.controller.refresh();
    const first = deferred<Result>();
    vi.mocked(h.transport.perform).mockImplementationOnce(() => first.promise);
    h.controller.edit({ ...flatEQ(), bass: 4 });
    const flushing = h.controller.flush();
    h.controller.edit({ ...flatEQ(), bass: 8 });
    first.resolve({ ok: false, error: "Not confirmed", state: seed() });
    await flushing;
    expect(h.transport.perform).toHaveBeenCalledTimes(1);
    expect(h.controller.getSnapshot().snapshot!.state.working.bass).toBe(0);
    expect(h.controller.getSnapshot().error).toBe("Not confirmed");
    h.controller.dispose();
  });
});

describe("export notice", () => {
  it("names the exported file from the home folder when it is inside it, otherwise in full", async () => {
    const h = harness(); await h.controller.refresh();
    for (const [home, path, shown] of [
      ["/home/deck", "/home/deck/Documents/DeckyEQ/Flat.json", "Documents/DeckyEQ/Flat.json"],
      ["/home/deck/", "/home/deck/Documents/DeckyEQ/Flat.json", "Documents/DeckyEQ/Flat.json"],
      ["/home/deck", "/run/media/deck/card/Flat.json", "/run/media/deck/card/Flat.json"],
      ["/home/deck", "/home/decker/Flat.json", "/home/decker/Flat.json"],  // Starts alike, is not inside.
      ["", "/home/deck/Flat.json", "/home/deck/Flat.json"],
    ]) {
      h.state.home = home;
      vi.mocked(h.transport.perform).mockResolvedValueOnce({ ok: true, state: structuredClone(h.state), path });
      await h.controller.action("export");
      expect(h.controller.getSnapshot().note).toBe(`Exported to ${shown}`);
    }
    h.controller.dispose();
  });
});
