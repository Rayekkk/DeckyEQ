import { ComponentProps, ReactElement, ReactNode, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ButtonItem, ConfirmModal, DialogBody, DialogButton, DialogFooter, DialogHeader, DropdownItem, Field, Focusable,
  ModalRoot, PanelSection, PanelSectionRow, showModal, ShowModalProps, SliderField, TextField, ToggleField } from "@decky/ui";
import { Controller } from "./controller";
import { CurveGraph } from "./editor";
import { bandHz, pairs, setFiveBands, shown, useFiveBands, withBand, withPairGain } from "./pairs";
import { Band, cloneEQ, db, EQ, formatHz, limits, Output, Preset, Snapshot } from "./types";
import { UpdateController, UpdateSection } from "./updates";

const POLL_MS = 1800;  // Status refresh while the panel is visible.
const phases: Record<string, string> = { active: "Live", standby: "Standby", off: "EQ off", bypassed: "Bypassed",
  waiting: "Waiting for output", connecting: "Connecting", error: "Paused", unavailable: "Unavailable" };
/** The same names in every preset list. */
const presetOptions = (presets: Preset[]) => presets.map(p => ({ data: p.id, label: p.name }));
/** The backend's refusals that a different name fixes (deckyeq/model.py name(), Service._do_rename). */
const nameError = (error: string) => /\bnames?\b/i.test(error);

// Steam's default ButtonItem uses an inline, fixed-width field layout.
// Standalone QAM actions must occupy the available row instead.
const ActionButton = (props: ComponentProps<typeof ButtonItem>) =>
  <ButtonItem {...props} layout="below" childrenContainerWidth="max" />;

/** A read-only row; `focusable` keeps it reachable with the D-pad. */
const infoRow = (label: ReactNode, description?: ReactNode) =>
  <PanelSectionRow><Field focusable label={label} description={description} /></PanelSectionRow>;

export interface FileUI { importFile(home: string): Promise<string | null>; exportFolder(home: string): Promise<string | null> }
const styles = `
.deq { --deq-accent: #73e3c0; color: #eef2f7; padding-bottom: 18px; width:100%; max-width:100%; min-width:0; box-sizing:border-box; overflow-x:hidden; }
.deq .deq-card, .deq .deq-note { box-sizing:border-box; }  /* Never Steam's own fields: it would narrow their rows. */
.deq button, .deq [role="button"], .deq .DialogButton { min-width:0 !important; max-width:100%; white-space:normal; overflow-wrap:anywhere; }
.deq .deq-card { margin: 0 16px 12px; padding: 14px; border: 1px solid #ffffff14; border-radius: 12px; background: #111923; }
.deq .deq-status { display:flex; align-items:center; gap:8px; font-size:12px; text-transform:uppercase; letter-spacing:1.2px; color:#aebccc; }
.deq .deq-dot { width:7px; height:7px; border-radius:50%; background:#7f8c9b; }
.deq .deq-dot.live { background:var(--deq-accent); box-shadow:0 0 12px #73e3c044; }
.deq .deq-name { margin-top:8px; font-size:20px; font-weight:700; overflow-wrap:anywhere; }
.deq .deq-small { font-size:12px; color:#9daebe; line-height:1.5; overflow-wrap:anywhere; }
.deq .deq-note { margin:0 16px 12px; padding:10px 12px; border-radius:8px; background:#26382f; font-size:12px; line-height:1.5; overflow-wrap:anywhere; }
.deq .deq-error { background:#472f2e; color:#ffd2cc; }
.deq .deq-value { color:var(--deq-accent); font-variant-numeric:tabular-nums; }
`;
// Steam's dialog at full size, as Companion's fan curve editor sizes it.
const fullStyles = `
.deq-full { width:min(1120px,94vw)!important; max-width:94vw!important; height:100%!important; max-height:900px!important; min-height:0!important;
  display:flex!important; flex-direction:column!important; flex:1 1 auto!important; overflow:hidden!important; box-sizing:border-box!important; padding:20px!important; }
.deq-full-layer { padding:16px!important; overflow:hidden!important; align-items:center!important; justify-content:center!important; }
.deq-full button { min-height:40px; }
.deq-full * { box-sizing:border-box; }
`;

export function EqualizerIcon() {
  return <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
    <path d="M5 3v18M12 3v18M19 3v18" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    <rect x="2.5" y="6" width="5" height="4" rx="1" fill="currentColor" />
    <rect x="9.5" y="13" width="5" height="4" rx="1" fill="currentColor" />
    <rect x="16.5" y="8" width="5" height="4" rx="1" fill="currentColor" />
  </svg>;
}

type Section = "bands" | "presets" | "output" | "about";
const titles: Record<Section, string> = { bands: "Equalizer", presets: "Presets", output: "Output", about: "About" };

// Field supplies Steam's label, spacing, focus highlight and activation; the icons inherit its color.
const iconPaths: Record<Section, ReactNode> = {
  bands: <path d="M4 21v-7m0-4V3m8 18v-9m0-4V3m8 18v-5m0-4V3M1 14h6m2-6h6m2 8h6" />,
  presets: <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />,
  output: <><path d="M11 5 6 9H2v6h4l5 4V5z" /><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14" /></>,
  about: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10h.01" /></>,
};
const Icon = ({ children }: { children: ReactNode }) =>
  <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
    strokeLinecap="round" strokeLinejoin="round" style={{ display: "block", width: "1.2em", height: "1.2em", flexShrink: 0 }}>
    {children}
  </svg>;
const Chevron = () =>
  <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
    style={{ display: "block", width: "1em", height: "1em", flexShrink: 0 }}><path d="m9 18 6-6-6-6" /></svg>;

/** A row that opens a page: icon, title, a one-line summary of its current state, chevron. */
function SectionLink({ title, description, icon, focus, onOpen }:
  { title: string; description: string; icon?: ReactNode; focus?: boolean; onOpen(): void }) {
  const ref = useRef<HTMLDivElement>(null);
  // Returning from a page puts the D-pad back on its row once; polls never move focus.
  useEffect(() => { if (focus) ref.current?.focus(); }, []);
  return <PanelSectionRow>
    <Field ref={ref} label={title} description={description} icon={icon && <Icon>{icon}</Icon>}
      bottomSeparator="standard" verticalAlignment="center" childrenLayout="inline" childrenContainerWidth="min"
      focusable highlightOnFocus onActivate={onOpen}><Chevron /></Field>
  </PanelSectionRow>;
}

function SectionHeader({ title, back, onBack, focus }: { title: string; back: string; onBack(): void; focus: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  // One focus handoff on page entry, never on a poll or dropdown dismissal.
  useEffect(() => { if (focus) ref.current?.querySelector<HTMLButtonElement>("button")?.focus(); }, []);
  return <PanelSection title={title}>
    <PanelSectionRow><div ref={ref}><ActionButton onClick={onBack}>‹ {back}</ActionButton></div></PanelSectionRow>
  </PanelSection>;
}

/** A page's last rows: "Show details" and, once opened, the technical facts and rare repair actions. */
function Details({ rows, children, label = "details" }: { rows: [ReactNode, ReactNode][]; children?: ReactNode; label?: string }) {
  const [shown, setShown] = useState(false);
  return <>
    <PanelSectionRow><ActionButton onClick={() => setShown(!shown)}>{shown ? `Hide ${label}` : `Show ${label}`}</ActionButton></PanelSectionRow>
    {shown && children}
    {shown && rows.map(([label, description], i) => <div key={i}>{infoRow(label, description)}</div>)}
  </>;
}

/** Asks for a preset name; the field opens Steam's keyboard and keeps names within the backend limit. */
function NameModal({ title, initial, ok, error, onSubmit, closeModal }:
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
type Run = (action: string, payload?: Record<string, unknown>, toggle?: Redraw) => void;
/** Steam's toggles and dropdowns keep showing what was picked until they are redrawn from the state. */
type Toggle = "enable" | "own" | "speakers" | "preset" | "output";
type Redraw = Toggle | (() => void);

/** What every page needs from the panel: the confirmed state and how to change it. */
interface Page {
  controller: Controller; snapshot: Snapshot; preset: Preset; locked: boolean; run: Run;
  /** Shows a failure where the user can see it while a dialog hides the QAM. */
  notify(text: string): void;
  change(patch: Partial<EQ>): void;
  bandChange(index: number, patch: Partial<Band>): void;
  confirm(heading: string, description: string, action: () => void, ok?: string, destructive?: boolean, toggle?: Redraw): void;
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

function GainSlider({ label, value, onChange, disabled, range = limits.band_gain as [number, number] }:
  { label: string; value: number; onChange(value: number): void; disabled: boolean; range?: [number, number] }) {
  return <PanelSectionRow><SliderField label={label} value={value} min={range[0]} max={range[1]} step={0.5}
    resetValue={0} showValue valueSuffix=" dB" disabled={disabled} onChange={onChange} /></PanelSectionRow>;
}

/** What Volume boost does now; it is global, so it says so before it is moved. */
const boostNote = ({ boost, enabled, speakers_only }: Snapshot["state"]) =>
  !enabled ? "For every preset and game, once EQ is on."
    : `For every preset and game.${boost > 0 && !speakers_only ? " Mind your hearing on headphones." : ""}`;


function MainPage({ page, open, focus }: { page: Page; open(section: Section): void; focus: Section | null }) {
  const { snapshot: { state, audio, presets, dirty, version, game }, locked, change, select, controller, confirm } = page;
  const own = !!game && state.scope === game.appid;
  const saved = !!game && game.appid in state.games;
  const eq = state.working;
  const adjusted = eq.bands.filter((_, i) => shown(eq, i).gain !== 0).length;
  const output = audio.outputs.find(o => o.name === state.target)?.description || (state.target ? "Disconnected output" : "No output chosen");
  const level = state.enabled && Math.abs(audio.effective_preamp) > 0.05 ? ` · level ${db(audio.effective_preamp)}` : "";
  const games = Object.keys(state.games).length;
  return <>
    <PanelSection>
      <PanelSectionRow><ToggleField key={page.resets.enable} label="Enable EQ" checked={state.enabled}
        disabled={locked || (!audio.supported && !state.enabled)} onChange={enabled => page.run("enable", { enabled }, "enable")} /></PanelSectionRow>
      <PanelSectionRow><SliderField label="Volume boost" value={state.boost} min={limits.boost[0]} max={limits.boost[1]}
        step={0.5} resetValue={0} showValue valueSuffix=" dB" disabled={locked}
        description={boostNote(state)}
        onChange={boost => controller.edit(cloneEQ(eq), boost)} /></PanelSectionRow>
      {game && <PanelSectionRow><ToggleField key={page.resets.own} label={`Own sound for ${game.name}`} checked={own} disabled={locked}
        description={own ? "Sound changes apply to this game only. The preset library is shared."
          : saved ? "Its saved sound could not be applied. Turn on to try again." : "This game uses your everyday sound."}
        onChange={enabled => enabled ? page.run("own_sound", { enabled, appid: game.appid }, "own")
          : confirm(`Forget the sound for ${game.name}?`, "The game will use your everyday sound.",
            () => page.run("own_sound", { enabled, appid: game.appid }, "own"), "Forget", true, "own")} /></PanelSectionRow>}
      <PanelSectionRow><DropdownItem key={page.resets.preset} label="Preset" layout="below" childrenContainerWidth="max"
        rgOptions={presetOptions(presets)}
        selectedOption={state.selected} disabled={locked} onChange={option => select(option.data)} /></PanelSectionRow>
      <PanelSectionRow>{page.graph}</PanelSectionRow>
    </PanelSection>
    <PanelSection title="Tone">
      <GainSlider label={`Bass · below ${formatHz(limits.bass_frequency)}`} value={eq.bass} disabled={locked}
        range={limits.tone_gain as [number, number]} onChange={bass => change({ bass })} />
      <GainSlider label={`Treble · above ${formatHz(limits.treble_frequency)}`} value={eq.treble} disabled={locked}
        range={limits.tone_gain as [number, number]} onChange={treble => change({ treble })} />
      <PanelSectionRow><SliderField label="Stereo width" value={Math.round(eq.width * 100)}
        min={limits.width[0] * 100} max={limits.width[1] * 100} step={5} resetValue={100} showValue valueSuffix="%"
        description={eq.width < 1 ? "0% is mono." : undefined}
        disabled={locked} onChange={percent => change({ width: percent / 100 })} /></PanelSectionRow>
    </PanelSection>
    {/* Below the sliders that make the sound edited: appearing mid-drag must not move the slider being dragged. */}
    {dirty && <PanelSection title="Unsaved changes">
      <PanelSectionRow><ActionButton disabled={locked} onClick={page.save}>{page.preset.factory ? "Save as new…" : "Save"}</ActionButton></PanelSectionRow>
      <PanelSectionRow><ActionButton disabled={locked} onClick={page.discard}>Discard</ActionButton></PanelSectionRow>
    </PanelSection>}
    <PanelSection title="More">
      <SectionLink title={titles.bands} icon={iconPaths.bands} focus={focus === "bands"} onOpen={() => open("bands")}
        description={`${adjusted ? `${adjusted} ${adjusted === 1 ? "band" : "bands"} adjusted` : "No bands adjusted"}${level}`} />
      <SectionLink title={titles.presets} icon={iconPaths.presets} focus={focus === "presets"} onOpen={() => open("presets")}
        description={`${presets.length} presets${games ? ` · ${games} game ${games === 1 ? "sound" : "sounds"}` : ""}`} />
      <SectionLink title={titles.output} icon={iconPaths.output} focus={focus === "output"} onOpen={() => open("output")}
        description={output} />
      <SectionLink title={titles.about} icon={iconPaths.about} focus={focus === "about"} onOpen={() => open("about")}
        description={`Version ${version} · updates`} />
    </PanelSection>
  </>;
}

function EqualizerPage({ page }: { page: Page }) {
  const { snapshot, locked, bandChange, confirm, run, controller, change, five } = page;
  const eq = snapshot.state.working, compensation = snapshot.audio.compensation;
  return <>
    <PanelSection>
      <PanelSectionRow><ActionButton disabled={locked} onClick={page.openFull}>Edit full screen</ActionButton></PanelSectionRow>
    </PanelSection>
    <PanelSection>
      {/* Always ten bands (or five pairs): a placeholder that does nothing shows as a flat band at its usual frequency. */}
      {five ? pairs(eq).map((pair, p) => <GainSlider key={`pair${p}`} label={pair.label}
        value={pair.gain} disabled={locked} onChange={gain => controller.edit(withPairGain(eq, p, gain))} />)
        : eq.bands.map((_, i) => <GainSlider key={i} label={formatHz(bandHz(eq, i))} value={shown(eq, i).gain} disabled={locked}
          onChange={gain => bandChange(i, { gain })} />)}
      <PanelSectionRow><SliderField label="Input level" value={eq.preamp} min={limits.preamp[0]} max={limits.preamp[1]}
        step={0.5} resetValue={0} showValue valueSuffix=" dB" disabled={locked}
        description={compensation < -0.05
          ? `Lowered by ${db(-compensation).slice(1)} to make room for big EQ boosts.`
          : undefined}
        onChange={preamp => change({ preamp })} /></PanelSectionRow>
      <PanelSectionRow><ToggleField label="Five sliders" checked={five} onChange={setFiveBands}
        description="Each slider moves two bands." /></PanelSectionRow>
    </PanelSection>
    <PanelSection>
      <PanelSectionRow><ActionButton disabled={locked} onClick={() => confirm("Reset to flat?",
        `Bands, bass, treble, stereo width and input level return to their defaults. Volume boost stays.${
          snapshot.state.scope ? " Only this game's sound is reset." : ""}`,
        () => run("reset"), "Reset")}>Reset to flat</ActionButton></PanelSectionRow>
    </PanelSection>
  </>;
}

function PresetsPage({ page, pick }: { page: Page; pick(kind: "import" | "export"): void }) {
  const { snapshot, preset, locked, confirm, select, run, named } = page;
  return <>
    <PanelSection>
      <PanelSectionRow><DropdownItem key={page.resets.preset} label="Preset" layout="below" childrenContainerWidth="max"
        rgOptions={presetOptions(snapshot.presets)}
        selectedOption={preset.id} disabled={locked} onChange={option => select(option.data)} /></PanelSectionRow>
    </PanelSection>
    <PanelSection title={`${preset.name}${snapshot.dirty ? " · edited" : ""}`}>
      {preset.description && infoRow("About this preset", preset.description)}
      {snapshot.dirty && !preset.factory && <PanelSectionRow><ActionButton disabled={locked}
        onClick={() => run("save")}>Save</ActionButton></PanelSectionRow>}
      {snapshot.dirty && <PanelSectionRow><ActionButton disabled={locked}
        onClick={page.discard}>Discard</ActionButton></PanelSectionRow>}
      <PanelSectionRow><ActionButton disabled={locked}
        onClick={() => named("Save as new preset", "Save", "save_as", preset.name)}>Save as new…</ActionButton></PanelSectionRow>
      {!preset.factory && <>
        <PanelSectionRow><ActionButton disabled={locked}
          onClick={() => named("Rename preset", "Rename", "rename", preset.name)}>Rename…</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked}
          onClick={() => confirm("Delete preset?", `Delete “${preset.name}” and switch to Flat?`,
            () => run("delete", { id: preset.id }), "Delete", true)}>Delete</ActionButton></PanelSectionRow>
      </>}
      {preset.factory && snapshot.dirty && infoRow("Built-in preset", "Save as new to keep your changes.")}
    </PanelSection>
    <PanelSection title="Files">
      <Details label="import or export" rows={[["Export folder", snapshot.export_directory]]}>
        <PanelSectionRow><ActionButton disabled={locked} onClick={() => pick("import")}>Import presets…</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked}
          onClick={() => run("export", { all: false })}>Export this sound</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked}
          onClick={() => run("export", { all: true })}>Export your presets</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked} onClick={() => pick("export")}>Export this sound to…</ActionButton></PanelSectionRow>
      </Details>
    </PanelSection>
    {Object.keys(snapshot.state.games).length > 0 && <PanelSection title="Games with their own sound">
      {Object.entries(snapshot.state.games).map(([appid, game]) => <PanelSectionRow key={appid}><ActionButton disabled={locked}
        onClick={() => confirm(`Forget the sound for ${game.name}?`, "The game will use your everyday sound.",
          () => run("forget_game", { appid }), "Forget", true)}>Forget {game.name}</ActionButton></PanelSectionRow>)}
    </PanelSection>}
  </>;
}

/** The description, plus what tells it apart (the active port, else the unique name) when two outputs share it. */
const outputLabel = (output: Output, all: Output[]) => {
  const twins = all.filter(o => o.description === output.description);
  if (twins.length < 2) return output.description;
  const portTells = !!output.port && new Set(twins.map(o => o.port)).size === twins.length;
  return `${output.description} (${portTells ? output.port : output.name})`;
};

function OutputPage({ page }: { page: Page }) {
  const { run, snapshot: { state, audio }, locked } = page;
  const disconnected = state.target && !audio.outputs.some(o => o.name === state.target)
    ? [{ data: state.target, label: "Selected output · disconnected" }] : [];
  const steam = audio.outputs.find(o => o.name === audio.default)?.description || audio.default || "Unknown";
  return <>
    <PanelSection>
      <PanelSectionRow><DropdownItem key={page.resets.output} label="Apply EQ to" layout="below" childrenContainerWidth="max" selectedOption={state.target} disabled={locked}
        strDefaultLabel="Choose an output"
        rgOptions={[...audio.outputs.map(output => ({ data: output.name, label: outputLabel(output, audio.outputs) })), ...disconnected]}
        onChange={option => run("output", { target: option.data, speakers_only: state.speakers_only }, "output")} /></PanelSectionRow>
      <PanelSectionRow><ToggleField key={page.resets.speakers} label="Speakers only" checked={state.speakers_only} disabled={locked}
        description="No EQ on headphones, HDMI or Bluetooth."
        onChange={speakers_only => run("output", { target: state.target, speakers_only }, "speakers")} /></PanelSectionRow>
      <Details rows={[
        ["Steam output", steam],
        ...(audio.game_error ? [["Game sound", audio.game_error] as [string, string]] : []),
        ["Volume", "Steam's volume, volume buttons and output choice work as usual."],
      ]}>
        <PanelSectionRow><ActionButton disabled={locked || !audio.supported}
          onClick={() => run("refresh")}>Reconnect EQ</ActionButton></PanelSectionRow>
      </Details>
    </PanelSection>
  </>;
}

/** The curve at full size in a Steam dialog; `page` is the panel's latest, so its actions see the sound shown now. */
function FullEditor({ controller, page, closeModal }: { controller: Controller; page(): Page; closeModal?(): void }) {
  const view = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const five = useFiveBands();
  const [band, setBand] = useState(0);
  const [redraw, setRedraw] = useState(0);  // Like the panel's resets: this dialog is not redrawn with the panel.
  useEffect(() => {  // The QAM's polling pauses while it is hidden: the status line here stays current on its own.
    void controller.refresh();
    const timer = setInterval(() => void controller.refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, []);
  const snapshot = view.snapshot;
  if (!snapshot) return null;
  const { state, presets, dirty, audio } = snapshot;
  const preset = presets.find(p => p.id === state.selected) ?? presets[0];
  const locked = view.busy || !view.connected;
  return <ModalRoot bAllowFullSize className="deq-full" modalClassName="deq-full-layer" onCancel={closeModal} closeModal={closeModal}>
    <style>{fullStyles}</style>
    <DialogHeader>Curve editor</DialogHeader>
    <DialogBody style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 8, margin: 0, padding: 0 }}>
      <Focusable flow-children="row" style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)", gap: 16, alignItems: "end" }}>
        <DropdownItem key={redraw} label="Preset" rgOptions={presetOptions(presets)} selectedOption={state.selected}
          disabled={locked} onChange={option => page().select(option.data, () => setRedraw(n => n + 1))} />
        <ToggleField label="Five sliders" checked={five} onChange={setFiveBands} />
      </Focusable>
      <div style={{ flex: "1 0 260px", minHeight: 260 }}>
        <CurveGraph fullscreen eq={state.working} five={five} disabled={locked} selected={band} select={setBand} edit={controller.edit} />
      </div>
      <div role={view.error ? "alert" : "status"} style={{ fontSize: 14, color: view.error ? "#ffd2cc" : "#afb7c0" }}>
        {dirty ? "Edited · " : ""}{view.error || (view.saving ? "Applying…" : audio.message)}</div>
    </DialogBody>
    <DialogFooter style={{ margin: 0, padding: 0, flexShrink: 0 }}>
      <Focusable flow-children="row" style={{ display: "flex", gap: 12, width: "100%" }}>
        <DialogButton style={{ flex: 1 }} disabled={locked || !dirty} onClick={() => page().save()}>{preset.factory ? "Save as new…" : "Save"}</DialogButton>
        <DialogButton style={{ flex: 1 }} disabled={locked || !dirty} onClick={() => page().discard()}>Discard</DialogButton>
        <DialogButton style={{ flex: 1 }} onClick={closeModal}>Done</DialogButton>
      </Focusable>
    </DialogFooter>
  </ModalRoot>;
}

function AboutPage({ version, updates, home }: { version: string; updates?: UpdateController; home: string }) {
  return <>
    {updates && <UpdateSection controller={updates} home={home} />}
    <PanelSection title="DeckyEQ">
      {infoRow(`Version ${version}`)}
      {infoRow("Author", "Rayek · github.com/Rayekkk/DeckyEQ")}
      {infoRow("License", "BSD 3-Clause · Copyright (c) 2026 Rayekkk. Dependency notices are included in the plugin package.")}
      {infoRow("Powered by PipeWire", "PipeWire filters and DeckyEQ's own limiter · no extra audio packages.")}
    </PanelSection>
  </>;
}

export function App({ controller, files, updates, visible = true, toast }: {
  controller: Controller; files: FileUI; updates?: UpdateController; visible?: boolean;
  /** Shows a short outcome (e.g. where a file was exported); without it the note stays in the panel. */
  toast?(body: string): void;
}) {
  const view = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const five = useFiveBands();
  const [section, setSection] = useState<Section | null>(null);
  const [left, setLeft] = useState<Section | null>(null);  // The page just closed, whose row takes focus again.
  const [graphBand, setGraphBand] = useState(0);
  const latest = useRef<Page | null>(null);
  const modals = useRef(new Set<{ Close(): void }>());
  useEffect(() => () => modals.current.forEach(modal => modal.Close()), []);  // An unloaded plugin leaves no dialog open.
  const show = (element: ReactElement, props: ShowModalProps = {}) => {
    const modal = showModal(element, undefined, { ...props, fnOnClose: () => { modals.current.delete(modal); } });
    modals.current.add(modal);
  };
  const [picking, setPicking] = useState(false);
  const [resets, setResets] = useState<Record<Toggle, number>>({ enable: 0, own: 0, speakers: 0, preset: 0, output: 0 });
  const reset = (toggle?: Redraw) => {
    if (typeof toggle === "function") toggle();
    else if (toggle) setResets(counts => ({ ...counts, [toggle]: counts[toggle] + 1 }));
  };
  const snapshot = view.snapshot;
  const locked = view.busy || !view.connected || picking;
  useEffect(() => {
    if (!visible) return;
    controller.clearMessage();  // A message from an earlier visit is stale; a current problem shows again.
    void controller.refresh();
    const timer = setInterval(() => void controller.refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [controller, visible]);
  useEffect(() => {
    controller.onEditRejected = toast;
    return () => { controller.onEditRejected = undefined; };
  }, [controller, toast]);
  useEffect(() => {
    if (!toast || !view.note) return;
    toast(view.note);
    controller.clearMessage();  // The note lives in the controller; a remount must not toast it again.
  }, [toast, view.note]);

  if (!snapshot) return <div className="deq"><style>{styles}</style>
    <PanelSection title="DeckyEQ"><PanelSectionRow>
      <Field focusable label={view.error ? "Could not load DeckyEQ" : "Loading DeckyEQ…"} description={view.error || undefined} />
      {view.error && <ActionButton onClick={() => void controller.refresh()}>Reconnect</ActionButton>}
    </PanelSectionRow></PanelSection>
    {view.error && updates && <UpdateSection controller={updates} />}
  </div>;

  const { state, audio } = snapshot;
  const preset = snapshot.presets.find(p => p.id === state.selected) ?? snapshot.presets[0];
  const navigate = (next: Section | null) => {
    controller.clearMessage();  // Messages belong to the page that caused them.
    setLeft(section);
    setSection(next);
  };
  const confirm = (heading: string, description: string, action: () => void, ok = "Continue", destructive = false, toggle?: Redraw) => {
    show(<ConfirmModal strTitle={heading} strDescription={description} bDestructiveWarning={destructive}
      strOKButtonText={ok} strCancelButtonText="Cancel" onOK={action} onCancel={() => reset(toggle)} />);
  };
  const notify = (text: string) => toast?.(text);
  // Carries the sound this render showed: a dialog confirmed after a game switched it is refused.
  const runIn = (scope: string): Run => (action, payload, toggle) => void controller.action(action, { ...payload, scope }).then(result => {
    if (!result.ok) { notify(result.error || "The operation failed."); reset(toggle); }
  });
  const run = runIn(state.scope);
  // A dialog hides the QAM: a refused name reopens it with what was typed and the reason.
  const named = (title: string, ok: string, action: "save_as" | "rename", initial = "", error?: string) => {
    show(<NameModal title={title} ok={ok} initial={initial} error={error} onSubmit={async name => {
      // Like run: the sound this render showed; a game that switched it meanwhile gets the save refused.
      const result = await controller.action(action, action === "rename" ? { name, id: preset.id } : { name, scope: state.scope });
      if (result.ok) return;
      const reason = result.error || "The operation failed.";
      if (nameError(reason)) named(title, ok, action, name, reason);
      else notify(reason);  // e.g. a full library or a switched sound: another name would not fix it.
    }} />);
  };
  const page: Page = {
    controller, snapshot, preset, locked, confirm, run, notify, resets, five, named,
    change: patch => controller.edit({ ...cloneEQ(state.working), ...patch }),
    bandChange: (index, patch) => controller.edit(withBand(state.working, index, patch)),
    select: (id, redraw = () => reset("preset")) => {
      if (id === state.selected) return;
      const apply = () => run("select", { id }, redraw);
      if (snapshot.dirty) confirm("Switch preset?", `Unsaved changes to “${preset.name}” will be lost.`, apply, "Switch", false, redraw);
      else apply();
    },
    save: () => preset.factory ? named("Save as new preset", "Save", "save_as", preset.name) : run("save"),
    discard: () => confirm("Discard changes?", `Restore the saved version of “${preset.name}”.`, () => run("revert", { id: preset.id }), "Discard"),
    graph: <CurveGraph eq={state.working} five={five} disabled={locked} selected={graphBand} select={setGraphBand} edit={controller.edit} />,
    openFull: () => show(<FullEditor controller={controller} page={() => latest.current!} />,
      { strTitle: "Curve editor", bNeverPopOut: true }),
  };
  latest.current = page;
  const pick = async (kind: "import" | "export") => {
    if (locked) return;
    setPicking(true);
    try {
      controller.clearMessage();
      await controller.flush();
      if (controller.getSnapshot().error) return;  // Keep a failed edit visible instead of exporting around it.
      // The picker can stay open while a game switches the sound: send the scope of the moment it closes.
      const sendNow = () => runIn(controller.getSnapshot().snapshot?.state.scope ?? state.scope);
      if (kind === "import") {
        const path = await files.importFile(snapshot.home);
        if (path) sendNow()("import", { path });
      } else {
        const directory = await files.exportFolder(snapshot.home);
        if (directory) sendNow()("export", { directory, all: false });
      }
    } catch (error) { controller.reportError(error); }
    finally { setPicking(false); }
  };

  const messages = <>
    {view.error && <div className="deq-note deq-error" role="alert">{view.error}</div>}
    {audio.error && audio.error !== view.error && <div className="deq-note deq-error" role="alert">{audio.error}</div>}
    {snapshot.warning && <div className="deq-note deq-error" role="alert">{snapshot.warning}</div>}
    {view.note && !toast && <div className="deq-note" role="status">{view.note}</div>}
  </>;

  if (!section) return <div className="deq"><style>{styles}</style>
    <div className="deq-card">
      <div className="deq-status"><span className={`deq-dot ${audio.phase === "active" && view.connected ? "live" : ""}`} />
        {!view.connected ? "Disconnected" : view.saving || view.busy ? "Applying…" : phases[audio.phase] ?? audio.phase}
      </div>
      <div className="deq-name">{preset.name}{snapshot.dirty ? " · edited" : ""}</div>
      <div className="deq-small">{audio.message}</div>
    </div>
    {messages}
    {(!view.connected || audio.error) && <PanelSection><PanelSectionRow>
      <ActionButton disabled={view.busy} onClick={() => view.connected ? run("refresh") : void controller.refresh()}>
        {view.connected ? "Reconnect EQ" : "Reconnect"}</ActionButton></PanelSectionRow></PanelSection>}
    <MainPage page={page} open={navigate} focus={visible ? left : null} />
  </div>;

  return <Focusable className="deq" onCancelActionDescription="Back" onCancelButton={event => {
    event.preventDefault();
    event.stopPropagation();  // B goes back one page instead of leaving the plugin.
    navigate(null);
  }}><style>{styles}</style>
    <SectionHeader key={section} title={titles[section]} back="DeckyEQ" onBack={() => navigate(null)} focus={visible} />
    {messages}
    {section !== "about" && audio.phase !== "active" && <div className="deq-note" role="status">{audio.message}</div>}
    {section === "bands" && <EqualizerPage page={page} />}
    {section === "presets" && <PresetsPage page={page} pick={kind => void pick(kind)} />}
    {section === "output" && <OutputPage page={page} />}
    {section === "about" && <AboutPage version={snapshot.version} updates={updates} home={snapshot.home} />}
  </Focusable>;
}
