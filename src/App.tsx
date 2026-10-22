import { ComponentProps, useEffect, useState, useSyncExternalStore } from "react";
import { ButtonItem, ConfirmModal, DropdownItem, Field, PanelSection,
  PanelSectionRow, showModal, SliderField, TextField, ToggleField } from "@decky/ui";
import { Controller } from "./controller";
import { cloneEQ, db, EQ, FilterType, formatHz, safeFrequency } from "./types";
import { UpdateController, UpdateSection } from "./updates";

const filterTypes: { data: FilterType; label: string }[] = [
  { data: "peaking", label: "Bell" }, { data: "lowshelf", label: "Low shelf" }, { data: "highshelf", label: "High shelf" },
];

// Steam's default ButtonItem uses an inline, fixed-width field layout.
// Standalone QAM actions must occupy the available row instead.
const ActionButton = (props: ComponentProps<typeof ButtonItem>) =>
  <ButtonItem {...props} layout="below" childrenContainerWidth="max" />;

export interface FileUI { importFile(home: string): Promise<string | null>; exportFolder(home: string): Promise<string | null> }
const styles = `
.deq { --deq-accent: #73e3c0; color: #eef2f7; padding-bottom: 18px; width:100%; max-width:100%; min-width:0; box-sizing:border-box; }
.deq *, .deq *::before, .deq *::after { box-sizing:border-box; }
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
  return <svg className="deq-chart" viewBox="0 0 270 94" role="img" aria-label="Ten equalizer band gain levels">
    <line x1="2" x2="268" y1="40" y2="40" stroke="#ffffff20" />
    <line x1="2" x2="268" y1="10" y2="10" stroke="#ffffff08" />
    <line x1="2" x2="268" y1="70" y2="70" stroke="#ffffff08" />
    {eq.bands.map((band, i) => {
      const y = 40 - band.gain * 2.5;
      return <g key={i}>
        <rect x={i * 27 + 5} y={Math.min(y, 40)} width="17" height={Math.max(2, Math.abs(y - 40))}
          rx="2" fill={band.gain >= 0 ? "#73e3c0" : "#af9aeb"} />
        <text x={i * 27 + 13.5} y="89" textAnchor="middle" fill="#91a1b2" fontSize="8">
          {band.frequency >= 1000 ? `${Number((band.frequency / 1000).toFixed(1))}k` : Math.round(band.frequency)}
        </text>
      </g>;
    })}
  </svg>;
}

export function App({ controller, files, updates, visible = true }: { controller: Controller; files: FileUI; updates?: UpdateController; visible?: boolean }) {
  const view = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const [page, setPage] = useState<"eq" | "presets" | "output" | "about">("eq");
  const [title, setTitle] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [bandIndex, setBandIndex] = useState(0);
  const [frequencyText, setFrequencyText] = useState("");
  const [picking, setPicking] = useState(false);
  const snapshot = view.snapshot;
  const locked = view.busy || !view.connected || picking;
  const currentFrequency = snapshot?.state.working.bands[bandIndex].frequency;
  useEffect(() => { setFrequencyText(String(currentFrequency ?? "")); }, [currentFrequency, bandIndex]);
  useEffect(() => {
    if (!visible) return;
    void controller.refresh();
    const timer = setInterval(() => void controller.refresh(), 1800);
    return () => clearInterval(timer);
  }, [controller, visible]);

  if (!snapshot) return <div className="deq"><style>{styles}</style>
    <PanelSection title="DeckyEQ"><PanelSectionRow>
      <Field label={view.error ? "Could not load DeckyEQ" : "Loading equalizer…"} description={view.error || "Connecting to the plugin backend."} />
      {view.error && <ActionButton onClick={() => void controller.refresh()}>Retry</ActionButton>}
    </PanelSectionRow></PanelSection>
    {view.error && updates && <UpdateSection controller={updates} />}
  </div>;

  const { state, audio } = snapshot;
  const eq = state.working;
  const preset = snapshot.presets.find(p => p.id === state.selected)!;
  const exactFrequency = Number(frequencyText.trim().replace(",", "."));
  const validFrequency = frequencyText.trim() !== "" && Number.isFinite(exactFrequency) && exactFrequency >= 0 && exactFrequency <= 48000;
  const change = (patch: Partial<EQ>) => controller.edit({ ...cloneEQ(eq), ...patch });
  const bandChange = (index: number, patch: Partial<EQ["bands"][number]>) => {
    const next = cloneEQ(eq);
    next.bands[index] = { ...next.bands[index], ...patch };
    controller.edit(next);
  };
  const confirm = (heading: string, description: string, action: () => void, destructive = false) => {
    showModal(<ConfirmModal strTitle={heading} strDescription={description} bDestructiveWarning={destructive}
      strOKButtonText={destructive ? "Delete" : "Continue"} strCancelButtonText="Cancel" onOK={action} />);
  };
  const select = (id: string) => {
    if (id === state.selected) return;
    const apply = () => { void controller.action("select", { id }); };
    if (snapshot.dirty) confirm("Switch preset?", "Your current draft will be replaced. Save it first if you want to keep it.", apply);
    else apply();
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
        if (path) await controller.action("import", { path });
      } else {
        const directory = await files.exportFolder(snapshot.home);
        if (directory) await controller.action("export", { directory, all: false });
      }
    } catch (error) { controller.reportError(error); }
    finally { setPicking(false); }
  };
  const slider = (label: string, value: number, onChange: (value: number) => void, min = -12, max = 12) =>
    <PanelSectionRow key={label}><SliderField label={label} value={value} min={min} max={max} step={0.5}
      resetValue={0} showValue valueSuffix=" dB" disabled={locked} onChange={onChange} /></PanelSectionRow>;

  return <div className="deq"><style>{styles}</style>
    <div className="deq-card">
      <div className="deq-status"><span className={`deq-dot ${audio.phase === "active" ? "live" : ""}`} />
        {view.saving ? "Applying…" : audio.phase === "active" ? "Live equalizer" : audio.phase === "off" ? "EQ off" : audio.phase}
      </div>
      <div className="deq-name">{preset.name}{snapshot.dirty ? " · edited" : ""}</div>
      <div className="deq-small">{audio.message}</div>
      {preset.description && <div className="deq-small">{preset.description}</div>}
      {page === "eq" && <BandLevels eq={eq} />}
    </div>
    {(view.error || audio.error) && <div className="deq-note deq-error" role="alert">{view.error || audio.error}</div>}
    {snapshot.warning && <div className="deq-note">{snapshot.warning}</div>}
    {view.note && <div className="deq-note" role="status">{view.note}</div>}
    <PanelSection>
      <PanelSectionRow><DropdownItem label="View" layout="below" childrenContainerWidth="max" selectedOption={page}
        rgOptions={[{data: "eq", label: "Equalizer"}, {data: "presets", label: "Presets"}, {data: "output", label: "Output"}, {data: "about", label: "About"}]}
        onChange={option => setPage(option.data)} /></PanelSectionRow>
      <PanelSectionRow><ToggleField label="Enable EQ" checked={state.enabled}
        description={state.enabled && audio.phase !== "active" ? "Enabled; waiting for a suitable output." : undefined}
        disabled={locked || (!audio.supported && !state.enabled)} onChange={enabled => void controller.action("enable", { enabled })} /></PanelSectionRow>
      {!view.connected && <PanelSectionRow><ActionButton onClick={() => void controller.refresh()}>Reconnect</ActionButton></PanelSectionRow>}
    </PanelSection>

    {page === "eq" && <>
      <PanelSection title="Preset">
        <PanelSectionRow><DropdownItem label="Current preset" layout="below" childrenContainerWidth="max" rgOptions={snapshot.presets.map(p => ({ data: p.id, label: p.name }))}
          selectedOption={state.selected} disabled={locked} onChange={option => select(option.data)} /></PanelSectionRow>
        {snapshot.dirty && <PanelSectionRow><ActionButton disabled={locked} onClick={() => setPage("presets")}>Save this sound…</ActionButton></PanelSectionRow>}
      </PanelSection>
      <PanelSection title="Tone">
        {slider("Bass · below 120 Hz", eq.bass, value => change({ bass: value }), -9, 9)}
        {slider("Treble · above 6 kHz", eq.treble, value => change({ treble: value }), -9, 9)}
      </PanelSection>
      <PanelSection title="10-band equalizer">
        {eq.bands.map((band, i) => slider(`${i + 1} · ${formatHz(band.frequency)}${band.type === "peaking" ? "" : band.type === "lowshelf" ? " · Low shelf" : " · High shelf"}`, band.gain, value => bandChange(i, { gain: value })))}
      </PanelSection>
      <PanelSection title="Advanced bands">
        <PanelSectionRow><ToggleField label="Edit filter type, frequency & width" checked={advanced} onChange={setAdvanced} /></PanelSectionRow>
        {advanced && <>
          <PanelSectionRow><DropdownItem label="Band" layout="below" childrenContainerWidth="max" selectedOption={bandIndex} disabled={locked}
            rgOptions={eq.bands.map((band, i) => ({ data: i, label: `${i + 1} · ${formatHz(band.frequency)}` }))}
            onChange={option => setBandIndex(option.data)} /></PanelSectionRow>
          <PanelSectionRow><DropdownItem label="Filter type" layout="below" childrenContainerWidth="max" selectedOption={eq.bands[bandIndex].type} disabled={locked}
            rgOptions={filterTypes} onChange={option => bandChange(bandIndex, { type: option.data })} /></PanelSectionRow>
          <PanelSectionRow><SliderField label={`Frequency · ${formatHz(eq.bands[bandIndex].frequency)}`}
            value={Math.log10(Math.min(20000, Math.max(20, eq.bands[bandIndex].frequency)))} min={Math.log10(20)} max={Math.log10(20000)} step={0.005}
            disabled={locked} onChange={value => bandChange(bandIndex, { frequency: Math.min(20000, Math.max(20, Math.round(10 ** value * 100) / 100)) })} /></PanelSectionRow>
          <PanelSectionRow><TextField label="Exact frequency (Hz)" value={frequencyText} disabled={locked}
            description="20 Hz – 20 kHz. 0 Hz and 24–48 kHz are also accepted for compatibility with the original Go S preset."
            onChange={event => setFrequencyText(event.target.value.slice(0, 20))} /></PanelSectionRow>
          <PanelSectionRow><ActionButton disabled={locked || !validFrequency || exactFrequency === currentFrequency}
            onClick={() => bandChange(bandIndex, { frequency: safeFrequency(exactFrequency) })}>Apply exact frequency</ActionButton></PanelSectionRow>
          <PanelSectionRow><SliderField label="Width · Q" description={eq.bands[bandIndex].type === "peaking" ? "Higher Q affects a narrower range." : "Higher Q adds resonance around the shelf transition."}
            value={eq.bands[bandIndex].q} min={0.2} max={5} step={0.1} showValue disabled={locked}
            onChange={value => bandChange(bandIndex, { q: value })} /></PanelSectionRow>
        </>}
      </PanelSection>
    </>}

    {page === "presets" && <>
      <PanelSection title="Your library">
        <PanelSectionRow><DropdownItem label="Preset" layout="below" childrenContainerWidth="max" rgOptions={snapshot.presets.map(p => ({ data: p.id, label: p.name }))}
          selectedOption={state.selected} disabled={locked} onChange={option => select(option.data)} /></PanelSectionRow>
        <PanelSectionRow><TextField label="Preset name" value={title} disabled={locked}
          onChange={event => setTitle(event.target.value.slice(0, 64))} /></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked || !title.trim()} onClick={async () => {
          const result = await controller.action("save_as", { name: title });
          if (result.ok) setTitle("");
        }}>Save as new preset</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked || preset.factory || !snapshot.dirty}
          onClick={() => void controller.action("save")}>Update selected preset</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked || preset.factory || !title.trim()}
          onClick={async () => { if ((await controller.action("rename", { name: title })).ok) setTitle(""); }}>Rename selected preset</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked || !snapshot.dirty}
          onClick={() => confirm("Revert draft?", "Restore the saved version of this preset.", () => void controller.action("revert"))}>Revert to saved preset</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked || preset.factory}
          onClick={() => confirm("Delete preset?", `Delete “${preset.name}” and switch to Flat?`, () => void controller.action("delete"), true)}>Delete selected preset</ActionButton></PanelSectionRow>
      </PanelSection>
      <PanelSection title="Import & export">
        <PanelSectionRow><ActionButton disabled={locked} onClick={() => void pick("import")}>Import preset or bank…</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked} onClick={() => void controller.action("export", { all: false })}>Export current sound</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked} onClick={() => void pick("export")}>Export current sound to folder…</ActionButton></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked} onClick={() => void controller.action("export", { all: true })}>Export saved preset library</ActionButton></PanelSectionRow>
        <PanelSectionRow><Field label="Portable JSON files" description={`Current sound includes your latest edits. Library export contains saved presets. Default folder: ${snapshot.export_directory}`} /></PanelSectionRow>
      </PanelSection>
    </>}

    {page === "output" && <>
      <PanelSection title="Audio output">
        <PanelSectionRow><Field label="Full hardware volume" description="Enabling EQ sets the physical output to 100% and moves your volume setting to DeckyEQ. Disabling EQ transfers the current volume back." /></PanelSectionRow>
        <PanelSectionRow><DropdownItem label="Apply EQ to" layout="below" childrenContainerWidth="max" selectedOption={state.target} disabled={locked}
          strDefaultLabel="Choose an output"
          rgOptions={[...audio.outputs.map(output => ({ data: output.name, label: output.description })),
            ...(state.target && !audio.outputs.some(o => o.name === state.target) ? [{ data: state.target, label: "Selected output · disconnected" }] : [])]}
          onChange={option => void controller.action("output", { target: option.data, speakers_only: state.speakers_only })} /></PanelSectionRow>
        <PanelSectionRow><ToggleField label="Speaker port only" checked={state.speakers_only} disabled={locked}
          description="Automatically bypass EQ when this device switches to headphones. Turn off to EQ a selected headset or HDMI output."
          onChange={speakers_only => void controller.action("output", { target: state.target, speakers_only })} /></PanelSectionRow>
        <PanelSectionRow><ActionButton disabled={locked || !audio.supported}
          onClick={() => void controller.action("refresh")}>Refresh / reconnect EQ</ActionButton></PanelSectionRow>
      </PanelSection>
      <PanelSection title="Level & headroom">
        <PanelSectionRow><ToggleField label="Automatic headroom" checked={eq.auto_headroom} disabled={locked}
          description="Compensates for filter boosts, shelf resonance and positive input gain. Conservative; this can make EQ quieter."
          onChange={auto_headroom => change({ auto_headroom })} /></PanelSectionRow>
        {slider("Input level", eq.preamp, value => change({ preamp: value }), -36, 12)}
        <PanelSectionRow><Field label="Effective input level" description="Includes automatic headroom compensation.">
          <span className="deq-value">{db(audio.effective_preamp)}</span></Field></PanelSectionRow>
        {!eq.auto_headroom && <PanelSectionRow><Field label="Headroom is manual" description="Positive EQ gains can clip. Reduce the input level when boosting frequencies. Total boost is limited to +24 dB." /></PanelSectionRow>}
      </PanelSection>
      <PanelSection title="Reset">
        <PanelSectionRow><ActionButton disabled={locked} onClick={() => confirm("Reset EQ controls?", "Reset this draft to flat. Saved presets will stay in your library.", () => void controller.action("reset"))}>Reset current controls</ActionButton></PanelSectionRow>
      </PanelSection>
    </>}
    {page === "about" && <>
      <PanelSection title="About DeckyEQ">
        <PanelSectionRow><Field label={`DeckyEQ ${snapshot.version}`} description="A live equalizer, bass and treble controls, and your own presets inside Gaming Mode." /></PanelSectionRow>
        <PanelSectionRow><Field label="Author" description="Rayek · github.com/Rayekkk/DeckyEQ" /></PanelSectionRow>
        <PanelSectionRow><Field label="License" description="BSD 3-Clause · Copyright (c) 2026 Rayekkk. Dependency notices are included in the plugin package." /></PanelSectionRow>
        <PanelSectionRow><Field label="Powered by PipeWire" description="Built-in stereo filters · no additional audio effects packages. Turning EQ off restores normal routing and keeps your library." /></PanelSectionRow>
      </PanelSection>
      {updates && <UpdateSection controller={updates} />}
    </>}
  </div>;
}
