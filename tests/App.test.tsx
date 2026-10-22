import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { Controller } from "../src/controller";
import { PreviewEngine } from "../preview/engine";
import { flatEQ } from "../src/types";

const controllers: Controller[] = [];
afterEach(() => { cleanup(); controllers.splice(0).forEach(c => c.dispose()); document.querySelectorAll(".preview-modal-host").forEach(e => e.remove()); });
async function setup(engine = new PreviewEngine(), toast?: (body: string) => void) {
  const perform = vi.spyOn(engine, "perform");
  const controller = new Controller(engine); controllers.push(controller);
  await controller.refresh();
  const files = { importFile: vi.fn(async () => "/home/deck/test.json"), exportFolder: vi.fn(async () => "/home/deck/Documents") };
  const rendered = render(<App controller={controller} files={files} toast={toast} />);
  return { controller, engine, perform, files, rendered };
}
/** Opens a page from its row on the main screen (the row's name starts with the page title). */
const open = (title: string) => fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${title}`) }));
const back = (to = "DeckyEQ") => fireEvent.click(screen.getByRole("button", { name: `‹ ${to}` }));
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
async function nameDialog(title: string, name: string, ok: string) {
  const dialog = await screen.findByRole("dialog", { name: title });
  fireEvent.change(dialog.querySelector("input")!, { target: { value: name } });
  fireEvent.click(screen.getByRole("button", { name: ok }));
}

it("keeps everyday controls on the main screen and the rest one row away", async () => {
  await setup();
  expect(screen.getByRole("switch", { name: "Enable EQ" })).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Preset" })).toBeTruthy();
  expect(screen.getAllByRole("slider")).toHaveLength(4);
  expect(screen.getByRole("slider", { name: "Stereo width" })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Equalizer bands\s*10 bands · flat/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Presets\s*2 presets/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Output and level\s*Legion Go 2 speakers · full level/ })).toBeTruthy();
  open("Equalizer bands");
  expect(screen.getAllByRole("slider")).toHaveLength(10);
  expect(screen.queryByRole("switch", { name: "Enable EQ" })).toBeNull();
  back();
  expect(screen.getAllByRole("slider")).toHaveLength(4);
});

it("boosts the volume for every preset without marking it edited", async () => {
  const { controller, engine, perform } = await setup();
  expect(screen.getByText("For every preset; takes effect when EQ is on.")).toBeTruthy();
  fireEvent.click(screen.getByRole("switch", { name: "Enable EQ" }));
  await screen.findByText(/Louder than SteamOS allows, for every preset/);
  fireEvent.change(screen.getByRole("slider", { name: "Volume boost" }), { target: { value: "6" } });
  expect(screen.getByText("For every preset; the limiter keeps it from clipping.")).toBeTruthy();  // Before the backend answers.
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.boost).toBe(6);
  expect(perform.mock.calls.at(-1)?.[1]).toMatchObject({ boost: 6 });
  expect(screen.queryByText("Flat · edited")).toBeNull();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "2" } });
  await act(async () => { await controller.flush(); });
  expect(perform.mock.calls.at(-1)?.[1]).not.toHaveProperty("boost");  // Only sent when moved.
  expect(engine.value.state.boost).toBe(6);
});

it("sets stereo width live, without lowering the level", async () => {
  const { controller, engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Stereo width" }), { target: { value: "200" } });
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.width).toBe(2);
  expect(screen.getByText("Wider than the original mix.")).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Output and level\s*Legion Go 2 speakers · full level/ })).toBeTruthy();
});

it("summarises adjusted bands and unsaved changes on the main rows", async () => {
  const { controller } = await setup();
  open("Equalizer bands");
  fireEvent.change(screen.getByRole("slider", { name: "63 Hz" }), { target: { value: "3" } });
  await act(async () => { await controller.flush(); });
  back();
  expect(screen.getByRole("button", { name: /^Equalizer bands\s*10 bands · 1 adjusted/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Presets\s*Flat · unsaved changes/ })).toBeTruthy();
  expect(screen.getByText("Flat · edited")).toBeTruthy();
});

it("saves the most recent tone adjustment as a named preset", async () => {
  const { engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "4" } });
  open("Presets");
  fireEvent.click(button("Save as new…"));
  await nameDialog("Save as new preset", "My speakers", "Save");
  await waitFor(() => expect(engine.value.presets.at(-1)?.name).toBe("My speakers"));
  expect(engine.value.presets.at(-1)?.eq.bass).toBe(4);
  await waitFor(() => expect((screen.getByRole("combobox", { name: "Preset" }) as HTMLSelectElement).selectedOptions[0].text).toBe("My speakers"));
});

it("renames, saves and deletes only user presets", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() });
  const { perform } = await setup(engine);
  open("Presets");
  expect(screen.queryByRole("button", { name: "Rename…" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete…" })).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "Preset" }), { target: { value: "mine" } });
  await waitFor(() => expect(screen.getByRole("heading", { name: "Mine" })).toBeTruthy());
  fireEvent.click(button("Rename…"));
  const dialog = await screen.findByRole("dialog", { name: "Rename preset" });
  expect((dialog.querySelector("input") as HTMLInputElement).value).toBe("Mine");
  fireEvent.change(dialog.querySelector("input")!, { target: { value: "   " } });
  expect(button("Rename").disabled).toBe(true);
  fireEvent.change(dialog.querySelector("input")!, { target: { value: "Ours" } });
  fireEvent.click(button("Rename"));
  await waitFor(() => expect(engine.value.presets.at(-1)?.name).toBe("Ours"));
  fireEvent.click(button("Delete…"));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await waitFor(() => expect(perform.mock.calls.some(call => call[0] === "delete")).toBe(true));
  expect(engine.value.state.selected).toBe("flat");
});

it("offers Save changes for an edited user preset and Discard for any edit", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() });
  engine.value.state.selected = "mine";
  const { controller } = await setup(engine);
  fireEvent.change(screen.getByRole("slider", { name: "Treble · above 6 kHz" }), { target: { value: "2" } });
  await act(async () => { await controller.flush(); });
  open("Presets");
  fireEvent.click(button("Save changes"));
  await waitFor(() => expect(engine.value.presets.at(-1)?.eq.treble).toBe(2));
  expect(screen.queryByRole("button", { name: "Discard changes" })).toBeNull();
});

it("confirms before replacing an edited draft", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "test", name: "Saved", eq: flatEQ() });
  const { perform } = await setup(engine);
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "2" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Preset" }), { target: { value: "test" } });
  await screen.findByRole("dialog", { name: "Switch preset?" });
  expect(perform.mock.calls.some(call => call[0] === "select")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(engine.value.state.selected).toBe("flat");
});

it("imports from the Decky file picker and exports this sound or the library", async () => {
  const { files, perform } = await setup();
  open("Presets");
  fireEvent.click(button("Import preset…"));
  await waitFor(() => expect(perform.mock.calls.some(call => call[0] === "import")).toBe(true));
  expect(files.importFile).toHaveBeenCalledWith("/home/deck");
  await waitFor(() => expect(button("Export this sound").disabled).toBe(false));
  fireEvent.click(button("Export this sound"));
  expect((await screen.findByRole("status")).textContent).toContain("/home/deck/Documents/DeckyEQ/");
  await waitFor(() => expect(button("Export all presets").disabled).toBe(false));
  fireEvent.click(button("Export all presets"));
  await waitFor(() => expect(perform.mock.calls.filter(call => call[0] === "export").map(call => call[1].all)).toEqual([false, true]));
});

it("exports to another folder from the details", async () => {
  const { files, perform } = await setup();
  open("Presets");
  fireEvent.click(button("Show details"));
  expect(screen.getByText("/home/deck/Documents/DeckyEQ")).toBeTruthy();
  fireEvent.click(button("Export this sound to another folder…"));
  await waitFor(() => expect(perform.mock.calls.some(call => call[0] === "export" && call[1].directory === "/home/deck/Documents")).toBe(true));
  expect(files.exportFolder).toHaveBeenCalledWith("/home/deck");
});

it("sends outcomes to a toast once instead of the panel when one is available", async () => {
  const toast = vi.fn();
  const { controller, files, rendered } = await setup(new PreviewEngine(), toast);
  open("Presets");
  fireEvent.click(button("Export this sound"));
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining("Exported to /home/deck/Documents/DeckyEQ/")));
  expect(screen.queryByRole("status")).toBeNull();
  rendered.unmount();
  render(<App controller={controller} files={files} toast={toast} />);
  expect(toast).toHaveBeenCalledOnce();  // A remount must not repeat it.
});

it("toasts a failure of an action confirmed in a dialog, which hides the panel", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() });
  engine.value.state.selected = "mine";
  const toast = vi.fn();
  const { perform } = await setup(engine, toast);
  perform.mockImplementationOnce(async () => ({ ok: false, error: "Disk full", state: await engine.status() }));
  open("Presets");
  fireEvent.click(button("Delete…"));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Disk full"));
});

it("reopens a refused name with what was typed and the reason", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() }, { id: "other", name: "Bass", eq: flatEQ() });
  engine.value.state.selected = "mine";
  const { perform } = await setup(engine);
  perform.mockImplementationOnce(async () => ({ ok: false, error: "A preset already uses that name.", state: await engine.status() }));
  open("Presets");
  fireEvent.click(button("Rename…"));
  await nameDialog("Rename preset", "Bass", "Rename");
  await waitFor(() => expect(screen.getByText("A preset already uses that name.", { selector: ".preview-modal p" })).toBeTruthy());
  expect((screen.getByRole("dialog", { name: "Rename preset" }).querySelector("input") as HTMLInputElement).value).toBe("Bass");
  expect(perform.mock.calls.find(call => call[0] === "rename")?.[1]).toEqual({ name: "Bass", id: "mine" });
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
  fireEvent.change(screen.getByRole("combobox", { name: "Preset" }), { target: { value: "go-s-original" } });
  await waitFor(() => expect(engine.value.state.selected).toBe("go-s-original"));
  open("Equalizer bands");
  await screen.findByRole("slider", { name: "31.25 Hz · Low shelf" });
  expect(engine.value.state.working.bands[5].type).toBe("highshelf");
  back();
  open("Output and level");
  expect((screen.getByRole("slider", { name: "Input level" }) as HTMLInputElement).value).toBe("8");
  expect(screen.queryByRole("switch", { name: "Automatic headroom" })).toBeNull();  // Always on now.
  back();
  open("Presets");
  expect(screen.queryByRole("button", { name: "Delete…" })).toBeNull();
  expect(screen.getByText(/Community speaker preset/)).toBeTruthy();
  fireEvent.click(button("Save as new…"));
  await nameDialog("Save as new preset", "My Go S", "Save");
  await waitFor(() => expect(engine.value.presets.at(-1)?.name).toBe("My Go S"));
  expect(engine.value.presets.at(-1)?.eq).toEqual(engine.value.presets[1].eq);
});

it("resets the whole sound to flat only after confirmation", async () => {
  const { controller, engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "5" } });
  await act(async () => { await controller.flush(); });
  open("Equalizer bands");
  fireEvent.click(button("Reset to flat"));
  fireEvent.click(await screen.findByRole("button", { name: "Reset" }));
  await waitFor(() => expect(engine.value.state.working.bass).toBe(0));
});

async function openBand() {
  open("Equalizer bands");
  fireEvent.click(screen.getByRole("button", { name: /^Edit one band/ }));
  expect(screen.getByRole("button", { name: "‹ Equalizer bands" })).toBeTruthy();
  fireEvent.click(button("Show exact frequency"));
}

it("edits filter type and exact frequency live, snapping unstable ranges", async () => {
  const { controller, engine } = await setup();
  await openBand();
  fireEvent.change(screen.getByRole("combobox", { name: "Filter type" }), { target: { value: "highshelf" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "31,25" } });
  fireEvent.click(button("Apply exact frequency"));
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0]).toMatchObject({type: "highshelf", frequency: 31.25});
  for (const [typed, stored] of [["0", 0], ["5", 20], ["23999", 20000]] as const) {
    fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: typed } });
    fireEvent.click(button("Apply exact frequency"));
    await act(async () => { await controller.flush(); });
    expect(engine.value.state.working.bands[0].frequency).toBe(stored);
  }
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "48001" } });
  expect(button("Apply exact frequency").disabled).toBe(true);
  back("Equalizer bands");
  expect(screen.getByRole("button", { name: "‹ DeckyEQ" })).toBeTruthy();
});

it("edits the gain and width of the chosen band", async () => {
  const { controller, engine } = await setup();
  await openBand();
  fireEvent.change(screen.getByRole("combobox", { name: "Band" }), { target: { value: "4" } });
  fireEvent.change(screen.getByRole("slider", { name: "Gain" }), { target: { value: "-3" } });
  fireEvent.change(screen.getByRole("slider", { name: "Bandwidth (Q)" }), { target: { value: "2" } });
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[4]).toMatchObject({ gain: -3, q: 2 });
});

it("goes back one page with B and returns focus to the row it came from", async () => {
  await setup();
  open("Equalizer bands");
  fireEvent.click(screen.getByRole("button", { name: /^Edit one band/ }));
  fireEvent.keyDown(button("‹ Equalizer bands"), { key: "Escape" });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /^Edit one band/ }));
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /^Equalizer bands/ }));
  expect(screen.getByRole("switch", { name: "Enable EQ" })).toBeTruthy();
});

it("starts the name dialog in its text field", async () => {
  await setup();
  open("Presets");
  fireEvent.click(button("Save as new…"));
  const dialog = await screen.findByRole("dialog", { name: "Save as new preset" });
  expect(document.activeElement).toBe(dialog.querySelector("input"));
});

it("keeps a failed edit visible instead of exporting around it", async () => {
  const { files, perform, engine } = await setup();
  perform.mockImplementationOnce(async () => ({ ok: false, error: "Simulated failure", state: await engine.status() }));
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "3" } });
  open("Presets");
  fireEvent.click(button("Show details"));
  fireEvent.click(button("Export this sound to another folder…"));
  expect((await screen.findByRole("alert")).textContent).toContain("Simulated failure");
  expect(files.exportFolder).not.toHaveBeenCalled();
});

it("opens the file picker again after an earlier, unrelated error", async () => {
  const { controller, files } = await setup();
  open("Presets");
  act(() => controller.reportError("Earlier rename failed"));
  fireEvent.click(button("Import preset…"));
  await waitFor(() => expect(files.importFile).toHaveBeenCalled());
});

it("clears an old message when switching pages and shows the snapped frequency", async () => {
  const { controller, engine } = await setup();
  act(() => controller.reportError("A preset already uses that name."));
  open("Presets");
  expect(screen.queryByRole("alert")).toBeNull();
  back();
  await openBand();
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "20" } });
  fireEvent.click(button("Apply exact frequency"));
  await act(async () => { await controller.flush(); });
  fireEvent.change(screen.getByRole("textbox", { name: "Exact frequency (Hz)" }), { target: { value: "10" } });
  fireEvent.click(button("Apply exact frequency"));
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0].frequency).toBe(20);
  expect((screen.getByRole("textbox", { name: "Exact frequency (Hz)" }) as HTMLInputElement).value).toBe("20");
});

it("moves the D-pad focus to the back button and back to the row it came from", async () => {
  await setup();
  open("Output and level");
  expect(document.activeElement).toBe(button("‹ DeckyEQ"));
  back();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /^Output and level/ }));
});

it("hides the reconnect action until something is wrong", async () => {
  const engine = new PreviewEngine();
  const { controller } = await setup(engine);
  expect(screen.queryByRole("button", { name: "Reconnect EQ" })).toBeNull();
  engine.value.audio.error = "The equalizer stopped.";
  await act(async () => { await controller.refresh(); });  // The mount poll may still be reading.
  await waitFor(async () => { await controller.refresh(); expect(screen.getByRole("button", { name: "Reconnect EQ" })).toBeTruthy(); });
});

it("says Disconnected and locks the controls when the backend stops answering", async () => {
  const engine = new PreviewEngine(); engine.value.state.enabled = true; engine.value.audio.phase = "active";
  const { controller } = await setup(engine);
  vi.spyOn(engine, "status").mockRejectedValue(new Error("backend gone"));
  await waitFor(async () => { await controller.refresh(); expect(screen.getByText("Disconnected")).toBeTruthy(); });
  expect(document.querySelector(".deq-dot.live")).toBeNull();
  expect((screen.getByRole("slider", { name: "Bass · below 120 Hz" }) as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByRole("button", { name: "Reconnect" })).toBeTruthy();
});

it("offers Reconnect when the panel cannot load at all", async () => {
  const engine = new PreviewEngine();
  const failing = vi.spyOn(engine, "status").mockRejectedValue(new Error("backend gone"));
  const controller = new Controller(engine); controllers.push(controller);
  await controller.refresh();
  render(<App controller={controller} files={{ importFile: vi.fn(), exportFolder: vi.fn() }} />);
  await screen.findByText("Could not load DeckyEQ");
  failing.mockRestore();
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await screen.findByRole("switch", { name: "Enable EQ" });
});

it("shows Applying while a change is on its way", async () => {
  const { engine } = await setup();
  let release!: () => void;
  const real = engine.perform.bind(engine);
  vi.spyOn(engine, "perform").mockImplementationOnce(async (...args) => { await new Promise<void>(r => { release = r; }); return real(...args); });
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "2" } });
  expect(screen.getByText("Applying…")).toBeTruthy();
  await waitFor(() => expect(release).toBeTypeOf("function"));
  await act(async () => { release(); });
  await waitFor(() => expect(screen.queryByText("Applying…")).toBeNull());
});

it("changes the output and the speakers-only switch independently", async () => {
  const engine = new PreviewEngine();
  engine.value.audio.outputs.push({ name: "alsa_output.hdmi", description: "HDMI", port: "hdmi-output-0", speaker: false });
  const { perform } = await setup(engine);
  open("Output and level");
  fireEvent.change(screen.getByRole("combobox", { name: "Apply EQ to" }), { target: { value: "alsa_output.hdmi" } });
  await waitFor(() => expect(perform).toHaveBeenCalledWith("output", { target: "alsa_output.hdmi", speakers_only: true }, expect.any(Number)));
  await waitFor(() => expect((screen.getByRole("switch", { name: "Speakers only" }) as HTMLInputElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("switch", { name: "Speakers only" }));
  await waitFor(() => expect(perform).toHaveBeenCalledWith("output", { target: "alsa_output.hdmi", speakers_only: false }, expect.any(Number)));
});

it("keeps preset names within the limit, counting characters rather than code units", async () => {
  await setup();
  open("Presets");
  fireEvent.click(button("Save as new…"));
  const dialog = await screen.findByRole("dialog", { name: "Save as new preset" });
  const input = dialog.querySelector("input") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "🎧".repeat(70) } });
  expect(Array.from(input.value)).toHaveLength(64);
});

it("explains a waiting EQ with the backend's reason only when it is waiting", async () => {
  const engine = new PreviewEngine();
  engine.value.state.enabled = true;
  engine.value.audio.phase = "standby";
  engine.value.audio.message = "Standby · Steam is playing to another output.";
  const { controller } = await setup(engine);
  expect(screen.queryByText("Enabled; waiting for a suitable output.")).toBeNull();
  engine.value.audio.phase = "bypassed";
  engine.value.audio.message = "Bypassed: the selected output is not using its speaker port.";
  engine.value.revision++;
  await waitFor(async () => {
    await controller.refresh();
    expect(screen.getAllByText("Bypassed: the selected output is not using its speaker port.").length).toBeGreaterThan(0);
  });
});

it("labels the chart like the sliders and shows storage problems as alerts", async () => {
  const engine = new PreviewEngine();
  engine.value.warning = "Your latest edits could not be saved: Disk full";
  await setup(engine);
  expect(screen.getByRole("alert").textContent).toContain("Disk full");
  expect(document.querySelector(".deq-chart")).toBeNull();  // Not on the main card: its controls do not move it.
  open("Equalizer bands");
  expect(screen.getByText("31.5")).toBeTruthy();
});

it("warns about headphones only when the boost can reach them", async () => {
  const engine = new PreviewEngine();
  engine.value.state.enabled = true; engine.value.state.boost = 6; engine.value.state.speakers_only = false;
  await setup(engine);
  expect(screen.getByText(/Mind your hearing on headphones/)).toBeTruthy();
});

it("says when large EQ boosts lower the level, and keeps a raised input level", async () => {
  const { controller, engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "9" } });
  await act(async () => { await controller.flush(); });
  open("Output and level");
  expect(screen.getByText(/EQ boosts above \+6 dB lower it by 3\.0 dB/)).toBeTruthy();
  fireEvent.change(screen.getByRole("slider", { name: "Input level" }), { target: { value: "6" } });
  await act(async () => { await controller.flush(); });
  expect(engine.value.audio.effective_preamp).toBeCloseTo(3, 1);  // +6 input kept, -3 for the large boost.
});


it("caps a shelf's Q where it would resonate", async () => {
  const { controller, engine } = await setup();
  await openBand();
  fireEvent.change(screen.getByRole("slider", { name: "Bandwidth (Q)" }), { target: { value: "4" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Filter type" }), { target: { value: "lowshelf" } });
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0]).toMatchObject({ type: "lowshelf", q: 1.4 });
  expect((screen.getByRole("slider", { name: "Bandwidth (Q)" }) as HTMLInputElement).max).toBe("1.4");
});

it("toasts any failed action, wherever the panel is scrolled", async () => {
  const toast = vi.fn();
  const engine = new PreviewEngine();
  const { perform } = await setup(engine, toast);
  perform.mockImplementationOnce(async () => ({ ok: false, error: "Output not connected", state: await engine.status() }));
  open("Output and level");
  fireEvent.click(screen.getByRole("switch", { name: "Speakers only" }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Output not connected"));
});

it("moves a band's frequency with its slider, within the stable range", async () => {
  const { controller, engine } = await setup();
  await openBand();
  const slider = screen.getByRole("slider", { name: /^Frequency/ }) as HTMLInputElement;
  fireEvent.change(slider, { target: { value: String(Math.log10(1000)) } });
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0].frequency).toBe(1000);
  fireEvent.change(slider, { target: { value: slider.max } });
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0].frequency).toBe(20000);
});

it("shows an audio problem as an alert with a way to reconnect", async () => {
  const engine = new PreviewEngine();
  engine.value.audio.error = "PipeWire did not confirm the new EQ settings.";
  const { perform } = await setup(engine);
  expect(screen.getByRole("alert").textContent).toContain("did not confirm");
  fireEvent.click(screen.getByRole("button", { name: "Reconnect EQ" }));
  await waitFor(() => expect(perform.mock.calls.some(call => call[0] === "refresh")).toBe(true));
});

it("discards changes after confirmation and offers no Save for built-in presets", async () => {
  const { controller, engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "5" } });
  await act(async () => { await controller.flush(); });
  open("Presets");
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();  // Flat is built in.
  expect(screen.getByText(/Built-in presets stay as they are/)).toBeTruthy();
  fireEvent.click(button("Discard changes"));
  fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
  await waitFor(() => expect(engine.value.state.working.bass).toBe(0));
});
