import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GamepadButton } from "../preview/decky-ui";
import { CurveGraph, graphLayout, padChange } from "../src/editor";
import { peakGain, response } from "../src/headroom";
import { flatPair, withPairGain } from "../src/pairs";
import { Band, EQ, flatEQ, limits } from "../src/types";
import goS from "../presets/legion-go-s-original.json";

const bell: Band = { type: "peaking", frequency: 1000, gain: 0, q: 1 };
afterEach(cleanup);
// jsdom has no PointerEvent; a MouseEvent with an id is what the graph reads.
window.PointerEvent ??= class extends MouseEvent {
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 0; }
} as typeof PointerEvent;

it("moves a band's gain, width and type within the backend's limits, never its frequency", () => {
  expect(padChange({ ...bell, gain: 12 }, GamepadButton.DIR_UP)).toEqual({ gain: 12 });
  expect(padChange({ ...bell, gain: -11.75 }, GamepadButton.DIR_DOWN)).toEqual({ gain: -12 });
  expect(padChange({ ...bell, q: limits.q[1] }, GamepadButton.TRIGGER_RIGHT)).toEqual({ q: limits.q[1] });
  expect(padChange({ ...bell, gain: 4 }, GamepadButton.SECONDARY)).toEqual({ gain: 0 });
  expect(padChange({ ...bell, q: 4 }, GamepadButton.OPTIONS)).toEqual({ type: "lowshelf", q: limits.shelf_q_max });
  expect(padChange({ ...bell, type: "highshelf" }, GamepadButton.OPTIONS)).toEqual({ type: "peaking", q: 1 });
  expect(padChange(bell, GamepadButton.DIR_RIGHT)).toBeUndefined();  // Left and right choose the band instead.
  expect(padChange(bell, GamepadButton.OK)).toBeUndefined();  // A enters editing.
});

it("draws the curve the headroom is computed from", () => {
  const eq = flatEQ();
  eq.bands[3] = { ...eq.bands[3], gain: 9 };
  eq.bass = 4;
  const hz = Array.from({ length: 400 }, (_, i) => 20 * 1000 ** (i / 399));
  const curve = response(eq, hz);
  expect(Math.max(...curve)).toBeLessThanOrEqual(peakGain(eq) + 1e-6);
  expect(Math.max(...curve)).toBeGreaterThan(peakGain(eq) - 0.2);
  expect(response(flatEQ(), [100, 1000])).toEqual([0, 0]);
});

const goSEQ = () => ({ width: 1, limiter: true, ...goS.preset.eq }) as EQ;
function graph({ eq = flatEQ(), five = false, disabled = false, fullscreen = false, selected = 0 } = {}) {
  const edit = vi.fn(), select = vi.fn();
  const { container } = render(<CurveGraph eq={eq} five={five} disabled={disabled} fullscreen={fullscreen} selected={selected} select={select} edit={edit} />);
  const root = container.querySelector(".deq-graph") as HTMLElement;
  /** Presses a gamepad button (see preview/decky-ui.tsx); true when the graph let it through to Steam. */
  const press = (key: string) => fireEvent.keyDown(root, { key });
  const last = () => edit.mock.lastCall?.[0] as EQ;
  const hits = () => Array.from(container.querySelectorAll(".deq-hit")).map(c => [Number(c.getAttribute("cx")), Number(c.getAttribute("cy"))]);
  return { edit, select, root, press, last, container, hits };
}
// The QAM graph's axes, worked out independently of the editor: log frequency, gain upwards.
const L = graphLayout(false);
const x = (hz: number) => L.left + Math.log(hz / 20) / Math.log(1000) * (L.width - L.left - L.right);
const y = (gain: number) => L.top + (15 - gain) / 30 * (L.bottom - L.top);
const withGain = (i: number, gain: number) => { const eq = flatEQ(); eq.bands[i] = { ...eq.bands[i], gain }; return eq; };

it("edits only after A, leaving with B or when focus moves on", () => {
  const { edit, select, root, press, last } = graph({ eq: withGain(0, 3) });
  expect(press("ArrowUp")).toBe(true);  // Not editing: the D-pad moves through the panel.
  expect(edit).not.toHaveBeenCalled();
  press("Enter");
  expect(screen.getByText("←→ band · ↑↓ gain · X flat · B done")).toBeTruthy();  // Short: one line in the QAM.
  expect(press("ArrowUp")).toBe(false);
  expect(last().bands[0].gain).toBe(3.5);
  edit.mockClear();
  expect(press("ArrowRight")).toBe(false);
  expect(select).toHaveBeenLastCalledWith(1);  // Right picks the next band ...
  press("ArrowLeft");
  expect(select).toHaveBeenLastCalledWith(9);  // ... and left wraps around.
  expect(edit).not.toHaveBeenCalled();  // Neither moves a frequency.
  expect(press("c")).toBe(true);  // Width and type are full-screen tools.
  expect(press("y")).toBe(true);
  press("x");
  expect(last().bands[0].gain).toBe(0);
  expect(press("Escape")).toBe(false);  // B finishes editing first ...
  expect(screen.getByText("Drag a dot or press A to edit")).toBeTruthy();
  expect(press("Escape")).toBe(true);  // ... and only then goes back.
  press("Enter");
  fireEvent.blur(root);
  expect(screen.getByText("Drag a dot or press A to edit")).toBeTruthy();
});

it("never starts gamepad editing from a tap or a drag, only from A", () => {
  const { root, press, container } = graph();
  fireEvent.click(root);  // Steam activates a tapped element.
  fireEvent.pointerDown(container.querySelectorAll(".deq-hit")[2], { pointerId: 1 });
  fireEvent.click(container.querySelectorAll(".deq-hit")[2]);
  expect(screen.getByText("Drag a dot or press A to edit")).toBeTruthy();
  expect(press("ArrowDown")).toBe(true);  // The D-pad still moves through the panel.
  press("Enter");
  expect(screen.getByText("←→ band · ↑↓ gain · X flat · B done")).toBeTruthy();
});

it("changes width and filter type in full screen", () => {
  const { press, last, root } = graph({ eq: withGain(0, 3), fullscreen: true });
  press("Enter");
  expect(screen.getByText("←→ band · ↑↓ gain · LT/RT Q · Y type · X flat · B done")).toBeTruthy();
  expect(JSON.parse(root.dataset.legend!)).toEqual({ [GamepadButton.TRIGGER_LEFT]: "Wider", [GamepadButton.TRIGGER_RIGHT]: "Narrower",
    [GamepadButton.SECONDARY]: "Flat band", [GamepadButton.OPTIONS]: "Filter type" });  // Steam's button hints.
  press("c");
  expect(last().bands[0].q).toBe(1.5);  // RT: narrower.
  press("z");
  expect(last().bands[0].q).toBe(1.3);  // LT: wider.
  press("y");
  expect(last().bands[0].type).toBe("lowshelf");
});

it("edits nothing when a band that does nothing is left flat, so the preset is not marked edited", () => {
  const eq = goSEQ();  // Band 2 is the 0 Hz placeholder, shown as a flat 63 Hz band.
  const full = graph({ eq, fullscreen: true, selected: 1 });
  full.press("Enter");
  for (const key of ["x", "z", "c", "y"]) full.press(key);
  expect(full.edit).not.toHaveBeenCalled();
  full.press("ArrowUp");
  expect(full.last().bands[1]).toEqual({ type: "peaking", frequency: 63, q: limits.flat_q, gain: 0.5 });  // A gain makes it real.
  cleanup();
  const five = graph({ eq, five: true, selected: 8 });  // The 48 kHz placeholder and a flat 16 kHz band.
  five.press("Enter");
  five.press("x");
  expect(five.edit).not.toHaveBeenCalled();
});

it("changes only the gain of a shelf that acts as a level, as any other type there would silence it", () => {
  const eq = withGain(9, 4);
  eq.bands[9] = { ...eq.bands[9], type: "lowshelf", frequency: 48000, q: 1 };
  const { press, edit, last, root } = graph({ eq, fullscreen: true, selected: 9 });
  expect(screen.getByText(/^Band 10 · Low shelf · 48 kHz · \+4\.0 dB/)).toBeTruthy();  // Its own name, not 16 kHz.
  press("Enter");
  expect(screen.getByText("←→ band · ↑↓ gain · X flat · B done")).toBeTruthy();
  expect(JSON.parse(root.dataset.legend!)).toEqual({ [GamepadButton.SECONDARY]: "Flat band" });
  for (const key of ["y", "z", "c"]) expect(press(key)).toBe(true);
  expect(edit).not.toHaveBeenCalled();
  press("ArrowUp");
  expect(last().bands[9]).toEqual({ ...eq.bands[9], gain: 4.5 });
});

it("shows every preset's ten bands (or five pairs), Go S placeholders as flat bands", () => {
  const eq = goSEQ();
  const { press, select, hits } = graph({ eq });
  const dots = hits();
  expect(dots).toHaveLength(10);
  expect(screen.getByText("31.5 Hz · -10.0 dB")).toBeTruthy();  // The usual name, not the file's 31.25 Hz.
  // Each dot sits on its band, gains upwards; the 0 Hz, 40 kHz and 48 kHz placeholders flat at 63 Hz, 4 kHz and 8 kHz.
  const expected = [[31.25, -10], [63, 0], [125, 5], [250, 5], [500, -5], [1000, -5], [2000, -5], [4000, 0], [8000, 0], [16000, 0]];
  dots.forEach(([cx, cy], i) => { expect(cx).toBeCloseTo(x(expected[i][0]), 6); expect(cy).toBeCloseTo(y(expected[i][1]), 6); });
  expect(dots[2][1]).toBeLessThan(dots[1][1]);  // +5 dB is above the 0 dB line.
  press("Enter");
  press("ArrowRight");
  expect(select).toHaveBeenLastCalledWith(1);  // The 0 Hz placeholder, shown as a flat 63 Hz band.
  cleanup();
  graph({ eq, selected: 1 });
  expect(screen.getByText("63 Hz · 0.0 dB")).toBeTruthy();
  cleanup();
  expect(graph({ eq, five: true }).hits()).toHaveLength(5);
});

it("keeps the selection on the same bands when ten sliders become five and back", () => {
  const { press, last, select } = graph({ five: true, selected: 7 });  // Band 8 (4 kHz) was chosen with ten sliders ...
  expect(screen.getByText("2 kHz – 4 kHz · 0.0 dB")).toBeTruthy();  // ... so its pair is.
  press("Enter");
  press("ArrowUp");
  expect(last()).toEqual(withPairGain(flatEQ(), 3, 0.5));
  press("ArrowRight");
  expect(select).toHaveBeenLastCalledWith(8);  // Pair 5 selects its first band, 8 kHz, for ten sliders.
  cleanup();
  graph({ five: true, selected: 2 });  // 125 Hz: the second pair, not the third.
  expect(screen.getByText("125 Hz – 250 Hz · 0.0 dB")).toBeTruthy();
});

it("with five sliders moves both bands of a pair by the same step, gain only", () => {
  const eq = goSEQ();
  const { edit, press, last, select } = graph({ eq, five: true });
  press("Enter");
  expect(screen.getByText("←→ pair · ↑↓ gain · X flat · B done")).toBeTruthy();
  press("ArrowUp");  // The pair shows -5: the -10 dB shelf and the flat placeholder.
  expect(last().bands.slice(0, 2)).toEqual([{ type: "lowshelf", frequency: 31.5, gain: -9.5, q: 1 },
    { type: "peaking", frequency: 63, q: limits.flat_q, gain: 0.5 }]);  // Each moves by 0.5 dB; the placeholder becomes a band.
  expect(last().bands.slice(2)).toEqual(eq.bands.slice(2));
  edit.mockClear();
  press("ArrowRight");
  expect(select).toHaveBeenLastCalledWith(2);  // The next pair, by its first band.
  expect(press("c")).toBe(true);  // No width ...
  expect(press("y")).toBe(true);  // ... and no type for a pair.
  expect(edit).not.toHaveBeenCalled();
  press("x");
  expect(last()).toEqual(flatPair(eq, 0));  // Both flat, not two bands that average 0.
  expect(last().bands[0].gain).toBe(0);
});

it("drags a dot up or down only, by as much as the finger moves: the frequency never moves", () => {
  for (const five of [false, true]) {
    const { edit, select, last, container } = graph({ five });
    const svg = container.querySelector("svg")!;
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: L.width, height: L.height }) as DOMRect;
    const touch = y(0) - 12;  // A little above the dot, still on it.
    fireEvent.pointerDown(container.querySelectorAll(".deq-hit")[1], { pointerId: 1, clientX: x(63), clientY: touch });
    expect(select).toHaveBeenLastCalledWith(five ? 2 : 1);
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: x(63), clientY: touch + 0.4 });
    expect(edit).not.toHaveBeenCalled();  // Touching a dot never makes it jump.
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: x(5000), clientY: touch + y(6) - y(0) });  // Far to the side as well.
    const bands = five ? [2, 3] : [1];
    expect(last().bands.map(b => b.gain)).toEqual(flatEQ().bands.map((_, i) => bands.includes(i) ? 6 : 0));
    expect(last().bands.map(b => b.frequency)).toEqual(limits.frequencies);
    fireEvent.pointerUp(svg, { pointerId: 1 });
    edit.mockClear();
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: x(100), clientY: y(0) });
    expect(edit).not.toHaveBeenCalled();
    cleanup();
  }
});

it("changes nothing while the panel is locked", () => {
  const { edit, press, container } = graph({ disabled: true });
  press("Enter");
  press("ArrowUp");
  fireEvent.pointerDown(container.querySelector(".deq-hit")!, { pointerId: 1 });
  fireEvent.pointerMove(container.querySelector("svg")!, { pointerId: 1, clientX: 100, clientY: 20 });
  expect(edit).not.toHaveBeenCalled();
});

it("reads only the frequency and gain at rest in the QAM, the full line while editing and in full screen", () => {
  const eq = flatEQ();
  eq.bands[0] = { ...eq.bands[0], gain: 12 };
  const { press } = graph({ eq });
  expect(screen.getByText("31.5 Hz · +12.0 dB")).toBeTruthy();
  press("Enter");
  expect(screen.getByText(/^Bell · 31\.5 Hz · \+12\.0 dB · Q \d/)).toBeTruthy();  // No band number: the line fits the QAM.
  press("Escape");
  expect(screen.getByText("31.5 Hz · +12.0 dB")).toBeTruthy();
  cleanup();
  const full = graph({ eq, fullscreen: true });
  expect(screen.getByText(/^Band 1 · Bell · 31\.5 Hz · \+12\.0 dB · Q \d/)).toBeTruthy();
  full.press("Enter");
  expect(screen.getByText("←→ band · ↑↓ gain · LT/RT Q · Y type · X flat · B done")).toBeTruthy();
  cleanup();
  const paired = graph({ eq: withPairGain(eq, 0, 6), five: true });
  expect(screen.getByText("31.5 Hz – 63 Hz · +6.0 dB")).toBeTruthy();
  paired.press("Enter");
  expect(screen.getByText("31.5 Hz – 63 Hz · +6.0 dB")).toBeTruthy();
  cleanup();
  graph({ eq: withPairGain(eq, 0, 6), five: true, fullscreen: true });
  expect(screen.getByText("Bands 1 and 2 · 31.5 Hz – 63 Hz · +6.0 dB")).toBeTruthy();
});

it("reads the band in the QAM at the panel's small text size, 12 px on its 263 px graph; full screen at 16 px", () => {
  const eq = flatEQ();
  eq.bands[9] = { type: "highshelf", frequency: 16000, gain: -12, q: 1.4 };
  const { press, container } = graph({ eq, selected: 9 });
  press("Enter");
  const reading = screen.getByText("High shelf · 16 kHz · -12.0 dB · Q 1.4");  // The longest line in the QAM.
  expect(Number(reading.getAttribute("font-size")) * 263 / 300).toBeCloseTo(12, 6);
  expect(Number(container.querySelector("svg text")!.getAttribute("font-size"))).toBe(11);  // The axes stay as they were.
  cleanup();
  graph({ eq, selected: 9, fullscreen: true });
  expect(Number(screen.getByText(/^Band 10 · /).getAttribute("font-size"))).toBe(graphLayout(true).font);
});

it("shows a band far from its usual frequency where it plays, and edits its gain there", () => {
  const eq = flatEQ();
  eq.bands[2] = { ...eq.bands[2], frequency: 3000, gain: 6 };  // A DeckyEQ 1.0 preset moved band 3 from 125 Hz.
  const { press, last, hits } = graph({ eq, selected: 2 });
  expect(screen.getByText("3 kHz · +6.0 dB")).toBeTruthy();
  expect(hits()[2][0]).toBeCloseTo(x(3000), 6);  // The dot and the reading agree.
  press("Enter");
  press("ArrowUp");
  expect(last().bands[2]).toEqual({ ...eq.bands[2], gain: 6.5 });  // Still at 3 kHz.
});

it("offers width and type for a placeholder only once it has a gain", () => {
  const eq = goSEQ();  // Band 2 is the 0 Hz placeholder, shown as a flat 63 Hz band.
  const edit = vi.fn();
  const view = (bands: EQ) => <CurveGraph fullscreen eq={bands} five={false} disabled={false} selected={1} select={() => {}} edit={edit} />;
  const { container, rerender } = render(view(eq));
  const root = container.querySelector(".deq-graph") as HTMLElement;
  fireEvent.keyDown(root, { key: "Enter" });
  expect(screen.getByText("←→ band · ↑↓ gain · X flat · B done")).toBeTruthy();
  expect(JSON.parse(root.dataset.legend!)).toEqual({ [GamepadButton.SECONDARY]: "Flat band" });
  fireEvent.keyDown(root, { key: "ArrowUp" });
  rerender(view(edit.mock.lastCall![0]));
  expect(screen.getByText("←→ band · ↑↓ gain · LT/RT Q · Y type · X flat · B done")).toBeTruthy();
});

it("has no width or type for a pair in full screen either", () => {
  const { press, edit } = graph({ eq: withGain(0, 3), five: true, fullscreen: true });
  press("Enter");
  expect(screen.getByText("←→ pair · ↑↓ gain · X flat · B done")).toBeTruthy();
  for (const key of ["z", "c", "y"]) expect(press(key)).toBe(true);  // Left to Steam.
  expect(edit).not.toHaveBeenCalled();
});

it("draws the curve across the whole 20 Hz–20 kHz axis", () => {
  const { container } = graph({ eq: withGain(9, 6) });
  const points = container.querySelector("path")!.getAttribute("d")!.match(/[\d.]+ [\d.]+/g)!.map(p => p.split(" ").map(Number));
  expect(points[0][0]).toBeCloseTo(L.left, 1);
  expect(points.at(-1)![0]).toBeCloseTo(L.width - L.right, 1);
  expect(Math.min(...points.map(p => p[1]))).toBeCloseTo(y(6), 0);  // The 16 kHz boost is drawn, at its height.
});
