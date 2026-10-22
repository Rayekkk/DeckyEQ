import { ComponentProps, ReactNode, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ButtonItem, ConfirmModal, DropdownItem, Field, Focusable, PanelSection,
  PanelSectionRow, showModal, SliderField, TextField, ToggleField } from "@decky/ui";
import { Controller } from "./controller";
import { Band, cloneEQ, db, EQ, FilterType, formatHz, limits, safeFrequency, Snapshot } from "./types";
import { UpdateController, UpdateSection } from "./updates";

const [freqMin, freqMax] = limits.frequency, [stableMin, stableMax] = limits.stable_frequency;
const POLL_MS = 1800;  // Status refresh while the panel is visible.
const phases: Record<string, string> = { active: "Live", standby: "Standby", off: "EQ off", bypassed: "Bypassed",
  waiting: "Waiting for output", error: "Paused", unavailable: "Unavailable" };
const filterTypes: { data: FilterType; label: string }[] = [
  { data: "peaking", label: "Bell" }, { data: "lowshelf", label: "Low shelf" }, { data: "highshelf", label: "High shelf" },
];

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
.deq .deq-chart { display:block; width:100%; height:auto; margin-top:12px; }
`;

export function EqualizerIcon() {
  return <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
    <path d="M5 3v18M12 3v18M19 3v18" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    <rect x="2.5" y="6" width="5" height="4" rx="1" fill="currentColor" />
    <rect x="9.5" y="13" width="5" height="4" rx="1" fill="currentColor" />
    <rect x="16.5" y="8" width="5" height="4" rx="1" fill="currentColor" />
  </svg>;
}

function BandLevels({ eq }: { eq: EQ }) {
  const slot = 270 / eq.bands.length;  // Bars share the chart width, whatever the band count.
  return <svg className="deq-chart" viewBox="0 0 270 98" role="img" aria-label={`${eq.bands.length} equalizer band gain levels`}>
    <line x1="2" x2="268" y1="40" y2="40" stroke="#ffffff20" />
    <line x1="2" x2="268" y1="10" y2="10" stroke="#ffffff08" />
    <line x1="2" x2="268" y1="70" y2="70" stroke="#ffffff08" />
    {eq.bands.map((band, i) => {
      const y = 40 - band.gain * 2.5;
      return <g key={i}>
        <rect x={i * slot + slot * 0.18} y={Math.min(y, 40)} width={slot * 0.64} height={Math.max(2, Math.abs(y - 40))}
          rx="2" fill={band.gain >= 0 ? "#73e3c0" : "#af9aeb"} />
        <text x={i * slot + slot / 2} y="93" textAnchor="middle" fill="#91a1b2" fontSize="10">
          {band.frequency >= 1000 ? `${Number((band.frequency / 1000).toFixed(1))}k` : Number(band.frequency.toFixed(1))}
        </text>
      </g>;
    })}
  </svg>;
}

type Section = "bands" | "band" | "presets" | "output" | "about";
const titles: Record<Section, string> = { bands: "Equalizer bands", band: "Edit one band", presets: "Presets",
  output: "Output and level", about: "About" };

// Field supplies Steam's label, spacing, focus highlight and activation; the icons inherit its color.
const iconPaths: Record<Exclude<Section, "band">, ReactNode> = {
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

/** Runs a backend action; a failure is also toasted, as the panel's message may be out of view. */
type Run = (action: string, payload?: Record<string, unknown>) => void;

/** What every page needs from the panel: the confirmed state and how to change it. */
interface Page {
  controller: Controller; snapshot: Snapshot; locked: boolean; run: Run;
  /** Shows a failure where the user can see it while a dialog hides the QAM. */
  notify(text: string): void;
  change(patch: Partial<EQ>): void;
  bandChange(index: number, patch: Partial<Band>): void;
  confirm(heading: string, description: string, action: () => void, ok?: string, destructive?: boolean): void;
  select(id: string): void;
}

function GainSlider({ label, value, onChange, disabled, range = limits.band_gain as [number, number] }:
  { label: string; value: number; onChange(value: number): void; disabled: boolean; range?: [number, number] }) {
  return <PanelSectionRow><SliderField label={label} value={value} min={range[0]} max={range[1]} step={0.5}
    resetValue={0} showValue valueSuffix=" dB" disabled={disabled} onChange={onChange} /></PanelSectionRow>;
}

/** What Volume boost does now; it is global, so it says so before it is moved. */
const boostNote = ({ boost, enabled, speakers_only }: Snapshot["state"]) =>
  !enabled ? "For every preset; takes effect when EQ is on."
    : boost === 0 ? "Louder than SteamOS allows, for every preset. The limiter keeps it from clipping."
    : `For every preset; the limiter keeps it from clipping.${speakers_only ? "" : " Mind your hearing on headphones."}`;

const typeSuffix = (band: Band) => band.type === "peaking" ? "" : band.type === "lowshelf" ? " · Low shelf" : " · High shelf";
const bandLabel = (band: Band) => `${formatHz(band.frequency)}${typeSuffix(band)}`;

function MainPage({ page, open, focus, preset }: { page: Page; open(section: Section): void; focus: Section | null; preset: string }) {
  const { snapshot: { state, audio, presets, dirty, version }, locked, change, select, controller } = page;
  const eq = state.working;
  const adjusted = eq.bands.filter(band => band.gain !== 0).length;
  const output = audio.outputs.find(o => o.name === state.target)?.description || (state.target ? "Disconnected output" : "No output chosen");
  const level = Math.abs(audio.effective_preamp) > 0.05 ? `level ${db(audio.effective_preamp)}` : "full level";
  return <>
    <PanelSection>
      <PanelSectionRow><ToggleField label="Enable EQ" checked={state.enabled}
        disabled={locked || (!audio.supported && !state.enabled)} onChange={enabled => page.run("enable", { enabled })} /></PanelSectionRow>
      <PanelSectionRow><DropdownItem label="Preset" layout="below" childrenContainerWidth="max"
        rgOptions={presets.map(p => ({ data: p.id, label: p.name }))}
        selectedOption={state.selected} disabled={locked} onChange={option => select(option.data)} /></PanelSectionRow>
      <PanelSectionRow><SliderField label="Volume boost" value={state.boost} min={limits.boost[0]} max={limits.boost[1]}
        step={0.5} resetValue={0} showValue valueSuffix=" dB" disabled={locked}
        description={boostNote(state)}
        onChange={boost => controller.edit(cloneEQ(eq), boost)} /></PanelSectionRow>
    </PanelSection>
    <PanelSection title="Tone">
      <GainSlider label={`Bass · below ${formatHz(limits.bass_frequency)}`} value={eq.bass} disabled={locked}
        range={limits.tone_gain as [number, number]} onChange={bass => change({ bass })} />
      <GainSlider label={`Treble · above ${formatHz(limits.treble_frequency)}`} value={eq.treble} disabled={locked}
        range={limits.tone_gain as [number, number]} onChange={treble => change({ treble })} />
      <PanelSectionRow><SliderField label="Stereo width" value={Math.round(eq.width * 100)}
        min={limits.width[0] * 100} max={limits.width[1] * 100} step={5} resetValue={100} showValue valueSuffix="%"
        description={eq.width === 1 ? undefined : eq.width < 1 ? "Narrower; 0% is mono." : "Wider than the original mix."}
        disabled={locked} onChange={percent => change({ width: percent / 100 })} /></PanelSectionRow>
    </PanelSection>
    <PanelSection title="More">
      <SectionLink title={titles.bands} icon={iconPaths.bands} focus={focus === "bands"} onOpen={() => open("bands")}
        description={`${eq.bands.length} bands · ${adjusted ? `${adjusted} adjusted` : "flat"}`} />
      <SectionLink title={titles.presets} icon={iconPaths.presets} focus={focus === "presets"} onOpen={() => open("presets")}
        description={dirty ? `${preset} · unsaved changes` : `${presets.length} presets`} />
      <SectionLink title={titles.output} icon={iconPaths.output} focus={focus === "output"} onOpen={() => open("output")}
        description={`${output} · ${level}`} />
      <SectionLink title={titles.about} icon={iconPaths.about} focus={focus === "about"} onOpen={() => open("about")}
        description={`Version ${version} · updates`} />
    </PanelSection>
  </>;
}

function BandsPage({ page, openBand, focus }: { page: Page; openBand(): void; focus: boolean }) {
  const { snapshot, locked, bandChange, confirm, run } = page;
  const eq = snapshot.state.working;
  return <>
    <div className="deq-card"><BandLevels eq={eq} /></div>
    <PanelSection>
      {eq.bands.map((band, i) => <GainSlider key={i} label={bandLabel(band)} value={band.gain} disabled={locked}
        onChange={gain => bandChange(i, { gain })} />)}
    </PanelSection>
    <PanelSection title="Fine tuning">
      <SectionLink title="Edit one band" description="Filter type, frequency and width" focus={focus} onOpen={openBand} />
      <PanelSectionRow><ActionButton disabled={locked} onClick={() => confirm("Reset to flat?",
        "Bands, bass, treble, stereo width and the level settings return to their defaults. Saved presets stay in your library.",
        () => run("reset"), "Reset")}>Reset to flat</ActionButton></PanelSectionRow>
    </PanelSection>
  </>;
}

function BandPage({ page, index, setIndex }: { page: Page; index: number; setIndex(index: number): void }) {
  const { snapshot, locked, bandChange } = page;
  const eq = snapshot.state.working;
  const band = eq.bands[index];
  const [frequencyText, setFrequencyText] = useState(String(band.frequency));
  useEffect(() => { setFrequencyText(String(band.frequency)); }, [band.frequency, index]);
  const exactFrequency = Number(frequencyText.trim().replace(",", "."));
  const validFrequency = frequencyText.trim() !== "" && Number.isFinite(exactFrequency) &&
    exactFrequency >= freqMin && exactFrequency <= freqMax;
  return <PanelSection>
    <PanelSectionRow><DropdownItem label="Band" layout="below" childrenContainerWidth="max" selectedOption={index}
      rgOptions={eq.bands.map((b, i) => ({ data: i, label: `${i + 1} · ${bandLabel(b)}` }))}
      onChange={option => setIndex(option.data)} /></PanelSectionRow>
    <GainSlider label="Gain" value={band.gain} disabled={locked} onChange={gain => bandChange(index, { gain })} />
    <PanelSectionRow><DropdownItem label="Filter type" layout="below" childrenContainerWidth="max" selectedOption={band.type} disabled={locked}
      rgOptions={filterTypes} onChange={option => bandChange(index, option.data === "peaking" ? { type: option.data }
        : { type: option.data, q: Math.min(band.q, limits.shelf_q_max) })} /></PanelSectionRow>
    <PanelSectionRow><SliderField label={`Frequency · ${formatHz(band.frequency)}`}
      value={Math.log10(Math.min(stableMax, Math.max(stableMin, band.frequency)))}
      min={Math.log10(stableMin)} max={Math.log10(stableMax)} step={Math.log10(2) / 12} disabled={locked}
      onChange={value => bandChange(index, { frequency: Math.min(stableMax, Math.max(stableMin, Math.round(10 ** value * 100) / 100)) })} />
    </PanelSectionRow>
    <PanelSectionRow><SliderField label="Bandwidth (Q)"
      description={band.type === "peaking" ? "Higher Q affects a narrower range." : "Higher Q adds a bump at the shelf's corner."}
      value={band.q} min={limits.q[0]} max={band.type === "peaking" ? limits.q[1] : limits.shelf_q_max} step={0.1} showValue disabled={locked}
      onChange={q => bandChange(index, { q })} /></PanelSectionRow>
    <Details rows={[]} label="exact frequency">
      <PanelSectionRow><TextField label="Exact frequency (Hz)" value={frequencyText} disabled={locked}
        description={`${formatHz(stableMin)} – ${formatHz(stableMax)}. 0 Hz and ${formatHz(limits.passthrough_frequency)}–${formatHz(freqMax)} are also accepted for compatibility with the original Go S preset.`}
        onChange={event => setFrequencyText(event.target.value.slice(0, 20))} /></PanelSectionRow>
      <PanelSectionRow><ActionButton disabled={locked || !validFrequency || exactFrequency === band.frequency}
        onClick={() => {
          const frequency = safeFrequency(exactFrequency);
          setFrequencyText(String(frequency));  // Show a snapped value even when the band already had it.
          bandChange(index, { frequency });
        }}>Apply exact frequency</ActionButton></PanelSectionRow>
    </Details>
  </PanelSection>;
}

function PresetsPage({ page, pick }: { page: Page; pick(kind: "import" | "export"): void }) {
  const { controller, snapshot, locked, confirm, select, run, notify } = page;
  const preset = snapshot.presets.find(p => p.id === snapshot.state.selected) ?? snapshot.presets[0];
  // A dialog hides the QAM: a refused name reopens it with what was typed and the reason.
  const named = (title: string, ok: string, action: "save_as" | "rename", initial = "", error?: string) => {
    showModal(<NameModal title={title} ok={ok} initial={initial} error={error} onSubmit={async name => {
      const result = await controller.action(action, action === "rename" ? { name, id: preset.id } : { name });
      if (result.ok) return;
      if (result.state) named(title, ok, action, name, result.error || "The operation failed.");
      else notify(result.error || "The operation failed.");  // Busy or disconnected: nothing to fix in the dialog.
    }} />);
  };
  return <>
    <PanelSection>
      <PanelSectionRow><DropdownItem label="Preset" layout="below" childrenContainerWidth="max"
        rgOptions={snapshot.presets.map(p => ({ data: p.id, label: `${p.name}${p.factory ? " · built-in" : ""}` }))}
        selectedOption={preset.id} disabled={locked} onChange={option => select(option.data)} /></PanelSectionRow>
    </PanelSection>
    <PanelSection title={`${preset.name}${snapshot.dirty ? " · unsaved changes" : ""}`}>
      {preset.description && infoRow("About this preset", preset.description)}
      {snapshot.dirty && !preset.factory && <PanelSectionRow><ActionButton disabled={locked}
        onClick={() => run("save")}>Save changes</ActionButton></PanelSectionRow>}
      {snapshot.dirty && <PanelSectionRow><ActionButton disabled={locked}
        onClick={() => confirm("Discard changes?", `Restore the saved version of “${preset.name}”.`,
          () => run("revert", { id: preset.id }), "Discard")}>Discard changes</ActionButton></PanelSectionRow>}
      <PanelSectionRow><ActionButton disabled={locked}
        onClick={() => named("Save as new preset", "Save", "save_as")}>Save as new…</ActionButton></PanelSectionRow>
      {!preset.factory && <>
        <PanelSectionRow><ActionButton disabled={locked}
          onClick={() => named("Rename preset", "Rename", "rename", preset.name)}>Rename…</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked}
          onClick={() => confirm("Delete preset?", `Delete “${preset.name}” and switch to Flat?`,
            () => run("delete", { id: preset.id }), "Delete", true)}>Delete…</ActionButton></PanelSectionRow>
      </>}
      {preset.factory && snapshot.dirty && infoRow("Built-in preset", "Built-in presets stay as they are. Use Save as new to keep your changes.")}
    </PanelSection>
    <PanelSection title="Files">
      <PanelSectionRow><ActionButton disabled={locked} onClick={() => pick("import")}>Import preset…</ActionButton></PanelSectionRow>
      <PanelSectionRow><ActionButton disabled={locked}
        onClick={() => run("export", { all: false })}>Export this sound</ActionButton></PanelSectionRow>
      <PanelSectionRow><ActionButton disabled={locked}
        onClick={() => run("export", { all: true })}>Export all presets</ActionButton></PanelSectionRow>
      <Details rows={[["Export folder", snapshot.export_directory],
        ["What is exported", "This sound includes your latest edits. All presets is your saved library."]]}>
        <PanelSectionRow><ActionButton disabled={locked} onClick={() => pick("export")}>Export this sound to another folder…</ActionButton></PanelSectionRow>
      </Details>
    </PanelSection>
  </>;
}

function OutputPage({ page }: { page: Page }) {
  const { run, snapshot: { state, audio }, locked, change } = page;
  const eq = state.working;
  const disconnected = state.target && !audio.outputs.some(o => o.name === state.target)
    ? [{ data: state.target, label: "Selected output · disconnected" }] : [];
  const steam = audio.outputs.find(o => o.name === audio.default)?.description || audio.default || "Unknown";
  return <>
    <PanelSection title="Output">
      <PanelSectionRow><DropdownItem label="Apply EQ to" layout="below" childrenContainerWidth="max" selectedOption={state.target} disabled={locked}
        strDefaultLabel="Choose an output"
        rgOptions={[...audio.outputs.map(output => ({ data: output.name, label: output.description })), ...disconnected]}
        onChange={option => run("output", { target: option.data, speakers_only: state.speakers_only })} /></PanelSectionRow>
      <PanelSectionRow><ToggleField label="Speakers only" checked={state.speakers_only} disabled={locked}
        description="Pause EQ while this device plays to headphones. Turn off to EQ a headset or HDMI output."
        onChange={speakers_only => run("output", { target: state.target, speakers_only })} /></PanelSectionRow>
    </PanelSection>
    <PanelSection title="Level">
      <PanelSectionRow><SliderField label="Input level" value={eq.preamp} min={limits.preamp[0]} max={limits.preamp[1]}
        step={0.5} resetValue={0} showValue valueSuffix=" dB" disabled={locked}
        description={audio.compensation < -0.05
          ? `EQ boosts above +${limits.limiter_allowance} dB lower it by ${db(-audio.compensation).slice(1)} so the limiter does not squash them.`
          : undefined}
        onChange={preamp => change({ preamp })} /></PanelSectionRow>
      <Details rows={[
        ["Status", audio.message],
        ["Effective input level", `${db(audio.effective_preamp)}, including automatic headroom`],
        ["Steam output", steam],
        ["Your volume stays yours", "EQ sits in front of the chosen output. Steam's volume, volume buttons and output choice work as usual; when Steam plays to another output, EQ waits."],
      ]}>
        <PanelSectionRow><ActionButton disabled={locked || !audio.supported}
          onClick={() => run("refresh")}>Reconnect EQ</ActionButton></PanelSectionRow>
      </Details>
    </PanelSection>
  </>;
}

function AboutPage({ version, updates }: { version: string; updates?: UpdateController }) {
  return <>
    <PanelSection title="DeckyEQ">
      {infoRow(`Version ${version}`, "A live equalizer with bass, treble, stereo width, volume boost, a limiter and your own presets, inside Gaming Mode.")}
      {infoRow("Author", "Rayek · github.com/Rayekkk/DeckyEQ")}
      {infoRow("License", "BSD 3-Clause · Copyright (c) 2026 Rayekkk. Dependency notices are included in the plugin package.")}
      {infoRow("Powered by PipeWire", "Built-in stereo filters · no additional audio effects packages. Turning EQ off restores normal routing and keeps your library.")}
    </PanelSection>
    {updates && <UpdateSection controller={updates} />}
  </>;
}

export function App({ controller, files, updates, visible = true, toast }: {
  controller: Controller; files: FileUI; updates?: UpdateController; visible?: boolean;
  /** Shows a short outcome (e.g. where a file was exported); without it the note stays in the panel. */
  toast?(body: string): void;
}) {
  const view = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const [section, setSection] = useState<Section | null>(null);
  const [left, setLeft] = useState<Section | null>(null);  // The page just closed, whose row takes focus again.
  const [bandIndex, setBandIndex] = useState(0);
  const [picking, setPicking] = useState(false);
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
    if (!toast || !view.note) return;
    toast(view.note);
    controller.clearMessage();  // The note lives in the controller; a remount must not toast it again.
  }, [toast, view.note]);

  if (!snapshot) return <div className="deq"><style>{styles}</style>
    <PanelSection title="DeckyEQ"><PanelSectionRow>
      <Field focusable label={view.error ? "Could not load DeckyEQ" : "Loading equalizer…"} description={view.error || "Connecting to the plugin backend."} />
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
  const confirm = (heading: string, description: string, action: () => void, ok = "Continue", destructive = false) => {
    showModal(<ConfirmModal strTitle={heading} strDescription={description} bDestructiveWarning={destructive}
      strOKButtonText={ok} strCancelButtonText="Cancel" onOK={action} />);
  };
  const notify = (text: string) => toast?.(text);
  const run: Run = (action, payload) => void controller.action(action, payload).then(result => {
    if (!result.ok) notify(result.error || "The operation failed.");
  });
  const page: Page = {
    controller, snapshot, locked, confirm, run, notify,
    change: patch => controller.edit({ ...cloneEQ(state.working), ...patch }),
    bandChange: (index, patch) => {
      const next = cloneEQ(state.working);
      next.bands[index] = { ...next.bands[index], ...patch };
      controller.edit(next);
    },
    select: id => {
      if (id === state.selected) return;
      const apply = () => run("select", { id });
      if (snapshot.dirty) confirm("Switch preset?", "Your unsaved changes will be replaced. Save them first if you want to keep them.", apply, "Switch");
      else apply();
    },
  };
  const pick = async (kind: "import" | "export") => {
    if (locked) return;
    setPicking(true);
    try {
      controller.clearMessage();
      await controller.flush();
      if (controller.getSnapshot().error) return;  // Keep a failed edit visible instead of exporting around it.
      if (kind === "import") {
        const path = await files.importFile(snapshot.home);
        if (path) run("import", { path });
      } else {
        const directory = await files.exportFolder(snapshot.home);
        if (directory) run("export", { directory, all: false });
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
    <MainPage page={page} open={navigate} focus={visible ? left : null} preset={preset.name} />
  </div>;

  const parent = section === "band" ? "bands" : null;
  const returning = section === "bands" && left === "band";
  return <Focusable className="deq" onCancelActionDescription="Back" onCancelButton={event => {
    event.preventDefault();
    event.stopPropagation();  // B goes back one page instead of leaving the plugin.
    navigate(parent);
  }}><style>{styles}</style>
    <SectionHeader key={section} title={titles[section]} back={parent ? titles[parent] : "DeckyEQ"}
      onBack={() => navigate(parent)} focus={visible && !returning} />
    {messages}
    {section === "bands" && <BandsPage page={page} openBand={() => navigate("band")} focus={visible && returning} />}
    {section === "band" && <BandPage page={page} index={Math.min(bandIndex, state.working.bands.length - 1)} setIndex={setBandIndex} />}
    {section === "presets" && <PresetsPage page={page} pick={kind => void pick(kind)} />}
    {section === "output" && <OutputPage page={page} />}
    {section === "about" && <AboutPage version={snapshot.version} updates={updates} />}
  </Focusable>;
}
