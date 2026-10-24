import { cloneEQ, EQ, Result, sameEQ, Snapshot, Transport, View } from "./types";
import { headroom } from "./headroom";

const EDIT_DEBOUNCE_MS = 80;  // Coalesce slider drags into one backend write.
const text = (error: unknown) => error instanceof Error ? error.message : String(error);
/** A path relative to the home folder when it is inside it (the toast has little room); otherwise as it is. */
export const fromHome = (path: string, home: string) => {
  const base = home.replace(/\/+$/, "");
  return base && path.startsWith(`${base}/`) ? path.slice(base.length + 1) : path;
};
const UNCONFIRMED = "Your last change was not confirmed. The controls now show the sound DeckyEQ is using.";

/** One queue survives QAM remounts. Slider writes are coalesced, never concurrent. */
export class Controller {
  private view: View = { snapshot: null, error: null, note: null, busy: false, saving: false, connected: false };
  private listeners = new Set<() => void>();
  private desired: EQ | null = null;
  private desiredBoost: number | null = null;  // Travels with the next slider write.
  private timer: ReturnType<typeof setTimeout> | null = null;
  private drain: Promise<void> | null = null;
  private reading: Promise<void> | null = null;
  private epoch = 0;
  private disposed = false;
  private unconfirmed = false;  // A write failed in transit; say so once the backend is back.
  /** Told once per slider write the backend refused: the panel's note may be scrolled out of view. */
  onEditRejected?: (error: string) => void;
  constructor(private transport: Transport) {}

  getSnapshot = (): View => this.view;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(update: Partial<View>) {
    if (this.disposed) return;
    this.view = { ...this.view, ...update };
    this.listeners.forEach(listener => listener());
  }
  clearMessage = () => this.publish({ error: null, note: null });
  reportError = (error: unknown) => this.publish({ error: text(error) });

  refresh = async (): Promise<void> => {
    if (this.disposed || this.view.busy || this.desired || this.drain || this.reading) return;
    const epoch = this.epoch;
    const request = (async () => {
      try {
        const snapshot = await this.transport.status();
        // An unchanged poll must not re-render the whole panel every 1.8 s.
        const unchanged = this.view.connected && JSON.stringify(snapshot) === JSON.stringify(this.view.snapshot);
        if (epoch === this.epoch && !this.disposed && !unchanged) {
          this.publish({ snapshot, connected: true,
            error: this.view.connected ? this.view.error : this.unconfirmed ? UNCONFIRMED : null });
          this.unconfirmed = false;
        }
      } catch (error) {
        if (epoch === this.epoch && !this.disposed)
          this.publish({ connected: false, error: `Could not reach DeckyEQ. ${text(error)}` });
      }
    })();
    this.reading = request;
    try { await request; } finally { if (this.reading === request) this.reading = null; }
  };

  edit = (eq: EQ, boost?: number) => {
    if (!this.view.snapshot || !this.view.connected || this.view.busy || this.disposed) return;
    this.epoch++;
    this.desired = cloneEQ(eq);
    if (boost !== undefined) this.desiredBoost = boost;
    const snapshot = this.optimistic(this.view.snapshot, eq, this.desiredBoost);
    this.publish({ snapshot, saving: true, error: null, note: null });
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; void this.flush(); }, EDIT_DEBOUNCE_MS);
  };

  private optimistic(snapshot: Snapshot, eq: EQ, boost: number | null = null): Snapshot {
    const preset = snapshot.presets.find(p => p.id === snapshot.state.selected);
    return { ...snapshot, state: { ...snapshot.state, working: cloneEQ(eq), ...(boost === null ? {} : { boost }) },
      dirty: !sameEQ(eq, preset?.eq),
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
      const eq = this.desired, boost = this.desiredBoost;
      this.desired = this.desiredBoost = null;
      try {
        const result = await this.transport.perform("edit", boost === null ? { eq } : { eq, boost },
          this.view.snapshot.revision);
        if (!result.ok || !result.state) {
          this.desired = this.desiredBoost = null;
          const error = result.error || "EQ was not applied.";
          this.publish({ snapshot: result.state || this.view.snapshot, error, saving: false });
          if (!this.disposed && !this.view.busy) this.onEditRejected?.(error);  // An action reports it with its own result.
          return;
        }
        this.publish({ snapshot: this.desired ? this.optimistic(result.state, this.desired, this.desiredBoost) : result.state,
          connected: true });
      } catch (error) {
        this.desired = this.desiredBoost = null;
        this.unconfirmed = true;
        this.publish({ error: `Change not confirmed. ${text(error)}`, connected: false, saving: false });
        return;
      }
    }
    this.publish({ saving: false });
  }

  action = async (action: string, payload: Record<string, unknown> = {}): Promise<Result> => {
    if (this.disposed || this.view.busy || !this.view.snapshot || !this.view.connected) {
      const error = "DeckyEQ is busy or disconnected. Try again.";
      if (!this.disposed && !this.view.busy) this.publish({ error });  // e.g. a dialog confirmed after a disconnect
      return { ok: false, error };
    }
    this.epoch++;
    this.publish({ busy: true, error: null, note: null });
    try {
      await this.flush();
      if (this.view.error || !this.view.connected)
        return { ok: false, error: this.view.error || "Connection lost." };
      const result = await this.transport.perform(action, payload, this.view.snapshot!.revision);
      if (result.state) this.publish({ snapshot: result.state });
      if (!result.ok) this.publish({ error: result.error || "The operation failed." });
      else this.publish({ note: result.path ? `Exported to ${fromHome(result.path, this.view.snapshot!.home)}` : result.message || null });
      return result;
    } catch (error) {
      this.unconfirmed = true;  // The next successful poll must still say the action was not confirmed.
      this.publish({ error: text(error), connected: false });
      return { ok: false, error: text(error) };
    } finally { this.publish({ busy: false }); }
  };

  dispose() {
    this.disposed = true;
    this.epoch++;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.desired = this.desiredBoost = null;
    this.listeners.clear();
  }
}
