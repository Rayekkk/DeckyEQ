// The panel's own building blocks shared by its pages (App.tsx): styles, icons, dialogs and the Page contract.
// Rows, links, headers and toasts every plugin shares are in ui.tsx (DeckyKit).
import { ReactNode, useState } from "react";
import { ConfirmModal, PanelSectionRow, SliderField, TextField } from "@decky/ui";
import { Controller } from "./controller";
import { colors } from "./ui";
import { Band, EQ, limits, Preset, Snapshot } from "./types";

/** DeckyEQ keeps its own green accent (owner, 2026-10-29); every other color is the shared palette. */
export const ACCENT = "#73e3c0";
export const POLL_MS = 1800;  // Status refresh while the panel is visible.
export const phases: Record<string, string> = { active: "Live", standby: "Standby", off: "EQ off", bypassed: "Bypassed",
  waiting: "Waiting for output", connecting: "Connecting", error: "Paused", unavailable: "Unavailable" };
/** The same names in every preset list. */
export const presetOptions = (presets: Preset[]) => presets.map(p => ({ data: p.id, label: p.name }));
/** The backend's refusals that a different name fixes (deckyeq/model.py name(), Service._do_rename). */
export const nameError = (error: string) => /\bnames?\b/i.test(error);

export interface FileUI { importFile(home: string): Promise<string | null>; exportFolder(home: string): Promise<string | null> }
export const styles = `
.deq { color: #eef2f7; padding-bottom: 18px; width:100%; max-width:100%; min-width:0; box-sizing:border-box; overflow-x:hidden; }
.deq .deq-card, .deq .deq-note { box-sizing:border-box; }  /* Never Steam's own fields: it would narrow their rows. */
.deq button, .deq [role="button"], .deq .DialogButton { min-width:0 !important; max-width:100%; white-space:normal; overflow-wrap:anywhere; }
.deq .deq-card { margin: 0 16px 12px; padding: 14px; border: 1px solid ${colors.curve.border}; border-radius: 12px; background: ${colors.surface}; }
.deq .deq-status { display:flex; align-items:center; gap:8px; font-size:12px; text-transform:uppercase; letter-spacing:1.2px; color:${colors.muted}; }
.deq .deq-dot { width:7px; height:7px; border-radius:50%; background:#7f8c9b; }
.deq .deq-dot.live { background:${ACCENT}; box-shadow:0 0 12px ${ACCENT}44; }
.deq .deq-name { margin-top:8px; font-size:20px; font-weight:700; overflow-wrap:anywhere; }
.deq .deq-small { font-size:12px; color:${colors.muted}; line-height:1.5; overflow-wrap:anywhere; }
.deq .deq-note { margin:0 16px 12px; padding:10px 12px; border-radius:8px; background:${colors.okSurface}; font-size:12px; line-height:1.5; overflow-wrap:anywhere; }
.deq .deq-error { background:${colors.errorSurface}; color:${colors.error}; }
`;

export function EqualizerIcon() {
  return <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
    <path d="M5 3v18M12 3v18M19 3v18" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    <rect x="2.5" y="6" width="5" height="4" rx="1" fill="currentColor" />
    <rect x="9.5" y="13" width="5" height="4" rx="1" fill="currentColor" />
    <rect x="16.5" y="8" width="5" height="4" rx="1" fill="currentColor" />
  </svg>;
}

export type Section = "bands" | "presets" | "output" | "about";
export const titles: Record<Section, string> = { bands: "Equalizer", presets: "Presets", output: "Output", about: "About" };

// Field supplies Steam's label, spacing, focus highlight and activation; the icons inherit its color.
export const iconPaths: Record<Section, ReactNode> = {
  bands: <path d="M4 21v-7m0-4V3m8 18v-9m0-4V3m8 18v-5m0-4V3M1 14h6m2-6h6m2 8h6" />,
  presets: <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />,
  output: <><path d="M11 5 6 9H2v6h4l5 4V5z" /><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14" /></>,
  about: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10h.01" /></>,
};

/** Asks for a preset name; the field opens Steam's keyboard and keeps names within the backend limit. */
export function NameModal({ title, initial, ok, error, onSubmit, closeModal }:
  { title: string; initial: string; ok: string; error?: string; onSubmit(name: string): void; closeModal?(): void }) {
  const [value, setValue] = useState(initial);
  return <ConfirmModal strTitle={title} strDescription={error} strOKButtonText={ok} strCancelButtonText="Cancel"
    bOKDisabled={!value.trim()} closeModal={closeModal} onOK={() => onSubmit(value)}>
    <TextField label="Preset name" value={value} focusOnMount
      onChange={event => setValue(Array.from(event.target.value).slice(0, limits.name_length).join(""))} />
  </ConfirmModal>;
}

/** Runs a backend action; a failure is also toasted, as the panel's message may be out of view.
 *  `toggle` names the control to redraw when it is refused (or redraws it). */
export type Run = (action: string, payload?: Record<string, unknown>, toggle?: Redraw) => void;
/** Steam's toggles and dropdowns keep showing what was picked until they are redrawn from the state. */
export type Toggle = "enable" | "own" | "speakers" | "preset" | "output";
export type Redraw = Toggle | (() => void);

/** What every page needs from the panel: the confirmed state and how to change it. */
export interface Page {
  controller: Controller; snapshot: Snapshot; preset: Preset; locked: boolean; run: Run;
  /** Shows a failure where the user can see it while a dialog hides the QAM. */
  notify(text: string): void;
  change(patch: Partial<EQ>): void;
  bandChange(index: number, patch: Partial<Band>): void;
  /** Asks first (UI.md §6): `ok` repeats the verb; `toggle` is redrawn when the dialog is cancelled. */
  confirm(heading: string, description: string, action: () => void, ok: string, destructive?: boolean, toggle?: Redraw): void;
  /** Switches preset; `redraw` puts a dropdown other than the panel's back when the switch is cancelled or refused. */
  select(id: string, redraw?: () => void): void;
  /** Asks for a preset name in a dialog, then saves as new or renames. */
  named(title: string, ok: string, action: "save_as" | "rename", initial?: string): void;
  /** Keeps the unsaved changes: in this preset, or for a built-in one as a new preset. */
  save(): void;
  discard(): void;
  /** Per control, changes when its change was cancelled or refused: Steam's control redraws from the state. */
  resets: Record<Toggle, number>;
  /** Five sliders instead of ten (this device's preference). */
  five: boolean;
  /** The main screen's curve, editable. */
  graph: ReactNode;
  openFull(): void;
}

export function GainSlider({ label, value, onChange, disabled, range = limits.band_gain as [number, number] }:
  { label: string; value: number; onChange(value: number): void; disabled: boolean; range?: [number, number] }) {
  return <PanelSectionRow><SliderField label={label} value={value} min={range[0]} max={range[1]} step={0.5}
    resetValue={0} showValue valueSuffix=" dB" disabled={disabled} onChange={onChange} /></PanelSectionRow>;
}

/** What Volume boost does now; it is global, so it says so before it is moved. */
export const boostNote = ({ boost, enabled, speakers_only }: Snapshot["state"]) =>
  !enabled ? "For every preset and game, once EQ is on."
    : `For every preset and game.${boost > 0 && !speakers_only ? " Mind your hearing on headphones." : ""}`;
