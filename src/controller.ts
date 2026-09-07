import { cloneEQ, EQ, Result, Snapshot, Transport, View } from "./types";
import { headroom } from "./headroom";

/** One queue survives QAM remounts. Slider writes are coalesced, never concurrent. */
export class Controller {
  private view: View = { snapshot: null, error: null, note: null, busy: false, saving: false, connected: false };
  private listeners = new Set<() => void>();
  private desired: EQ | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private drain: Promise<void> | null = null;
  private reading: Promise<void> | null = null;
  private epoch = 0;
  private disposed = false;
  constructor(private transport: Transport) {}

  getSnapshot = (): View => this.view;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(update: Partial<View>) {
    if (this.disposed) return;
    this.view = { ...this.view, ...update };
    this.listeners.forEach(listener => listener());
  }
  clearMessage = () => this.publish({ error: null, note: null });
  reportError = (error: unknown) => this.publish({ error: error instanceof Error ? error.message : String(error) });

  refresh = async (): Promise<void> => {
    if (this.disposed || this.view.busy || this.desired || this.drain || this.reading) return;
    const epoch = this.epoch;
    const request = (async () => {
      try {
        const snapshot = await this.transport.status();
        if (epoch === this.epoch && !this.disposed)
          this.publish({ snapshot, connected: true, error: this.view.connected ? this.view.error : null });
      } catch (error) {
        if (epoch === this.epoch && !this.disposed)
          this.publish({ connected: false, error: `Could not reach DeckyEQ. ${String(error)}` });
      }
    })();
    this.reading = request;
    try { await request; } finally { if (this.reading === request) this.reading = null; }
  };

  edit = (eq: EQ) => {
    if (!this.view.snapshot || !this.view.connected || this.view.busy || this.disposed) return;
    this.epoch++;
    this.desired = cloneEQ(eq);
    const snapshot = this.optimistic(this.view.snapshot, eq);
    this.publish({ snapshot, saving: true, error: null, note: null });
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; void this.flush(); }, 80);
  };

  private optimistic(snapshot: Snapshot, eq: EQ): Snapshot {
    const preset = snapshot.presets.find(p => p.id === snapshot.state.selected);
    return { ...snapshot, state: { ...snapshot.state, working: cloneEQ(eq) },
      dirty: JSON.stringify(eq) !== JSON.stringify(preset?.eq),
      audio: { ...snapshot.audio, ...headroom(eq) } };
  }

  flush = async (): Promise<void> => {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (this.drain) { await this.drain; return; }
    if (this.disposed || !this.desired) return;
    const request = this.writePending();
    this.drain = request;
    try { await request; } finally { if (this.drain === request) this.drain = null; }
  };

  private async writePending() {
    while (this.desired && this.view.snapshot && !this.disposed) {
      const eq = this.desired;
      this.desired = null;
      try {
        const result = await this.transport.perform("edit", { eq }, this.view.snapshot.revision);
        if (!result.ok || !result.state) {
          this.desired = null;
          this.publish({ snapshot: result.state || this.view.snapshot, error: result.error || "EQ was not applied.", saving: false });
          return;
        }
        this.publish({ snapshot: this.desired ? this.optimistic(result.state, this.desired) : result.state, connected: true });
      } catch (error) {
        this.desired = null;
        this.publish({ error: `Change not confirmed. ${String(error)}`, connected: false, saving: false });
        return;
      }
    }
    this.publish({ saving: false });
  }

  action = async (action: string, payload: Record<string, unknown> = {}): Promise<Result> => {
    if (this.disposed || this.view.busy || !this.view.snapshot || !this.view.connected)
      return { ok: false, error: "DeckyEQ is busy or disconnected." };
    this.epoch++;
    this.publish({ busy: true, error: null, note: null });
    try {
      await this.flush();
      if (this.view.error || !this.view.connected)
        return { ok: false, error: this.view.error || "Connection lost." };
      const result = await this.transport.perform(action, payload, this.view.snapshot!.revision);
      if (result.state) this.publish({ snapshot: result.state });
      if (!result.ok) this.publish({ error: result.error || "The operation failed." });
      else this.publish({ note: result.path ? `Exported to ${result.path}` : result.message || null });
      return result;
    } catch (error) {
      this.publish({ error: String(error), connected: false });
      return { ok: false, error: String(error) };
    } finally { this.publish({ busy: false }); }
  };

  dispose() {
    this.disposed = true;
    this.epoch++;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.desired = null;
    this.listeners.clear();
  }
}
