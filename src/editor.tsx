import { PointerEvent, useEffect, useRef, useState } from "react";
import { Focusable, GamepadButton, GamepadEvent } from "@decky/ui";
import { response } from "./headroom";
import { bandHz, dead, flatPair, level, pairs, shown, withBand, withPairGain } from "./pairs";
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
const isDirection = (button: number) => button >= GamepadButton.DIR_UP && button <= GamepadButton.DIR_RIGHT;

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

/** viewBox geometry: fixed in the QAM; in full screen as wide as the space it gets, text at 16 px (like Companion's fan curve).
 *  `reading` is the size of the line that reads the band: in the QAM 12 px, the panel's small text, on its 263 px graph. */
export const graphLayout = (fullscreen: boolean, width = 800, height = 300) => {
  const view = fullscreen ? { width: Math.max(600, Math.round(width / height * 300)), height: 300 } : { width: 300, height: 180 };
  const scale = fullscreen ? view.width / width : 1, font = fullscreen ? 16 * scale : 11, reading = fullscreen ? font : 12 * 300 / 263;
  return { ...view, scale, font, reading, left: font * 2.4, right: font * 0.8, top: font * 1.8, bottom: view.height - font * 1.6 };
};

/** The curve with one handle per band (or pair with five sliders): drag a dot up or down, or press A and use the gamepad. */
export function CurveGraph({ eq, five, disabled, fullscreen = false, selected, select, edit }: {
  eq: EQ; five: boolean; disabled: boolean; fullscreen?: boolean;
  selected: number; select(index: number): void; edit(next: EQ): void;
}) {
  const [steering, setSteering] = useState(false);
  // The finger moves the dot by as much as it moves, from where it touched: touching a dot never makes it jump.
  const drag = useRef<{ index: number; pointer: number; offset: number } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [frame, setFrame] = useState({ width: 800, height: 300 });
  useEffect(() => {
    const element = svg.current;
    if (!fullscreen || !element) return;
    const update = () => {
      const r = element.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setFrame(p => Math.abs(p.width - r.width) < 0.5 && Math.abs(p.height - r.height) < 0.5 ? p : { width: r.width, height: r.height });
    };
    const Observer = element.ownerDocument.defaultView?.ResizeObserver, observer = Observer ? new Observer(update) : null;
    observer?.observe(element);
    update();
    return () => observer?.disconnect();
  }, [fullscreen]);
  const L = graphLayout(fullscreen, frame.width, frame.height);
  const x = (hz: number) => L.left + Math.log(clamp(hz, fMin, fMax) / fMin) / Math.log(fMax / fMin) * (L.width - L.left - L.right);
  const y = (gain: number) => L.top + (RANGE - clamp(gain, -RANGE, RANGE)) / (2 * RANGE) * (L.bottom - L.top);
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
  const consume = (evt: GamepadEvent) => { evt.preventDefault(); evt.stopPropagation(); };
  const enter = (evt: GamepadEvent) => { if (disabled) return; consume(evt); setSteering(true); };
  const onDirection = (evt: GamepadEvent) => {
    if (!isDirection(evt.detail.button) || disabled) return;
    consume(evt);  // Every direction stays in the graph while editing, so focus never wanders off.
    apply(evt.detail.button);
  };
  const onButton = (evt: GamepadEvent) => {
    const button = evt.detail.button;
    if (isDirection(button) || disabled || !apply(button)) return;  // Directions arrive through onGamepadDirection.
    consume(evt);
  };
  /** The gain at the pointer's height; up and down only: the frequency stays. */
  const pointerGain = (evt: PointerEvent, box: DOMRect) =>
    RANGE - ((evt.clientY - box.top) / box.height * L.height - L.top) / (L.bottom - L.top) * 2 * RANGE;
  const move = (evt: PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect();
    if (disabled || !box?.height || !drag.current || drag.current.pointer !== evt.pointerId) return;
    setGain(drag.current.index, Math.round(clamp(pointerGain(evt, box) - drag.current.offset, gMin, gMax) * 2) / 2);
  };
  const stop = () => { drag.current = null; };
  const curve = response(eq, SAMPLES).map((g, i) => `${i ? "L" : "M"}${x(SAMPLES[i]).toFixed(1)} ${y(g).toFixed(1)}`).join(" ");
  const band = shown(eq, index), pair = groups?.[index];
  const reading = pair ? [pair.label, db(pair.gain)] : [formatHz(bandHz(eq, index)), db(band.gain)];
  // The QAM at rest shows the reading only; the filter type and Q appear once the user edits, and in full screen.
  // Band numbers only in full screen: the QAM's line must fit its narrow graph.
  const number = fullscreen ? [pair ? `Bands ${2 * index + 1} and ${2 * index + 2}` : `Band ${index + 1}`] : [];
  const about = (pair ? [...number, ...reading] : !fullscreen && !steering ? reading
    : [...number, typeNames[band.type], ...reading, `Q ${band.q.toFixed(1)}`]).join(" · ");
  const hint = !steering ? "Drag a dot or press A to edit"
    : groups ? "←→ pair · ↑↓ gain · X flat · B done"
    : tools ? "←→ band · ↑↓ gain · LT/RT Q · Y type · X flat · B done"
    : "←→ band · ↑↓ gain · X flat · B done";
  const legend = !steering ? {} : groups ? { [GamepadButton.SECONDARY]: "Flat pair" }
    : !tools ? { [GamepadButton.SECONDARY]: "Flat band" }
    : { [GamepadButton.TRIGGER_LEFT]: "Wider", [GamepadButton.TRIGGER_RIGHT]: "Narrower", [GamepadButton.SECONDARY]: "Flat band",
      [GamepadButton.OPTIONS]: "Filter type" };
  // Only A starts editing: a tap or drag (Steam's onActivate) must not. The empty onActivate keeps the graph focusable.
  return <Focusable className="deq-graph" onActivate={() => {}} onOKButton={enter} onCancelButton={steering ? evt => { consume(evt); setSteering(false); } : undefined}
    // Only while editing: Steam keeps the focus on any element that handles directions (seen on the Legion).
    onButtonDown={steering ? onButton : undefined} onGamepadDirection={steering ? onDirection : undefined} onGamepadBlur={() => setSteering(false)}
    onOKActionDescription={steering ? undefined : "Edit curve"} onCancelActionDescription={steering ? "Done" : undefined}
    actionDescriptionMap={legend} focusClassName="deq-graph-focused"
    style={{ border: `2px solid ${steering ? "#73e3c0" : "#ffffff24"}`, borderRadius: 8, width: "100%", boxSizing: "border-box",
      background: "#111923", color: "#91a1b2", ...(fullscreen ? { height: "100%", display: "flex", flexDirection: "column" } : {}) }}>
    <style>{".deq-graph-focused{outline:2px solid #fff;outline-offset:2px}"}</style>
    <div style={{ padding: "6px 10px", fontSize: fullscreen ? 16 : 12, color: steering ? "#73e3c0" : "#dcdedf" }}>{hint}</div>
    <svg ref={svg} viewBox={`0 0 ${L.width} ${L.height}`} role="img"
      aria-label="Equalizer curve. Drag a dot up or down, or press A and use the D-pad."
      style={{ display: "block", width: "100%", touchAction: "none", ...(fullscreen ? { flex: 1, minHeight: 0 } : {}) }}
      onPointerMove={move} onPointerUp={evt => { stop(); svg.current?.releasePointerCapture?.(evt.pointerId); }}
      onPointerCancel={stop} onLostPointerCapture={stop}>
      {[-12, -6, 0, 6, 12].map(g => <g key={g}>
        <line x1={L.left} x2={L.width - L.right} y1={y(g)} y2={y(g)} stroke={g === 0 ? "#ffffff50" : "#ffffff14"} />
        <text x={L.left - L.font * 0.4} y={y(g) + L.font * 0.35} textAnchor="end" fill="currentColor" fontSize={L.font}>{g > 0 ? `+${g}` : g}</text>
      </g>)}
      {fullscreen && <>
        <line x1={L.left} x2={L.width - L.right} y1={y(limits.limiter_allowance)} y2={y(limits.limiter_allowance)} stroke="#73e3c060" strokeDasharray="6 6" />
        <text x={L.width - L.right} y={y(limits.limiter_allowance) - L.font * 0.4} textAnchor="end" fill="#73e3c0" fontSize={L.font * 0.85}>
          Boosts above +{limits.limiter_allowance} dB lower the level</text>
      </>}
      {TICKS[fullscreen ? "full" : "qam"].map(f => <text key={f} x={x(f)} y={L.height - L.font * 0.4} textAnchor="middle" fill="currentColor"
        fontSize={L.font}>{f >= 1000 ? `${f / 1000}k` : f}</text>)}
      <path d={curve} fill="none" stroke="#73e3c0" strokeWidth={2.5 * L.scale} />
      {handles.map(h => <g key={h.i} style={{ cursor: "ns-resize" }} onPointerDown={evt => {
        evt.preventDefault();
        pick(h.i);
        const box = svg.current?.getBoundingClientRect();
        if (disabled || !box?.height) return;
        drag.current = { index: h.i, pointer: evt.pointerId, offset: pointerGain(evt, box) - h.gain };
        svg.current?.setPointerCapture?.(evt.pointerId);
      }}>
        <circle className="deq-hit" cx={x(h.frequency)} cy={y(h.gain)} r={(fullscreen ? 22 : 16) * L.scale} fill="transparent" />
        <circle cx={x(h.frequency)} cy={y(h.gain)} r={(h.i === index ? 7 : 4.5) * L.scale} fill={h.i === index ? "#ffffff" : "#111923"}
          stroke="#73e3c0" strokeWidth={2 * L.scale} />
      </g>)}
      {handles.length > 0 && <text x={L.left} y={L.reading * 1.1} fill="#eef2f7" fontSize={L.reading}>{about}</text>}
    </svg>
  </Focusable>;
}
