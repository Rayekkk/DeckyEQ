// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026 Rayekkk
// Release flow shared with SpotiDeck and Legion Go 2 Companion.
import { ButtonItem, Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { useSyncExternalStore } from "react";
import { version } from "../package.json";
import { fromHome } from "./controller";

/** dk_update's answers (DeckyKit D2). */
export interface UpdateCheck {
  ok: boolean;
  current_version: string;
  latest_version?: string;
  update_available: boolean;
  download_available: boolean;
  asset_name?: string;
  size?: number;
  checked_at?: number;
  no_release?: boolean;
  /** Why a newer release cannot be downloaded yet (e.g. no verified ZIP), or that none is published. */
  message?: string;
  error?: string;
}

export interface UpdateDownload {
  ok: boolean;
  path?: string;
  version?: string;
  sha256?: string;
  error?: string;
}

export interface UpdateClient {
  check(): Promise<UpdateCheck>;
  download(expectedVersion: string): Promise<UpdateDownload>;
}

interface UpdateState {
  busy: "check" | "download" | null;
  check?: UpdateCheck;
  download?: UpdateDownload;
  error?: string;
}

// A request belongs to the plugin instance, so switching pages or closing QAM
// retains progress. Disposal prevents old requests from reaching a new instance.
export class UpdateController {
  private state: UpdateState = { busy: null };
  private listeners = new Set<() => void>();
  private closed = false;
  constructor(private client: UpdateClient) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  dispose() { this.closed = true; this.listeners.clear(); }
  private publish(state: UpdateState) {
    if (this.closed) return;
    this.state = state;
    this.listeners.forEach(listener => listener());
  }
  async run(kind: "check" | "download") {
    if (this.closed || this.state.busy) return;
    const expected = this.state.check?.latest_version;
    if (kind === "download" && (!this.state.check?.update_available || !this.state.check.download_available || !expected)) return;
    this.publish({ ...this.state, busy: kind, error: undefined });
    try {
      if (kind === "check") {
        const result = await this.client.check();
        if (!result.ok) throw new Error(result.error || "Could not check GitHub releases. Try again.");
        // A successful check may carry a note (e.g. no ZIP yet); statusText shows it, not the error alert.
        this.publish({ ...this.state, check: result, error: undefined,
          download: this.state.download?.version === result.latest_version ? this.state.download : undefined });
      } else {
        const result = await this.client.download(expected!);
        if (!result.ok || !result.path || result.version !== expected) {
          throw new Error(result.error || "Could not confirm the downloaded ZIP. Try again.");
        }
        this.publish({ ...this.state, download: result });
      }
    } catch (error) {
      // A failed re-check must not keep offering the previous result's download.
      this.publish({ ...this.state, check: kind === "check" ? undefined : this.state.check,
        error: error instanceof Error ? error.message : "The update request failed. Try again." });
    } finally {
      this.publish({ ...this.state, busy: null });
    }
  }
}

function statusText(check?: UpdateCheck, failed = false): [string, string] {
  const installed = check?.current_version || version;
  if (!check) return [failed ? "Check failed" : "Not checked yet", `Installed version: ${installed}.`];
  if (check.no_release) return ["No public release yet", `Installed version: ${installed}.`];
  if (check.update_available) {
    if (!check.download_available) return ["New release found", `Version ${check.latest_version}: ${check.message?.replace(/\.$/, "") || "no verified installation ZIP yet"}. Check again later.`];
    const size = check.size ? ` (${(check.size / 1048576).toFixed(1)} MB)` : "";
    return ["Update available", `Version ${check.latest_version}${size}.`];
  }
  return check.latest_version === installed ? ["Up to date", `Version ${installed} is the latest public release.`]
    : ["No newer release", `Installed: ${installed}. Latest public release: ${check.latest_version}.`];
}

/** `home` shortens the ZIP's path to ~/…: the full path names the user. */
export function UpdateSection({ controller, home = "" }: { controller: UpdateController; home?: string }) {
  const view = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const downloaded = !!view.check?.latest_version && view.download?.version === view.check.latest_version;
  // No result with an error: the check itself failed (a failed download keeps the check).
  const [label, description]: [string, string?] = downloaded ? ["Ready to install"] : statusText(view.check, !!view.error);
  const path = view.download?.path, relative = path && fromHome(path, home);
  return <PanelSection title="Updates">
    <PanelSectionRow><Field focusable label={label} description={description} /></PanelSectionRow>
    <PanelSectionRow><ButtonItem layout="below" childrenContainerWidth="max" disabled={view.busy !== null}
      onClick={() => void controller.run("check")}>
      {view.busy === "check" ? "Checking GitHub…" : "Check for updates"}
    </ButtonItem></PanelSectionRow>
    {view.check?.update_available && view.check.download_available && !downloaded && <PanelSectionRow>
      <ButtonItem layout="below" childrenContainerWidth="max" disabled={view.busy !== null}
        onClick={() => void controller.run("download")}>
        {view.busy === "download" ? "Downloading ZIP…" : `Download ${view.check.latest_version}`}
      </ButtonItem>
    </PanelSectionRow>}
    {view.error && <PanelSectionRow><Field focusable label="Could not complete" description={<div role="alert">{view.error}</div>} /></PanelSectionRow>}
    {view.download?.path && <PanelSectionRow><Field focusable label={`Version ${view.download.version} downloaded`}
      description={<><div style={{ overflowWrap: "anywhere" }}>{relative === path ? path : `~/${relative}`}</div><div style={{ marginTop: 6 }}>
        Turn on Developer mode in Decky Settings → General, then Developer → Install Plugin from ZIP.
      </div></>} /></PanelSectionRow>}
  </PanelSection>;
}
