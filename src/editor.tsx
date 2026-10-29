import { GamepadButton } from "@decky/ui";
import { CurveAxes, CurveGraph as Graph, curveLayout, CurveLayout } from "./curve";
import { response } from "./headroom";
import { bandHz, dead, flatPair, level, pairs, shown, withBand, withPairGain } from "./pairs";
import { ACCENT } from "./shared";
import { colors } from "./ui";
import { Band, db, EQ, formatHz, limits, sameEQ } from "./types";

// The chart: 20 Hz–20 kHz on a log axis, ±RANGE dB; handles sit at each band's own gain.
const RANGE = 15;
const [fMin, fMax] = limits.stable_frequency, [gMin, gMax] = limits.band_gain;
const SAMPLES = Array.from({ length: 160 }, (_, i) => fMin * (fMax / fMin) ** (i / 159));
const TICKS = { qam: [63, 250, 1000, 4000, 16000], full: [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000] };
const TYPES: Band["type"][] = ["peaking", "lowshelf", "highshelf"];
const typeNames: Record<Band["type"], string> = { peaking: "Bell", lowshelf: "Low shelf", highshelf: "High shelf" };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const qMax = (type: Band["type"]) => type === "peaking" ? limits.q[1] : limits.shelf_q_max;

/** A gain moved by ↑↓ or set flat by X; undefined for any other button. */
export const padGain = (gain: number, button: number) => button === GamepadButton.DIR_UP ? clamp(gain + 0.5, gMin, gMax)
  : button === GamepadButton.DIR_DOWN ? clamp(gain - 0.5, gMin, gMax) : button === GamepadButton.SECONDARY ? 0 : undefined;

/** One band changed by a gamepad button while editing; undefined when the button does not change a band.
 *  The graph only moves gains: left and right choose the band, whose frequency is the same in every preset. */
export function padChange(band: Band, button: number): Partial<Band> | undefined {
  const gain = padGain(band.gain, button);
  if (gain !== undefined) return { gain };
  switch (button) {
    case GamepadButton.TRIGGER_LEFT: return { q: Math.round(clamp(band.q - 0.1, limits.q[0], qMax(band.type)) * 10) / 10 };
    case GamepadButton.TRIGGER_RIGHT: return { q: Math.round(clamp(band.q + 0.1, limits.q[0], qMax(band.type)) * 10) / 10 };
    case GamepadButton.OPTIONS: {
      const type = TYPES[(TYPES.indexOf(band.type) + 1) % TYPES.length];
      return { type, q: Math.min(band.q, qMax(type)) };
    }
  }
  return undefined;
}

/** The size of the line that reads the band: in the QAM 12 px, the panel's small text, on its 263 px graph. */
const readingSize = (L: CurveLayout, fullscreen: boolean) => fullscreen ? L.font : 12 * 300 / 263;
/** viewBox geometry (curve.tsx's defaults: 300×180 in the QAM, text at 16 px in full screen, like Companion's fan curve). */
export const graphLayout = (fullscreen: boolean, width = 800, height = 300) => {
  const L = curveLayout(fullscreen, { width, height });
  return { ...L, reading: readingSize(L, fullscreen) };
};

/** The curve with one handle per band (or pair with five sliders): drag a dot up or down, or press A and use the gamepad. */
export function CurveGraph({ eq, five, disabled, fullscreen = false, selected, select, edit }: {
  eq: EQ; five: boolean; disabled: boolean; fullscreen?: boolean;
  selected: number; select(index: number): void; edit(next: EQ): void;
}) {
  const groups = five ? pairs(eq) : null;
  // Every preset shows all its bands: a placeholder that does nothing appears as a flat band at its usual frequency.
  const handles = groups ? groups.map((p, i) => ({ i, frequency: p.frequency, gain: p.gain }))
    : eq.bands.map((_, i) => ({ i, frequency: shown(eq, i).frequency, gain: shown(eq, i).gain }));
  // The selection is a band: with five sliders the pair it is in, and picking a pair selects its first band.
  const at = Math.min(groups ? selected >> 1 : selected, handles.length - 1), index = at;
  const pick = (handle: number) => select(groups ? 2 * handle : handle);
  // Width and filter type are full-screen tools for one band; a level has only its gain, and a placeholder gets them
  // once it has a gain (see pairs.ts).
  const tools = fullscreen && !groups && !level(eq.bands[index]) && !dead(eq.bands[index]);
  const change = (next: EQ) => { if (!sameEQ(next, eq)) edit(next); };  // An edit that changes nothing is not sent.
  const setGain = (i: number, gain: number) => {
    if (gain !== handles[i].gain) change(groups ? withPairGain(eq, i, gain) : withBand(eq, i, { gain }));
  };
  /** Applies a button to the selected handle; false when it is not one of the editor's here. */
  const apply = (button: number) => {
    if (!handles.length) return false;
    if (button === GamepadButton.DIR_LEFT || button === GamepadButton.DIR_RIGHT) {
      pick(handles[(at + (button === GamepadButton.DIR_LEFT ? handles.length - 1 : 1)) % handles.length].i);
      return true;
    }
    const gain = padGain(handles[at].gain, button);
    if (gain !== undefined) {
      if (groups && button === GamepadButton.SECONDARY) change(flatPair(eq, index));  // Both flat, not just an average of 0.
      else setGain(index, gain);
      return true;
    }
    if (!tools) return false;
    const patch = padChange(shown(eq, index), button);
    if (patch) change(withBand(eq, index, patch));
    return !!patch;
  };
  /** The gain at a height in the graph; up and down only: the frequency stays. */
  const gainAt = (L: CurveLayout, y: number) => RANGE - (y - L.top) / (L.bottom - L.top) * 2 * RANGE;
  const band = shown(eq, index), pair = groups?.[index];
  const reading = pair ? [pair.label, db(pair.gain)] : [formatHz(bandHz(eq, index)), db(band.gain)];
  // The QAM at rest shows the reading only; the filter type and Q appear once the user edits, and in full screen.
  // Band numbers only in full screen: the QAM's line must fit its narrow graph.
  const number = fullscreen ? [pair ? `Bands ${2 * index + 1} and ${2 * index + 2}` : `Band ${index + 1}`] : [];
  const about = (steering: boolean) => (pair ? [...number, ...reading] : !fullscreen && !steering ? reading
    : [...number, typeNames[band.type], ...reading, `Q ${band.q.toFixed(1)}`]).join(" · ");
  const hint = (steering: boolean) => !steering ? "Drag a dot or press A to edit"
    : groups ? "←→ pair · ↑↓ gain · X flat · B done"
    : tools ? "←→ band · ↑↓ gain · LT/RT Q · Y type · X flat · B done"
    : "←→ band · ↑↓ gain · X flat · B done";
  const legend: Record<number, string> = groups ? { [GamepadButton.SECONDARY]: "Flat pair" }
    : !tools ? { [GamepadButton.SECONDARY]: "Flat band" }
    : { [GamepadButton.TRIGGER_LEFT]: "Wider", [GamepadButton.TRIGGER_RIGHT]: "Narrower", [GamepadButton.SECONDARY]: "Flat band",
      [GamepadButton.OPTIONS]: "Filter type" };
  return <Graph<{ index: number; gain: number }> className="deq-graph" accent={ACCENT} fullscreen={fullscreen} disabled={disabled}
    label="Equalizer curve. Drag a dot up or down, or press A and use the D-pad." hint={hint} legend={legend} press={apply}
    // The finger moves the dot by as much as it moves, from where it touched: touching a dot never makes it jump.
    drag={(h, now, from, L) => setGain(h.index, Math.round(clamp(h.gain + gainAt(L, now.y) - gainAt(L, from.y), gMin, gMax) * 2) / 2)}>
    {({ layout: L, steering, grab }) => {
      const x = (hz: number) => L.left + Math.log(clamp(hz, fMin, fMax) / fMin) / Math.log(fMax / fMin) * (L.width - L.left - L.right);
      const y = (gain: number) => L.top + (RANGE - clamp(gain, -RANGE, RANGE)) / (2 * RANGE) * (L.bottom - L.top);
      const curve = response(eq, SAMPLES).map((g, i) => `${i ? "L" : "M"}${x(SAMPLES[i]).toFixed(1)} ${y(g).toFixed(1)}`).join(" ");
      const size = readingSize(L, fullscreen);
      return <>
        <CurveAxes layout={L} x={x} y={y} rows={[-12, -6, 0, 6, 12].map(g => ({ value: g, label: g > 0 ? `+${g}` : String(g), strong: g === 0 }))}
          columns={TICKS[fullscreen ? "full" : "qam"].map(f => ({ value: f, label: f >= 1000 ? `${f / 1000}k` : String(f) }))} />
        {fullscreen && <>
          <line x1={L.left} x2={L.width - L.right} y1={y(limits.limiter_allowance)} y2={y(limits.limiter_allowance)}
            stroke={ACCENT} strokeOpacity={0.4} strokeDasharray="6 6" />
          <text x={L.width - L.right} y={y(limits.limiter_allowance) - L.font * 0.4} textAnchor="end" fill={ACCENT} fontSize={L.font * 0.85}>
            Boosts above +{limits.limiter_allowance} dB lower the level</text>
        </>}
        <path d={curve} fill="none" stroke={ACCENT} strokeWidth={2.5 * L.scale} />
        {handles.map(h => <g key={h.i} style={{ cursor: "ns-resize" }} onPointerDown={evt => { pick(h.i); grab(evt, { index: h.i, gain: h.gain }); }}>
          <circle className="deq-hit" cx={x(h.frequency)} cy={y(h.gain)} r={(fullscreen ? 22 : 16) * L.scale} fill="transparent" />
          <circle cx={x(h.frequency)} cy={y(h.gain)} r={(h.i === index ? 7 : 4.5) * L.scale}
            fill={h.i === index ? colors.curve.selected : colors.surface} stroke={ACCENT} strokeWidth={2 * L.scale} />
        </g>)}
        {handles.length > 0 && <text x={L.left} y={size * 1.1} fill={colors.curve.reading} fontSize={size}>{about(steering)}</text>}
      </>;
    }}
  </Graph>;
}
