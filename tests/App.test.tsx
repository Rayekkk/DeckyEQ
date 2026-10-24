import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { Controller } from "../src/controller";
import { PreviewEngine } from "../preview/engine";
import { setFiveBands } from "../src/pairs";
import { flatEQ, limits } from "../src/types";

const controllers: Controller[] = [];
afterEach(() => {
  cleanup(); controllers.splice(0).forEach(c => c.dispose()); document.querySelectorAll(".preview-modal-host").forEach(e => e.remove());
  act(() => setFiveBands(false)); localStorage.clear();
});
async function setup(engine = new PreviewEngine(), toast?: (body: string) => void) {
  const perform = vi.spyOn(engine, "perform");
  const controller = new Controller(engine); controllers.push(controller);
  await controller.refresh();
  const files = { importFile: vi.fn(async (): Promise<string | null> => "/home/deck/test.json"), exportFolder: vi.fn(async () => "/home/deck/Documents") };
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
  expect(screen.getByRole("button", { name: /^Equalizer\s*No bands adjusted$/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Presets\s*5 presets$/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Output\s*Legion Go 2 speakers$/ })).toBeTruthy();
  open("Equalizer");
  expect(screen.getAllByRole("slider")).toHaveLength(11);  // Ten bands and Input level.
  expect(screen.queryByRole("switch", { name: "Enable EQ" })).toBeNull();
  back();
  expect(screen.getAllByRole("slider")).toHaveLength(4);
});

it("boosts the volume for every preset without marking it edited", async () => {
  const { controller, engine, perform } = await setup();
  expect(screen.getByText("For every preset and game, once EQ is on.")).toBeTruthy();
  fireEvent.click(screen.getByRole("switch", { name: "Enable EQ" }));
  await screen.findByText("For every preset and game.");
  fireEvent.change(screen.getByRole("slider", { name: "Volume boost" }), { target: { value: "6" } });
  expect(screen.getByText("For every preset and game.")).toBeTruthy();  // Before the backend answers.
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
  expect(screen.queryByText("0% is mono.")).toBeNull();  // Wider needs no words; only mono is worth a note.
  expect(screen.getByRole("button", { name: /^Equalizer\s*No bands adjusted$/ })).toBeTruthy();
});

it("summarises adjusted bands on the main row; unsaved changes are shown on the card and the heading only", async () => {
  const { controller } = await setup();
  open("Equalizer");
  fireEvent.change(screen.getByRole("slider", { name: "63 Hz" }), { target: { value: "3" } });
  await act(async () => { await controller.flush(); });
  back();
  expect(screen.getByRole("button", { name: /^Equalizer\s*1 band adjusted/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Presets\s*5 presets$/ })).toBeTruthy();
  expect(screen.getByText("Flat · edited")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Unsaved changes" })).toBeTruthy();
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

it("refuses to save a new preset under a sound the game has switched away from", async () => {
  const engine = new PreviewEngine();
  Object.assign(engine.value, { game: { appid: "7", name: "Celeste" } });
  engine.value.state.games["7"] = { name: "Celeste" };
  engine.value.state.scope = "7";
  const toast = vi.fn();
  const { controller, perform } = await setup(engine, toast);
  open("Presets");
  fireEvent.click(button("Save as new…"));
  const dialog = await screen.findByRole("dialog", { name: "Save as new preset" });
  engine.value.state.scope = "";  // The game ended while the dialog hid the panel.
  engine.value.revision++;
  await act(async () => { await controller.refresh(); });
  fireEvent.change(dialog.querySelector("input")!, { target: { value: "Celeste mix" } });
  fireEvent.click(button("Save"));
  await waitFor(() => expect(perform).toHaveBeenCalledWith("save_as", { name: "Celeste mix", scope: "7" }, expect.any(Number)));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("The running game changed. Check the sound and try again."));
  expect(screen.queryByRole("dialog", { name: "Save as new preset" })).toBeNull();  // Another name would not fix it.
  expect(engine.value.presets.some(p => p.name === "Celeste mix")).toBe(false);
  fireEvent.click(button("Save as new…"));  // Saving again uses the sound shown now.
  await nameDialog("Save as new preset", "Celeste mix", "Save");
  await waitFor(() => expect(engine.value.presets.at(-1)?.name).toBe("Celeste mix"));
  expect(perform).toHaveBeenLastCalledWith("save_as", { name: "Celeste mix", scope: "" }, expect.any(Number));
});

it("renames, saves and deletes only user presets", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() });
  const { perform } = await setup(engine);
  open("Presets");
  expect(screen.queryByRole("button", { name: "Rename…" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
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
  fireEvent.click(button("Delete"));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Delete preset?" })).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(perform.mock.calls.some(call => call[0] === "delete")).toBe(true));
  expect(engine.value.state.selected).toBe("flat");
});

it("offers Save for an edited user preset and Discard for any edit", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() });
  engine.value.state.selected = "mine";
  const { controller } = await setup(engine);
  fireEvent.change(screen.getByRole("slider", { name: "Treble · above 6 kHz" }), { target: { value: "2" } });
  await act(async () => { await controller.flush(); });
  open("Presets");
  expect(screen.getByRole("heading", { name: "Mine · edited" })).toBeTruthy();
  fireEvent.click(button("Save"));
  await waitFor(() => expect(engine.value.presets.at(-1)?.eq.treble).toBe(2));
  expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
});

it("confirms before replacing an edited draft", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "test", name: "Saved", eq: flatEQ() });
  const { perform } = await setup(engine);
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "2" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Preset" }), { target: { value: "test" } });
  const dialog = await screen.findByRole("dialog", { name: "Switch preset?" });
  expect(dialog.textContent).toContain("Unsaved changes to “Flat” will be lost.");
  expect(perform.mock.calls.some(call => call[0] === "select")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(engine.value.state.selected).toBe("flat");
});

it("imports from the Decky file picker and exports this sound or the library", async () => {
  const { files, perform } = await setup();
  open("Presets");
  expect(screen.queryByRole("button", { name: "Import presets…" })).toBeNull();  // Behind one expander.
  fireEvent.click(button("Show import or export"));
  fireEvent.click(button("Import presets…"));
  await waitFor(() => expect(perform.mock.calls.some(call => call[0] === "import")).toBe(true));
  expect(files.importFile).toHaveBeenCalledWith("/home/deck");
  await waitFor(() => expect(button("Export this sound").disabled).toBe(false));
  fireEvent.click(button("Export this sound"));
  await screen.findByText("Exported to Documents/DeckyEQ/preview-preset.json");  // From the home folder, not the full path.
  await waitFor(() => expect(button("Export your presets").disabled).toBe(false));
  fireEvent.click(button("Export your presets"));  // Only your own presets: the built-in ones need no export.
  await waitFor(() => expect(perform.mock.calls.filter(call => call[0] === "export").map(call => call[1].all)).toEqual([false, true]));
});

it("exports to another folder from the details", async () => {
  const { files, perform } = await setup();
  open("Presets");
  fireEvent.click(button("Show import or export"));
  expect(screen.getByText("/home/deck/Documents/DeckyEQ")).toBeTruthy();
  expect(screen.queryByText("What is exported")).toBeNull();
  fireEvent.click(button("Export this sound to…"));
  await waitFor(() => expect(perform.mock.calls.some(call => call[0] === "export" && call[1].directory === "/home/deck/Documents")).toBe(true));
  expect(files.exportFolder).toHaveBeenCalledWith("/home/deck");
});

it("keeps why a game's sound could not be applied under the output details", async () => {
  const engine = new PreviewEngine();
  engine.value.audio.game_error = "pw-dump: timed out";
  await setup(engine);
  open("Output");
  fireEvent.click(button("Show details"));
  expect(screen.getByText("Game sound")).toBeTruthy();
  expect(screen.getByText("pw-dump: timed out")).toBeTruthy();
});

it("sends outcomes to a toast once instead of the panel when one is available", async () => {
  const toast = vi.fn();
  const { controller, files, rendered } = await setup(new PreviewEngine(), toast);
  open("Presets");
  fireEvent.click(button("Show import or export"));
  fireEvent.click(button("Export this sound"));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Exported to Documents/DeckyEQ/preview-preset.json"));
  expect(screen.queryByText(/Exported to/)).toBeNull();
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
  fireEvent.click(button("Delete"));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Delete preset?" })).getByRole("button", { name: "Delete" }));
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

it("toasts a refusal that another name would not fix instead of asking for a name again", async () => {
  const toast = vi.fn();
  const { perform, engine } = await setup(new PreviewEngine(), toast);
  perform.mockImplementationOnce(async () => ({ ok: false, error: "The preset library is full. Delete some presets first.",
    state: await engine.status() }));
  open("Presets");
  fireEvent.click(button("Save as new…"));
  await nameDialog("Save as new preset", "Mine", "Save");
  await waitFor(() => expect(toast).toHaveBeenCalledWith("The preset library is full. Delete some presets first."));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("toasts a refused slider edit once when an action sent it on its way", async () => {
  const toast = vi.fn();
  const { engine, perform } = await setup(new PreviewEngine(), toast);
  perform.mockImplementationOnce(async () => ({ ok: false, error: "PipeWire did not confirm the new EQ settings.", state: await engine.status() }));
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("switch", { name: "Enable EQ" }));  // Within the slider's 80 ms: the action sends the edit.
  await waitFor(() => expect(toast).toHaveBeenCalledWith("PipeWire did not confirm the new EQ settings."));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 100)); });
  expect(toast).toHaveBeenCalledOnce();
  expect(perform.mock.calls.map(call => call[0])).toEqual(["edit"]);  // The refused edit stopped the action.
});

it("closes its dialogs and the full-size editor when the plugin unloads", async () => {
  const { rendered } = await setup();
  open("Equalizer");
  fireEvent.click(button("Reset to flat"));  // A confirmation ...
  fireEvent.click(button("Edit full screen"));  // ... the full-size editor ...
  back();
  open("Presets");
  fireEvent.click(within(rendered.container).getByRole("button", { name: "Save as new…" }));  // ... and a name dialog.
  await screen.findByRole("dialog", { name: "Save as new preset" });
  expect(document.querySelectorAll(".preview-modal-host")).toHaveLength(3);
  rendered.unmount();
  expect(document.querySelector(".preview-modal-host")).toBeNull();
});

it("locks the full-size editor when the backend stops answering, and shows why there", async () => {
  const { controller, engine } = await setup();
  const full = await openFull();
  vi.spyOn(engine, "status").mockRejectedValue(new Error("backend gone"));
  await act(async () => { await controller.refresh(); });
  expect((within(full).getByRole("combobox", { name: "Preset" }) as HTMLSelectElement).disabled).toBe(true);
  expect(within(full).getByRole("alert").textContent).toBe("Could not reach DeckyEQ. backend gone");
});

it("puts the preset list back when Switch preset? is closed with B", async () => {
  const { controller } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "2" } });
  await act(async () => { await controller.flush(); });
  const list = () => screen.getByRole("combobox", { name: "Preset" }) as HTMLSelectElement;
  fireEvent.change(list(), { target: { value: "more-bass" } });
  fireEvent.keyDown(await screen.findByRole("dialog", { name: "Switch preset?" }), { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Switch preset?" })).toBeNull();
  await waitFor(() => expect(list().value).toBe("flat"));
});

it("uses the same preset names in every list: main screen, Presets and full screen", async () => {
  await setup();
  const names = (root: HTMLElement) =>
    Array.from((within(root).getByRole("combobox", { name: "Preset" }) as HTMLSelectElement).options).map(o => o.text);
  const main = names(document.querySelector(".deq") as HTMLElement);
  expect(main).toEqual(["Flat", "Clear voices", "More bass", "Legion Go S", "Legion Go 2"]);
  open("Presets");
  expect(names(document.querySelector(".deq") as HTMLElement)).toEqual(main);
  back();
  expect(names(await openFull())).toEqual(main);
});

it("names a band moved far from its usual frequency in DeckyEQ 1.0 by where it plays", async () => {
  const engine = new PreviewEngine();
  engine.value.state.working.bands[2] = { ...engine.value.state.working.bands[2], frequency: 3000, gain: 6 };
  const { controller } = await setup(engine);
  open("Equalizer");
  const slider = screen.getByRole("slider", { name: "3 kHz" });
  expect(screen.queryByRole("slider", { name: "125 Hz" })).toBeNull();
  fireEvent.change(slider, { target: { value: "5" } });
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[2]).toMatchObject({ frequency: 3000, gain: 5 });
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
  open("Equalizer");
  await screen.findByRole("slider", { name: "31.5 Hz" });  // The same names as every preset: its 31.25 Hz shelf is band 1.
  expect(screen.getByRole("slider", { name: "1 kHz" })).toBeTruthy();
  expect(engine.value.state.working.bands[5].type).toBe("highshelf");
  // Its 0 Hz, 40 kHz and 48 kHz placeholders do nothing: they show as flat bands at 63 Hz, 4 kHz and 8 kHz.
  expect(screen.queryByRole("slider", { name: "0 Hz" })).toBeNull();
  expect(["63 Hz", "4 kHz", "8 kHz"].map(name => (screen.getByRole("slider", { name }) as HTMLInputElement).value)).toEqual(["0", "0", "0"]);
  expect(screen.getAllByRole("slider")).toHaveLength(11);  // Ten bands, like every preset, and Input level.
  expect((screen.getByRole("slider", { name: "Input level" }) as HTMLInputElement).value).toBe("8");
  expect(screen.queryByRole("switch", { name: "Automatic headroom" })).toBeNull();  // Always on now.
  back();
  open("Presets");
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  expect(screen.getByText(/Community speaker preset/)).toBeTruthy();
  fireEvent.click(button("Save as new…"));
  await nameDialog("Save as new preset", "My Go S", "Save");
  await waitFor(() => expect(engine.value.presets.at(-1)?.name).toBe("My Go S"));
  expect(engine.value.presets.at(-1)?.eq).toEqual(engine.value.presets.find(p => p.id === "go-s-original")?.eq);
});

it("resets the whole sound to flat only after confirmation", async () => {
  const { controller, engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "5" } });
  await act(async () => { await controller.flush(); });
  open("Equalizer");
  fireEvent.click(button("Reset to flat"));
  const dialog = await screen.findByRole("dialog", { name: "Reset to flat?" });
  expect(dialog.textContent).toContain("Bands, bass, treble, stereo width and input level return to their defaults. Volume boost stays.");
  expect(dialog.textContent).not.toContain("Only this game's sound");
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  await waitFor(() => expect(engine.value.state.working.bass).toBe(0));
});

it("says a reset only reaches the game's own sound while that sound is active", async () => {
  const engine = new PreviewEngine();
  Object.assign(engine.value, { game: { appid: "7", name: "Celeste" } });
  engine.value.state.games["7"] = { name: "Celeste" };
  engine.value.state.scope = "7";
  await setup(engine);
  open("Equalizer");
  fireEvent.click(button("Reset to flat"));
  const dialog = await screen.findByRole("dialog", { name: "Reset to flat?" });
  expect(dialog.textContent).toContain("Volume boost stays. Only this game's sound is reset.");
});

it("toasts a slider edit the backend refused, once, and keeps the note", async () => {
  const toast = vi.fn();
  const { controller, engine, perform } = await setup(new PreviewEngine(), toast);
  perform.mockImplementationOnce(async () => ({ ok: false, error: "PipeWire did not confirm the new EQ settings.", state: await engine.status() }));
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "3" } });
  await act(async () => { await controller.flush(); });
  expect(toast).toHaveBeenCalledExactlyOnceWith("PipeWire did not confirm the new EQ settings.");
  expect(screen.getByRole("alert").textContent).toContain("did not confirm");
  await act(async () => { await controller.refresh(); });
  fireEvent.click(screen.getByRole("switch", { name: "Enable EQ" }));
  await act(async () => { await controller.flush(); });
  expect(toast).toHaveBeenCalledOnce();  // Later renders and polls do not repeat it.
});

it("labels outputs that share a description by their port, or their name when that is the same too", async () => {
  const engine = new PreviewEngine();
  engine.value.audio.outputs.push(
    { name: "alsa_output.usb-a", description: "USB Audio", port: "analog-output-lineout", speaker: false },
    { name: "alsa_output.usb-b", description: "USB Audio", port: "analog-output-headphones", speaker: false },
    { name: "alsa_output.dock-a", description: "Dock", port: "hdmi-output-0", speaker: false },
    { name: "alsa_output.dock-b", description: "Dock", port: "hdmi-output-0", speaker: false });
  await setup(engine);
  open("Output");
  const labels = Array.from((screen.getByRole("combobox", { name: "Apply EQ to" }) as HTMLSelectElement).options).map(o => o.text);
  expect(labels).toEqual(["Legion Go 2 speakers", "USB Audio (analog-output-lineout)", "USB Audio (analog-output-headphones)",
    "Dock (alsa_output.dock-a)", "Dock (alsa_output.dock-b)"]);
});

it("shows the input level on the equalizer row only while EQ is on and the level is not 0", async () => {
  const engine = new PreviewEngine();
  engine.value.state.boost = 6;  // The boost has its own control; the row never mentions it.
  engine.value.audio.effective_preamp = -3;
  const { controller } = await setup(engine);
  expect(screen.getByRole("button", { name: /^Equalizer\s*No bands adjusted$/ })).toBeTruthy();  // EQ is off: no level promised.
  engine.value.state.enabled = true;
  engine.value.revision++;
  await waitFor(async () => {
    await controller.refresh();
    expect(screen.getByRole("button", { name: /^Equalizer\s*No bands adjusted · level -3\.0 dB$/ })).toBeTruthy();
  });
  engine.value.audio.effective_preamp = 0;
  engine.value.revision++;
  await waitFor(async () => {
    await controller.refresh();
    expect(screen.getByRole("button", { name: /^Equalizer\s*No bands adjusted$/ })).toBeTruthy();
  });
});

it("counts adjusted bands and game sounds on the main rows", async () => {
  const engine = new PreviewEngine();
  engine.value.state.working.bands[0].gain = 2;
  engine.value.state.working.bands[4].gain = -1;
  engine.value.state.games["7"] = { name: "Celeste" };
  const { controller } = await setup(engine);
  expect(screen.getByRole("button", { name: /^Equalizer\s*2 bands adjusted$/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Presets\s*5 presets · 1 game sound$/ })).toBeTruthy();
  engine.value.state.working = structuredClone(engine.value.presets.find(p => p.id === "go-s-original")!.eq);
  engine.value.revision++;
  await waitFor(async () => {  // Six bands play; the two placeholders with a gain do nothing.
    await controller.refresh();
    expect(screen.getByRole("button", { name: /^Equalizer\s*6 bands adjusted$/ })).toBeTruthy();
  });
  engine.value.state.games["8"] = { name: "Hades" };
  engine.value.revision++;
  await waitFor(async () => {
    await controller.refresh();
    expect(screen.getByRole("button", { name: /^Presets\s*5 presets · 2 game sounds$/ })).toBeTruthy();
  });
});

it("names an output that is no longer connected", async () => {
  const engine = new PreviewEngine();
  engine.value.state.target = "alsa_output.gone";
  await setup(engine);
  expect(screen.getByRole("button", { name: /^Output\s*Disconnected output$/ })).toBeTruthy();
  open("Output");
  const select = screen.getByRole("combobox", { name: "Apply EQ to" }) as HTMLSelectElement;
  expect(select.selectedOptions[0].text).toBe("Selected output · disconnected");
});

it("labels every phase of the equalizer on the status card", async () => {
  const engine = new PreviewEngine();
  const { controller } = await setup(engine);
  const labels = { active: "Live", standby: "Standby", off: "EQ off", bypassed: "Bypassed", waiting: "Waiting for output",
    connecting: "Connecting", error: "Paused", unavailable: "Unavailable", unheard_of: "unheard_of" };
  for (const [phase, label] of Object.entries(labels)) {
    engine.value.audio.phase = phase;
    engine.value.revision++;
    await waitFor(async () => {
      await controller.refresh();
      expect(document.querySelector(".deq-status")!.textContent).toBe(label);
    });
  }
});

it("puts a toggle back when the backend refuses its change", async () => {
  const engine = new PreviewEngine();
  const toast = vi.fn();
  const { perform } = await setup(engine, toast);
  perform.mockImplementationOnce(async () => ({ ok: false, error: "Choose a connected audio output.", state: await engine.status() }));
  const enable = screen.getByRole("switch", { name: "Enable EQ" }) as HTMLInputElement;
  fireEvent.click(enable);
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Choose a connected audio output."));
  await waitFor(() => expect(enable.isConnected).toBe(false));  // Redrawn from the state, which is still off.
  expect((screen.getByRole("switch", { name: "Enable EQ" }) as HTMLInputElement).checked).toBe(false);
  await waitFor(() => expect((screen.getByRole("switch", { name: "Enable EQ" }) as HTMLInputElement).disabled).toBe(false));
  open("Output");
  perform.mockImplementationOnce(async () => ({ ok: false, error: "Refused", state: await engine.status() }));
  const speakers = screen.getByRole("switch", { name: "Speakers only" }) as HTMLInputElement;
  fireEvent.click(speakers);
  await waitFor(() => expect(speakers.isConnected).toBe(false));
  expect((screen.getByRole("switch", { name: "Speakers only" }) as HTMLInputElement).checked).toBe(true);
});

it("puts a dropdown back when the backend refuses the pick: Steam's keeps showing it otherwise", async () => {
  const engine = new PreviewEngine();
  engine.value.audio.outputs.push({ name: "alsa_output.hdmi", description: "HDMI", port: "hdmi-output-0", speaker: false });
  const { perform } = await setup(engine, vi.fn());
  const refuse = () => perform.mockImplementationOnce(async () => ({ ok: false, error: "Refused", state: await engine.status() }));
  const list = (name: string) => screen.getByRole("combobox", { name }) as HTMLSelectElement;
  refuse();
  fireEvent.change(list("Preset"), { target: { value: "more-bass" } });
  await waitFor(() => expect(list("Preset").value).toBe("flat"));
  open("Output");
  refuse();
  fireEvent.change(list("Apply EQ to"), { target: { value: "alsa_output.hdmi" } });
  await waitFor(() => expect(list("Apply EQ to").value).toBe("alsa_output.preview"));
  expect(perform.mock.calls.map(call => call[0])).toEqual(["select", "output"]);
});

it("redraws only the toggle whose change was cancelled, so the others keep their place", async () => {
  const engine = new PreviewEngine();
  Object.assign(engine.value, { game: { appid: "7", name: "Celeste" } });
  engine.value.state.games["7"] = { name: "Celeste" };
  engine.value.state.scope = "7";
  await setup(engine);
  expect(screen.getByText("Sound changes apply to this game only. The preset library is shared.")).toBeTruthy();
  const enable = screen.getByRole("switch", { name: "Enable EQ" });
  const own = screen.getByRole("switch", { name: "Own sound for Celeste" });
  fireEvent.click(own);
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(own.isConnected).toBe(false));
  expect((screen.getByRole("switch", { name: "Own sound for Celeste" }) as HTMLInputElement).checked).toBe(true);
  expect(enable.isConnected).toBe(true);
  expect(screen.getByRole("switch", { name: "Enable EQ" })).toBe(enable);
});

it("locks the panel while a file picker is open", async () => {
  const { files } = await setup();
  let close!: (path: string | null) => void;
  files.importFile.mockImplementationOnce(() => new Promise<string | null>(done => { close = done; }));
  open("Presets");
  fireEvent.click(button("Show import or export"));
  fireEvent.click(button("Import presets…"));
  await waitFor(() => expect(files.importFile).toHaveBeenCalled());
  expect(button("Save as new…").disabled).toBe(true);
  expect((screen.getByRole("combobox", { name: "Preset" }) as HTMLSelectElement).disabled).toBe(true);
  await act(async () => close(null));
  await waitFor(() => expect(button("Save as new…").disabled).toBe(false));
});

it("sends an import with the sound that is active when the file picker closes", async () => {
  const engine = new PreviewEngine();
  const { controller, files, perform } = await setup(engine);
  files.importFile.mockImplementationOnce(async () => {
    Object.assign(engine.value, { game: { appid: "7", name: "Celeste" } });  // A game started while the picker was open.
    engine.value.state.games["7"] = { name: "Celeste" };
    engine.value.state.scope = "7";
    engine.value.revision++;
    await controller.refresh();
    return "/home/deck/test.json";
  });
  open("Presets");
  fireEvent.click(button("Show import or export"));
  fireEvent.click(button("Import presets…"));
  await waitFor(() => expect(perform).toHaveBeenCalledWith("import", { path: "/home/deck/test.json", scope: "7" }, expect.any(Number)));
  await waitFor(() => expect(engine.value.presets.some(p => p.name === "Imported preview")).toBe(true));
});

it("goes back with B and returns focus to the row it came from", async () => {
  await setup();
  open("Equalizer");
  fireEvent.keyDown(button("‹ DeckyEQ"), { key: "Escape" });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /^Equalizer/ }));
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
  fireEvent.click(button("Show import or export"));
  fireEvent.click(button("Export this sound to…"));
  expect((await screen.findByRole("alert")).textContent).toContain("Simulated failure");
  expect(files.exportFolder).not.toHaveBeenCalled();
});

it("opens the file picker again after an earlier, unrelated error", async () => {
  const { controller, files } = await setup();
  open("Presets");
  act(() => controller.reportError("Earlier rename failed"));
  fireEvent.click(button("Show import or export"));
  fireEvent.click(button("Import presets…"));
  await waitFor(() => expect(files.importFile).toHaveBeenCalled());
});

it("clears an old message when switching pages", async () => {
  const { controller } = await setup();
  act(() => controller.reportError("A preset already uses that name."));
  open("Presets");
  expect(screen.queryByRole("alert")).toBeNull();
});

it("moves the D-pad focus to the back button and back to the row it came from", async () => {
  await setup();
  open("Output");
  expect(document.activeElement).toBe(button("‹ DeckyEQ"));
  back();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /^Output/ }));
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
  open("Output");
  fireEvent.change(screen.getByRole("combobox", { name: "Apply EQ to" }), { target: { value: "alsa_output.hdmi" } });
  await waitFor(() => expect(perform).toHaveBeenCalledWith("output", { target: "alsa_output.hdmi", speakers_only: true, scope: "" }, expect.any(Number)));
  await waitFor(() => expect((screen.getByRole("switch", { name: "Speakers only" }) as HTMLInputElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("switch", { name: "Speakers only" }));
  await waitFor(() => expect(perform).toHaveBeenCalledWith("output", { target: "alsa_output.hdmi", speakers_only: false, scope: "" }, expect.any(Number)));
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
  engine.value.audio.message = "Steam is playing to another output. EQ applies to Legion Go 2 speakers.";
  const { controller } = await setup(engine);
  expect(screen.queryByText("Enabled; waiting for a suitable output.")).toBeNull();
  engine.value.audio.phase = "bypassed";
  engine.value.audio.message = "Speakers only is on, so EQ is off on this output.";
  engine.value.revision++;
  await waitFor(async () => {
    await controller.refresh();
    expect(screen.getAllByText("Speakers only is on, so EQ is off on this output.").length).toBeGreaterThan(0);
  });
});

it("shows the curve on the main screen only, and storage problems as alerts", async () => {
  const engine = new PreviewEngine();
  engine.value.warning = "Your latest edits could not be saved: Disk full";
  await setup(engine);
  expect(screen.getByRole("alert").textContent).toContain("Disk full");
  expect(screen.getByRole("img", { name: /^Equalizer curve/ })).toBeTruthy();
  open("Equalizer");
  expect(screen.queryByRole("img", { name: /^Equalizer curve/ })).toBeNull();  // The sliders come first; the full-screen editor has the curve.
});

it("warns about headphones only when the boost can reach them", async () => {
  const engine = new PreviewEngine();
  engine.value.state.enabled = true; engine.value.state.boost = 6; engine.value.state.speakers_only = false;
  const { controller } = await setup(engine);
  expect(screen.getByText("For every preset and game. Mind your hearing on headphones.")).toBeTruthy();
  engine.value.state.speakers_only = true;
  engine.value.revision++;
  await waitFor(async () => {
    await controller.refresh();
    expect(screen.getByText("For every preset and game.")).toBeTruthy();
  });
  engine.value.state.speakers_only = false; engine.value.state.boost = 0;
  engine.value.revision++;
  await waitFor(async () => {
    await controller.refresh();
    expect(screen.queryByText(/Mind your hearing/)).toBeNull();  // Nothing is boosted.
  });
});

it("says when large EQ boosts lower the level, and keeps a raised input level", async () => {
  const { controller, engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "9" } });
  await act(async () => { await controller.flush(); });
  open("Equalizer");
  expect(screen.getByText("Lowered by 3.0 dB to make room for big EQ boosts.")).toBeTruthy();
  fireEvent.change(screen.getByRole("slider", { name: "Input level" }), { target: { value: "6" } });
  await act(async () => { await controller.flush(); });
  expect(engine.value.audio.effective_preamp).toBeCloseTo(3, 1);  // +6 input kept, -3 for the large boost.
});


it("toasts any failed action, wherever the panel is scrolled", async () => {
  const toast = vi.fn();
  const engine = new PreviewEngine();
  const { perform } = await setup(engine, toast);
  perform.mockImplementationOnce(async () => ({ ok: false, error: "Output not connected", state: await engine.status() }));
  open("Output");
  fireEvent.click(screen.getByRole("switch", { name: "Speakers only" }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Output not connected"));
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
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();  // Flat is built in.
  expect(screen.getByText("Save as new to keep your changes.")).toBeTruthy();
  fireEvent.click(button("Discard"));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Discard changes?" })).getByRole("button", { name: "Discard" }));
  await waitFor(() => expect(engine.value.state.working.bass).toBe(0));
});

it("moves neighbouring pairs with five sliders and never changes the sound by switching", async () => {
  const { controller, engine, perform } = await setup();
  fireEvent.change(screen.getByRole("combobox", { name: "Preset" }), { target: { value: "go-s-original" } });
  await waitFor(() => expect(engine.value.state.selected).toBe("go-s-original"));
  open("Equalizer");
  const before = structuredClone(engine.value.state.working);
  perform.mockClear();
  fireEvent.click(screen.getByRole("switch", { name: "Five sliders" }));
  expect(screen.getAllByRole("slider")).toHaveLength(6);  // Five pairs and Input level.
  expect((screen.getByRole("slider", { name: "31.5 Hz – 63 Hz" }) as HTMLInputElement).value).toBe("-5");  // With the flat 63 Hz.
  fireEvent.click(screen.getByRole("switch", { name: "Five sliders" }));
  fireEvent.click(screen.getByRole("switch", { name: "Five sliders" }));
  await act(async () => { await controller.flush(); });
  expect(perform).not.toHaveBeenCalled();
  expect(engine.value.state.working).toEqual(before);
  expect(controller.getSnapshot().snapshot!.dirty).toBe(false);
  expect(localStorage.getItem("deckyeq.fiveBands")).toBe("1");
  fireEvent.change(screen.getByRole("slider", { name: "125 Hz – 250 Hz" }), { target: { value: "2" } });
  fireEvent.change(screen.getByRole("slider", { name: "31.5 Hz – 63 Hz" }), { target: { value: "-5.5" } });
  await act(async () => { await controller.flush(); });
  // Both bands of a pair move by the step: -10 and the flat placeholder become -10.5 and -0.5, they do not jump to the average.
  expect(engine.value.state.working.bands.map(b => b.gain)).toEqual([-10.5, -0.5, 2, 2, -5, -5, -5, -5, 0, 0]);
  expect(engine.value.state.working.bands.slice(0, 2)).toEqual([{ type: "lowshelf", frequency: 31.5, gain: -10.5, q: 1 },
    { type: "peaking", frequency: 63, gain: -0.5, q: limits.flat_q }]);  // The placeholder became a band; band 1 sits at 31.5 Hz.
  expect((screen.getByRole("slider", { name: "31.5 Hz – 63 Hz" }) as HTMLInputElement).value).toBe("-5.5");  // Still the average.
});

it("offers Save and Discard on the main screen only while the sound has unsaved changes", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() });
  engine.value.state.selected = "mine";
  const { controller, perform } = await setup(engine);
  const bass = () => screen.getByRole("slider", { name: "Bass · below 120 Hz" });
  expect(screen.queryByRole("heading", { name: "Unsaved changes" })).toBeNull();
  fireEvent.change(bass(), { target: { value: "3" } });
  await act(async () => { await controller.flush(); });
  fireEvent.click(button("Save"));
  await waitFor(() => expect(engine.value.presets.find(p => p.id === "mine")?.eq.bass).toBe(3));
  expect(perform).toHaveBeenCalledWith("save", { scope: "" }, expect.any(Number));
  await waitFor(() => expect(screen.queryByRole("heading", { name: "Unsaved changes" })).toBeNull());
  fireEvent.change(bass(), { target: { value: "5" } });
  await act(async () => { await controller.flush(); });
  fireEvent.click(button("Discard"));
  const dialog = await screen.findByRole("dialog", { name: "Discard changes?" });
  expect(dialog.textContent).toContain("Restore the saved version of “Mine”.");
  fireEvent.click(within(dialog).getByRole("button", { name: "Discard" }));
  await waitFor(() => expect(engine.value.state.working.bass).toBe(3));
});

it("saves an edited built-in preset as a new one from the main screen", async () => {
  const { controller, engine } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "4" } });
  await act(async () => { await controller.flush(); });
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();  // Built-in presets stay as they are.
  fireEvent.click(button("Save as new…"));
  await nameDialog("Save as new preset", "Main", "Save");
  await waitFor(() => expect(engine.value.presets.at(-1)).toMatchObject({ name: "Main", eq: { bass: 4 } }));
});

it("keeps the output on the Output page and Input level with the equalizer", async () => {
  await setup();
  open("Output");
  expect(screen.getByRole("heading", { name: "Output" })).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Apply EQ to" })).toBeTruthy();
  expect(screen.queryByRole("slider", { name: "Input level" })).toBeNull();
  back();
  open("Equalizer");
  expect(screen.getByRole("slider", { name: "Input level" })).toBeTruthy();
});

it("edits the sound from the curve on the main screen, and B finishes editing there", async () => {
  const { controller, engine } = await setup();
  const press = (key: string) => fireEvent.keyDown(document.querySelector(".deq-graph")!, { key });
  press("Enter"); press("ArrowRight"); press("ArrowDown");
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[1].gain).toBe(-0.5);
  expect(engine.value.state.working.bands[1].frequency).toBe(63);  // The graph never moves a frequency.
  expect(screen.getByRole("heading", { name: "Unsaved changes" })).toBeTruthy();
  expect(screen.getByText("←→ band · ↑↓ gain · X flat · B done")).toBeTruthy();
  press("Escape");
  expect(screen.getByText("63 Hz · -0.5 dB")).toBeTruthy();  // Editing is over: the short reading again.
  expect(screen.getByRole("switch", { name: "Enable EQ" })).toBeTruthy();
});

async function openFull() {
  open("Equalizer");
  fireEvent.click(button("Edit full screen"));
  return document.querySelector(".preview-fullmodal") as HTMLElement;
}

it("edits, saves and closes the curve at full size", async () => {
  const { controller, engine } = await setup();
  const full = await openFull();
  const modal = within(full);
  expect(modal.getByRole("img", { name: /^Equalizer curve/ })).toBeTruthy();
  expect((modal.getByRole("button", { name: "Save as new…" }) as HTMLButtonElement).disabled).toBe(true);  // Nothing to save yet.
  const press = (key: string) => fireEvent.keyDown(full.querySelector(".deq-graph")!, { key });
  press("Enter"); press("ArrowUp");
  await act(async () => { await controller.flush(); });
  expect(engine.value.state.working.bands[0].gain).toBe(0.5);
  expect(full.textContent).toContain("Edited · EQ is off, so changes are not heard.");
  fireEvent.click(modal.getByRole("button", { name: "Save as new…" }));
  await nameDialog("Save as new preset", "Full", "Save");
  await waitFor(() => expect(engine.value.presets.at(-1)?.name).toBe("Full"));
  await waitFor(() => expect(full.textContent).not.toContain("Edited"));
  fireEvent.change(modal.getByRole("combobox", { name: "Preset" }), { target: { value: "more-bass" } });
  await waitFor(() => expect(engine.value.state.working.bands[2].gain).toBe(5));
  fireEvent.click(modal.getByRole("button", { name: "Done" }));
  expect(document.querySelector(".preview-fullmodal")).toBeNull();
});

it("discards changes from the full-size editor after confirmation", async () => {
  const { controller, engine } = await setup();
  const full = await openFull();
  const press = (key: string) => fireEvent.keyDown(full.querySelector(".deq-graph")!, { key });
  press("Enter"); press("ArrowUp");
  await act(async () => { await controller.flush(); });
  fireEvent.click(within(full).getByRole("button", { name: "Discard" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Discard changes?" })).getByRole("button", { name: "Discard" }));
  await waitFor(() => expect(engine.value.state.working.bands[0].gain).toBe(0));
});

it("puts the preset list back when Switch preset? is cancelled, on the main screen, Presets and full screen", async () => {
  const { controller, perform } = await setup();
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "2" } });
  await act(async () => { await controller.flush(); });
  const cancel = async (root: HTMLElement) => {
    const list = () => within(root).getByRole("combobox", { name: "Preset" }) as HTMLSelectElement;
    fireEvent.change(list(), { target: { value: "more-bass" } });
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Switch preset?" })).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(list().value).toBe("flat"));  // The sound is still Flat; Steam's list would show More bass.
  };
  await cancel(document.querySelector(".deq") as HTMLElement);
  open("Presets");
  await cancel(document.querySelector(".deq") as HTMLElement);
  back();
  await cancel(await openFull());
  expect(perform.mock.calls.some(call => call[0] === "select")).toBe(false);
});

it("keeps the full-screen status current while it is open, and stops when it closes", async () => {
  const engine = new PreviewEngine();
  const status = vi.spyOn(engine, "status");
  const controller = new Controller(engine); controllers.push(controller);
  await controller.refresh();
  // The QAM is hidden behind the dialog, so its own polling has stopped.
  render(<App controller={controller} files={{ importFile: async () => null, exportFolder: async () => null }} visible={false} />);
  vi.useFakeTimers();
  try {
    const full = await openFull();
    const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    await tick(0);
    engine.value.state.enabled = true;  // Headphones, a game: the sound changes while nothing is touched.
    engine.value.audio.message = "EQ on Legion Go 2 speakers";
    engine.value.revision++;
    await tick(1900);
    expect(full.textContent).toContain("EQ on Legion Go 2 speakers");
    fireEvent.click(within(full).getByRole("button", { name: "Done" }));
    const reads = status.mock.calls.length;
    await tick(6000);
    expect(status).toHaveBeenCalledTimes(reads);
  } finally { vi.useRealTimers(); }
});

const BANDS = ["31.5 Hz", "63 Hz", "125 Hz", "250 Hz", "500 Hz", "1 kHz", "2 kHz", "4 kHz", "8 kHz", "16 kHz"];
const PAIRS = ["31.5 Hz – 63 Hz", "125 Hz – 250 Hz", "500 Hz – 1 kHz", "2 kHz – 4 kHz", "8 kHz – 16 kHz"];
const bandSliders = () => screen.getAllByRole("slider").map(s => s.getAttribute("aria-label")).filter(name => name !== "Input level");

it("shows the same ten bands, and the same five pairs, for every preset", async () => {
  const { engine } = await setup();
  for (const id of engine.value.presets.map(p => p.id)) {
    fireEvent.change(screen.getByRole("combobox", { name: "Preset" }), { target: { value: id } });
    await waitFor(() => expect(engine.value.state.selected).toBe(id));
    open("Equalizer");
    expect(bandSliders()).toEqual(BANDS);
    fireEvent.click(screen.getByRole("switch", { name: "Five sliders" }));
    expect(bandSliders()).toEqual(PAIRS);
    fireEvent.click(screen.getByRole("switch", { name: "Five sliders" }));
    back();
  }
});

it("follows the five-slider switch in every view", async () => {
  const { rendered } = await setup();
  const full = await openFull();
  expect(full.querySelectorAll(".deq-hit")).toHaveLength(10);
  expect((within(full).getByRole("combobox", { name: "Preset" }).closest("[style*=grid]") as HTMLElement).style.gridTemplateColumns)
    .toBe("minmax(0, 2fr) minmax(0, 1fr)");  // The preset name gets twice the room of the switch.
  fireEvent.click(within(rendered.container).getByRole("switch", { name: "Five sliders" }));
  expect(full.querySelectorAll(".deq-hit")).toHaveLength(5);
  back();
  expect(rendered.container.querySelectorAll(".deq-hit")).toHaveLength(5);  // The curve on the main screen follows too.
  expect((within(full).getByRole("switch", { name: "Five sliders" }) as HTMLInputElement).checked).toBe(true);
});

it("puts Volume boost right under Enable EQ, before the game's own sound and the preset", async () => {
  const engine = new PreviewEngine();
  Object.assign(engine.value, { game: { appid: "7", name: "Celeste" } });
  await setup(engine);
  const controls = Array.from(document.querySelectorAll("input[aria-label], select[aria-label]")).map(e => e.getAttribute("aria-label"));
  expect(controls.slice(0, 4)).toEqual(["Enable EQ", "Volume boost", "Own sound for Celeste", "Preset"]);
});

it("tells the Equalizer, Presets and Output pages why nothing is heard, only while EQ is not live", async () => {
  const engine = new PreviewEngine();
  const { controller } = await setup(engine);
  const note = (text: string) => screen.queryByText(text, { selector: ".deq-note" });
  for (const page of ["Equalizer", "Presets", "Output"]) {
    open(page);
    expect(note("EQ is off, so changes are not heard.")).toBeTruthy();
    back();
  }
  expect(note("EQ is off, so changes are not heard.")).toBeNull();  // The card says it on the main screen.
  open("About");
  expect(note("EQ is off, so changes are not heard.")).toBeNull();
  back();
  engine.value.state.enabled = true;
  engine.value.audio.phase = "bypassed";
  engine.value.audio.message = "Speakers only is on, so EQ is off on this output.";
  engine.value.revision++;
  await waitFor(async () => { await controller.refresh(); expect(controller.getSnapshot().snapshot!.audio.phase).toBe("bypassed"); });
  open("Output");
  expect(note("Speakers only is on, so EQ is off on this output.")).toBeTruthy();
  expect(screen.queryByText("Status")).toBeNull();  // The details no longer repeat it.
  back();
  engine.value.audio.phase = "active";
  engine.value.audio.message = "EQ on Legion Go 2 speakers";
  engine.value.revision++;
  await waitFor(async () => { await controller.refresh(); expect(controller.getSnapshot().snapshot!.audio.phase).toBe("active"); });
  for (const page of ["Equalizer", "Presets", "Output"]) {
    open(page);
    expect(document.querySelector(".deq-note")).toBeNull();
    back();
  }
});

it("starts Save as new… from the current preset's name, from the page, the main screen and the full editor", async () => {
  const engine = new PreviewEngine();
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() });
  engine.value.state.selected = "mine";
  const { controller } = await setup(engine);
  const input = async () => (await screen.findByRole("dialog", { name: "Save as new preset" })).querySelector("input") as HTMLInputElement;
  open("Presets");
  fireEvent.click(button("Save as new…"));
  expect((await input()).value).toBe("Mine");
  expect(button("Save").disabled).toBe(false);  // One press of A saves it; the backend adds " (2)".
  fireEvent.click(button("Cancel"));
  back();
  fireEvent.change(screen.getByRole("combobox", { name: "Preset" }), { target: { value: "more-bass" } });
  await waitFor(() => expect(engine.value.state.selected).toBe("more-bass"));
  fireEvent.change(screen.getByRole("slider", { name: "Bass · below 120 Hz" }), { target: { value: "4" } });
  await act(async () => { await controller.flush(); });
  fireEvent.click(button("Save as new…"));  // A built-in preset's Save on the main screen.
  expect((await input()).value).toBe("More bass");
  fireEvent.click(button("Cancel"));
  const full = await openFull();
  fireEvent.click(within(full).getByRole("button", { name: "Save as new…" }));
  expect((await input()).value).toBe("More bass");
});

it("puts the sliders first on the Equalizer page, Five sliders after Input level and no curve", async () => {
  await setup();
  open("Equalizer");
  const after = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  const first = screen.getByRole("slider", { name: "31.5 Hz" }), level = screen.getByRole("slider", { name: "Input level" });
  const five = screen.getByRole("switch", { name: "Five sliders" }), reset = button("Reset to flat");
  expect(after(button("Edit full screen"), first)).toBe(true);
  expect(after(level, five) && after(five, reset)).toBe(true);
  expect(screen.getByText("Each slider moves two bands.")).toBeTruthy();
  expect(screen.queryByText(/neighbouring/)).toBeNull();
});

it("has no card line repeating the Own sound switch, and one Forget dialog for a game's sound", async () => {
  const engine = new PreviewEngine();
  Object.assign(engine.value, { game: { appid: "7", name: "Celeste" } });
  engine.value.state.games["7"] = { name: "Celeste" };
  engine.value.state.scope = "7";
  await setup(engine);
  expect(screen.queryByText("Own sound for Celeste", { selector: ".deq-small" })).toBeNull();
  const forget = async () => {
    const dialog = await screen.findByRole("dialog", { name: "Forget the sound for Celeste?" });
    expect(dialog.textContent).toContain("The game will use your everyday sound.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  };
  fireEvent.click(screen.getByRole("switch", { name: "Own sound for Celeste" }));
  await forget();
  open("Presets");
  fireEvent.click(button("Forget Celeste"));
  await forget();
});

it("keeps the Output page short: one line for Speakers only, and details that do not repeat other pages", async () => {
  await setup();
  open("Output");
  expect(screen.getByText("No EQ on headphones, HDMI or Bluetooth.")).toBeTruthy();
  fireEvent.click(button("Show details"));
  expect(screen.getByText("Volume")).toBeTruthy();
  expect(screen.getByText("Steam's volume, volume buttons and output choice work as usual.")).toBeTruthy();
  for (const gone of ["Status", "Effective input level", "Your volume stays yours"]) expect(screen.queryByText(gone)).toBeNull();
});

it("has a preview backend that refuses like the real one", async () => {
  const engine = new PreviewEngine();
  const result = async (action: string, payload: Record<string, unknown> = {}) => {
    const answer = await engine.perform(action, payload, engine.value.revision);
    return answer.ok ? "ok" : answer.error;
  };
  expect(await result("save")).toBe("Built-in presets are read-only. Use Save as new… to keep your changes.");
  expect(await result("rename", { name: "Mine" })).toBe("Built-in presets cannot be renamed.");
  expect(await result("delete")).toBe("Built-in presets cannot be deleted.");
  engine.value.presets.push({ id: "mine", name: "Mine", eq: flatEQ() });
  engine.value.state.selected = "mine";
  for (const action of ["delete", "revert", "rename"])  // A dialog opened for another preset.
    expect(await result(action, { id: "flat", name: "Ours" })).toBe("The selected preset changed. Check it and try again.");
  engine.value.state.selected = "flat";
  engine.value.state.working = { limiter: true, width: 1, auto_headroom: true, preamp: 0, treble: 0, bass: 0,
    bands: flatEQ().bands.map(({ type, q, gain, frequency }) => ({ type, q, gain, frequency })) };
  expect(await result("enable", { enabled: true })).toBe("ok");
  expect(engine.value.dirty).toBe(false);  // The same sound with its keys in another order.
});
