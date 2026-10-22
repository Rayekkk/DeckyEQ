import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { Controller } from "../src/controller";
import { PreviewEngine } from "../preview/engine";
import { flatEQ } from "../src/types";

const controllers: Controller[] = [];
afterEach(() => { cleanup(); controllers.splice(0).forEach(c => c.dispose()); document.querySelectorAll(".preview-modal-host").forEach(e => e.remove()); });
async function setup(engine = new PreviewEngine()) {
  const perform = vi.spyOn(engine, "perform");
  const controller = new Controller(engine); controllers.push(controller);
  await controller.refresh();
  const files = { importFile: vi.fn(async () => "/home/deck/test.json"), exportFolder: vi.fn(async () => "/home/deck/Documents") };
  const rendered = render(<App controller={controller} files={files} />);
  return { controller, engine, perform, files, rendered };
}

it("offers ten bands plus independent bass and treble", async () => {
  await setup();
  expect(screen.getAllByRole("slider")).toHaveLength(12);
  expect(screen.getByRole("slider", { name: "Bass · below 120 Hz" })).toBeTruthy();
  expect(screen.getByRole("slider", { name: "Treble · above 6 kHz" })).toBeTruthy();
});

it("saves the most recent tone adjustment as a named preset", async () => {
  const { engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "4" } });
  fireEvent.change(screen.getByRole("combobox", { name: "View" }), { target: { value: "presets" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Preset name" }), { target: { value: "My speakers" } });
  fireEvent.click(screen.getByRole("button", { name: "Save as new preset" }));
  await waitFor(() => expect(engine.value.presets.at(-1)?.name).toBe("My speakers"));
  expect(engine.value.presets.at(-1)?.eq.bass).toBe(4);
});

it("confirms before replacing an edited draft", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "test", name: "Saved", eq: flatEQ() });
  const { perform } = await setup(engine);
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "2" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Current preset" }), { target: { value: "test" } });
  await screen.findByRole("dialog", { name: "Switch preset?" });
  expect(perform.mock.calls.some(call => call[0] === "select")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(engine.value.state.selected).toBe("flat");
});

it("imports from the Decky file picker and exports through backend", async () => {
  const { files, perform } = await setup();
  fireEvent.change(screen.getByRole("combobox", { name: "View" }), { target: { value: "presets" } });
  fireEvent.click(screen.getByRole("button", { name: "Import preset or bank…" }));
  await waitFor(() => expect(perform.mock.calls.some(call => call[0] === "import")).toBe(true));
  expect(files.importFile).toHaveBeenCalledWith("/home/deck");
  await waitFor(() => expect((screen.getByRole("button", { name: "Export current sound" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Export current sound" }));
  await screen.findByRole("status");
  expect(screen.getByRole("status").textContent).toContain("/home/deck/Documents/DeckyEQ/");
});

it("retains a pending edit across QAM unmount and remount", async () => {
  const { controller, files, rendered, engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Treble · above 6 kHz" }), { target: { value: "3" } });
  rendered.unmount();
  await act(async () => { await controller.flush(); });
  render(<App controller={controller} files={files} />);
  expect((screen.getByRole("slider", { name: "Treble · above 6 kHz" }) as HTMLInputElement).value).toBe("3");
  expect(engine.value.state.working.treble).toBe(3);
});

it("supports editing presets when audio is unavailable", async () => {
  const engine = new PreviewEngine(); engine.value.audio.supported = false;
  await setup(engine);
  expect((screen.getByRole("switch", { name: "Enable EQ" }) as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole("slider", { name: "Bass · below 120 Hz" }) as HTMLInputElement).disabled).toBe(false);
});

it("selects the original Go S preset including shelves and positive input level", async () => {
  const { engine } = await setup();
  fireEvent.change(screen.getByRole("combobox", { name: "Current preset" }), { target: { value: "go-s-original" } });
  await screen.findByRole("slider", { name: "1 · 31.25 Hz · Low shelf" });
  expect(engine.value.state.working.bands[5].type).toBe("highshelf");
  fireEvent.change(screen.getByRole("combobox", { name: "View" }), { target: { value: "output" } });
  expect((screen.getByRole("slider", { name: "Input level" }) as HTMLInputElement).value).toBe("8");
  expect((screen.getByRole("switch", { name: "Automatic headroom" }) as HTMLInputElement).checked).toBe(false);
  fireEvent.change(screen.getByRole("combobox", { name: "View" }), { target: { value: "presets" } });
  expect((screen.getByRole("button", { name: "Delete selected preset" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole("textbox", { name: "Preset name" }), { target: { value: "My Go S" } });
  fireEvent.click(screen.getByRole("button", { name: "Save as new preset" }));
  await waitFor(() => expect(engine.value.presets.at(-1)?.name).toBe("My Go S"));
  expect(engine.value.presets.at(-1)?.eq).toEqual(engine.value.presets[1].eq);
});

it("edits filter type and exact frequency live, snapping unstable ranges", async () => {
  const { controller, engine } = await setup();
  fireEvent.click(screen.getByRole("switch", { name: "Edit filter type, frequency & width" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Filter type" }), { target: { value: "highshelf" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "31,25" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply exact frequency" }));
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0]).toMatchObject({type: "highshelf", frequency: 31.25});
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply exact frequency" }));
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0].frequency).toBe(0);
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply exact frequency" }));
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0].frequency).toBe(20);
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "23999" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply exact frequency" }));
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0].frequency).toBe(20000);
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "48001" } });
  expect((screen.getByRole("button", { name: "Apply exact frequency" }) as HTMLButtonElement).disabled).toBe(true);
});

it("keeps a failed edit visible instead of exporting around it", async () => {
  const { files, perform, engine } = await setup();
  perform.mockImplementationOnce(async () => ({ ok: false, error: "Simulated failure", state: await engine.status() }));
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "3" } });
  fireEvent.change(screen.getByRole("combobox", { name: "View" }), { target: { value: "presets" } });
  fireEvent.click(screen.getByRole("button", { name: "Export current sound to folder…" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Simulated failure");
  expect(files.exportFolder).not.toHaveBeenCalled();
});

it("opens the file picker again after an earlier, unrelated error", async () => {
  const { controller, files } = await setup();
  act(() => controller.reportError("Earlier rename failed"));
  fireEvent.change(screen.getByRole("combobox", { name: "View" }), { target: { value: "presets" } });
  fireEvent.click(screen.getByRole("button", { name: "Import preset or bank…" }));
  await waitFor(() => expect(files.importFile).toHaveBeenCalled());
});
