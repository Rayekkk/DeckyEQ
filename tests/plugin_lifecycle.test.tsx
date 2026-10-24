import { cloneElement, ReactElement } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PreviewEngine } from "../preview/engine";
import { openFilePicker, toaster } from "@decky/api";
import { Router } from "../preview/decky-ui";

const decky = vi.hoisted(() => ({
  visible: true,
  status: vi.fn(),
  perform: vi.fn(),
  checkUpdates: vi.fn(),
  downloadUpdate: vi.fn(),
  setGame: vi.fn(),
  selections: new Map<string, (option: { data: unknown }) => void>(),
}));

vi.mock("@decky/api", () => ({
  definePlugin: (factory: unknown) => factory,
  callable: (name: string) => ({ get_status: decky.status, perform: decky.perform,
    updates_check: decky.checkUpdates, updates_download: decky.downloadUpdate, set_game: decky.setGame })[name],
  useQuickAccessVisible: () => decky.visible,
  FileSelectionType: { FILE: 0, FOLDER: 1 },
  openFilePicker: vi.fn(),
  toaster: { toast: vi.fn() },
}));
vi.mock("@decky/ui", async () => {
  const native = await import("../preview/decky-ui");
  return { ...native, DropdownItem: (props: any) => {
    // Steam's menu keeps this callback after it hides the QAM's plugin view.
    decky.selections.set(props.label, props.onChange);
    return <native.DropdownItem {...props} />;
  } };
});

import factory from "../src/index";
type Plugin = { content: ReactElement; alwaysRender?: boolean; onDismount(): void };
const plugins: Plugin[] = [];
let engine: PreviewEngine;

beforeEach(() => {
  decky.visible = true;
  decky.selections.clear();
  decky.checkUpdates.mockReset().mockResolvedValue({ success: true, current_version: "1.0.0", update_available: false, download_available: false, no_release: true });
  decky.downloadUpdate.mockReset();
  engine = new PreviewEngine();
  decky.status.mockReset().mockImplementation(() => engine.status());
  decky.perform.mockReset().mockImplementation((...args: Parameters<PreviewEngine["perform"]>) => engine.perform(...args));
  decky.setGame.mockReset().mockImplementation((...args: Parameters<PreviewEngine["setGame"]>) => engine.setGame(...args));
  delete Router.MainRunningApp;
});
afterEach(() => {
  cleanup();
  plugins.splice(0).forEach(plugin => plugin.onDismount());
  vi.useRealTimers();
});

/** Moves fake time; RTL's findBy and waitFor would wait forever on faked timers. */
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

async function setup(fake = false) {
  if (fake) vi.useFakeTimers();
  // Use the actual plugin descriptor, not a direct <App /> mount. Decky removes
  // the component tree on hide unless that descriptor opts into alwaysRender.
  const plugin = (factory as unknown as () => Plugin)();
  plugins.push(plugin);
  // Clone triggers the visibility-hook update without replacing the component.
  function Host() { return decky.visible || plugin.alwaysRender ? cloneElement(plugin.content) : null; }
  const rendered = render(<Host />);
  if (fake) await tick(0);
  else await screen.findByRole("combobox", { name: "Preset" });
  const visibility = (value: boolean) => { decky.visible = value; rendered.rerender(<Host />); };
  return { visibility, plugin };
}

const celeste = { appid: 7, display_name: "Celeste" };
const everyday = async (name: string) => {  // Cancels or confirms the "Forget the sound for ...?" dialog.
  fireEvent.click(screen.getByRole("switch", { name: "Own sound for Hades" }));
  await tick(0);
  fireEvent.click(screen.getByRole("button", { name }));
  await tick(100);
};

it("offers a running game its own sound and lists it under Presets", async () => {
  await setup(true);
  expect(screen.queryByRole("switch", { name: /^Own sound/ })).toBeNull();
  Router.MainRunningApp = { appid: 1145360, display_name: "Hades" };
  await tick(4000);
  const toggle = screen.getByRole("switch", { name: "Own sound for Hades" });
  expect(decky.setGame).toHaveBeenCalledWith("1145360", "Hades");
  fireEvent.click(toggle);
  await tick(100);
  expect(decky.perform).toHaveBeenCalledWith("own_sound", { enabled: true, appid: "1145360", scope: "" }, expect.any(Number));
  expect(screen.queryByText("Own sound for Hades", { selector: ".deq-small" })).toBeNull();  // The switch says it; the card does not repeat it.
  await everyday("Cancel");
  expect((screen.getByRole("switch", { name: "Own sound for Hades" }) as HTMLInputElement).checked).toBe(true);
  open("Presets");
  fireEvent.click(screen.getByRole("button", { name: "Forget Hades" }));
  await tick(0);
  fireEvent.click(screen.getByRole("button", { name: "Forget" }));
  await tick(100);
  expect(decky.perform).toHaveBeenCalledWith("forget_game", { appid: "1145360", scope: "1145360" }, expect.any(Number));
  delete Router.MainRunningApp;
  await tick(4000);
  expect(decky.setGame).toHaveBeenLastCalledWith("", "");
});

it("gives a game back the everyday sound when its own sound is turned off", async () => {
  await setup(true);
  Router.MainRunningApp = { appid: 1145360, display_name: "Hades" };
  await tick(4000);
  fireEvent.click(screen.getByRole("switch", { name: "Own sound for Hades" }));
  await tick(100);
  await everyday("Forget");
  expect(decky.perform).toHaveBeenLastCalledWith("own_sound", { enabled: false, appid: "1145360", scope: "1145360" }, expect.any(Number));
  expect(engine.value.state.scope).toBe("");
  expect(engine.value.state.games).toEqual({});
  expect((screen.getByRole("switch", { name: "Own sound for Hades" }) as HTMLInputElement).checked).toBe(false);
  expect(screen.queryByText("Own sound for Hades", { selector: ".deq-small" })).toBeNull();
  expect(screen.getByText("This game uses your everyday sound.")).toBeTruthy();
});

it("says a saved game sound could not be applied when its switch was refused", async () => {
  await setup(true);
  engine.value.state.games["7"] = { name: "Celeste" };  // Saved earlier.
  decky.setGame.mockImplementation(async (appid: string, name: string) => {
    await engine.setGame(appid, name);
    return { ok: false, error: "Filter busy" };
  });
  Router.MainRunningApp = celeste;
  await tick(4000);
  expect(toaster.toast).toHaveBeenCalledWith({ title: "DeckyEQ", body: "The game's sound could not be applied." });
  expect(toaster.toast).not.toHaveBeenCalledWith(expect.objectContaining({ body: expect.stringContaining("Filter busy") }));  // No raw tool text.
  expect(screen.getByText("Its saved sound could not be applied. Turn on to try again.")).toBeTruthy();
  expect((screen.getByRole("switch", { name: "Own sound for Celeste" }) as HTMLInputElement).checked).toBe(false);
});

it("reports the running game again after the backend restarted on its own", async () => {
  await setup(true);
  Router.MainRunningApp = celeste;
  await tick(4000);
  expect(decky.setGame).toHaveBeenCalledTimes(2);  // "No game" at load, then Celeste.
  engine.value.game = null;  // A new backend starts without a game; the panel stays.
  await tick(4000);
  expect(decky.setGame).toHaveBeenCalledTimes(2);  // Checked every 30 s, not every tick during play.
  await tick(28000);
  expect(decky.setGame).toHaveBeenCalledTimes(3);
  expect(decky.setGame).toHaveBeenLastCalledWith("7", "Celeste");
  expect(engine.value.game).toMatchObject({ appid: "7" });
  await tick(6000);
  expect(decky.setGame).toHaveBeenCalledTimes(3);  // Once reported, it is left alone.
});

it("says once when a game's sound could not be applied and does not retry every tick", async () => {
  await setup(true);
  expect(decky.setGame).toHaveBeenCalledWith("", "");  // At load: no game, whatever a reboot left active.
  decky.setGame.mockResolvedValue({ ok: false, error: "Filter busy" });
  Router.MainRunningApp = celeste;
  await tick(4000);
  expect(toaster.toast).toHaveBeenCalledWith({ title: "DeckyEQ", body: "The game's sound could not be applied." });
  await tick(2500);
  expect(decky.setGame).toHaveBeenCalledTimes(2);
});

it("says it only for the first refusal of a game, not for every retry", async () => {
  vi.mocked(toaster.toast).mockClear();
  await setup(true);
  decky.setGame.mockResolvedValue({ ok: false, error: "Filter busy" });
  Router.MainRunningApp = celeste;
  await tick(4000);
  await tick(32000);
  expect(decky.setGame.mock.calls.map(call => call[0])).toEqual(["", "7", "7"]);  // Retried after 30 s ...
  expect(toaster.toast).toHaveBeenCalledOnce();  // ... quietly.
});

it("tries again on the next tick when the backend did not answer the game at all", async () => {
  await setup(true);
  decky.setGame.mockRejectedValueOnce(new Error("not up yet"));
  Router.MainRunningApp = celeste;
  await tick(4000);
  expect(decky.setGame).toHaveBeenCalledTimes(2);
  await tick(2000);
  expect(decky.setGame).toHaveBeenCalledTimes(3);  // Two seconds later, not thirty.
  expect(decky.setGame).toHaveBeenLastCalledWith("7", "Celeste");
});

it("stops following the running game once the plugin unloads", async () => {
  const { plugin } = await setup(true);
  plugin.onDismount();
  Router.MainRunningApp = celeste;
  await tick(6000);
  expect(decky.setGame).toHaveBeenCalledOnce();  // Only "no game" at load.
});

it("retries quietly when the backend failed to start: the panel says why, no toast for every game", async () => {
  vi.mocked(toaster.toast).mockClear();
  const failed = "DeckyEQ must not run as root. Reinstall it so Decky starts it as your user.";
  decky.status.mockRejectedValue(new Error(failed));  // As main.py answers every call after a failed start.
  decky.setGame.mockResolvedValue({ ok: false, error: failed });
  await setup(true);
  Router.MainRunningApp = celeste;
  await tick(4000);
  Router.MainRunningApp = { appid: 1145360, display_name: "Hades" };
  await tick(34000);
  expect(decky.setGame.mock.calls.map(call => call[0])).toEqual(["", "7", "1145360", "1145360"]);  // Retried every 30 s.
  expect(toaster.toast).not.toHaveBeenCalled();
  expect(screen.getByText("Could not load DeckyEQ")).toBeTruthy();
  expect(screen.getByText(new RegExp(failed.replace(/\./g, "\\.")))).toBeTruthy();
});

it("waits for a slow backend start before it gives up on the first status", async () => {
  decky.status.mockImplementation(() => new Promise(() => {}));  // The backend waits up to 30 s for its own start.
  await setup(true);
  await tick(34000);
  expect(screen.getByText("Loading DeckyEQ…")).toBeTruthy();
  expect(screen.queryByText(/plugin backend/)).toBeNull();
  await tick(2000);
  expect(screen.getByText("Could not load DeckyEQ")).toBeTruthy();
});

it("ignores a late answer for a game the user already left", async () => {
  await setup(true);
  let answerCeleste!: (result: unknown) => void;
  decky.setGame.mockImplementationOnce(() => new Promise(done => { answerCeleste = done; }))
    .mockImplementationOnce(() => new Promise(() => {}));  // Hades: still switching.
  Router.MainRunningApp = celeste;
  await tick(4000);
  Router.MainRunningApp = { appid: 1145360, display_name: "Hades" };
  await tick(4000);
  expect(decky.setGame).toHaveBeenCalledTimes(3);  // "No game", Celeste, Hades.
  await act(async () => answerCeleste({ ok: false, error: "Celeste could not switch" }));
  await tick(4000);
  expect(toaster.toast).not.toHaveBeenCalledWith({ title: "DeckyEQ", body: "Celeste could not switch" });
  expect(decky.setGame).toHaveBeenCalledTimes(3);  // Hades is not sent twice.
});

it("does not report a game again while its first report is still being applied", async () => {
  await setup(true);
  let answer!: (result: unknown) => void;
  decky.setGame.mockImplementationOnce(() => new Promise(done => { answer = done; }));
  Router.MainRunningApp = celeste;
  await tick(4000);
  await tick(34000);  // Past the 30 s recheck; the backend has not got the game yet.
  expect(decky.setGame).toHaveBeenCalledTimes(2);  // "No game", then Celeste once.
  await act(async () => answer(await engine.setGame("7", "Celeste")));
});

it("ignores a one-tick blip of the running game but follows a real change after the second read", async () => {
  await setup(true);
  Router.MainRunningApp = celeste;
  await tick(4000);
  expect(decky.setGame).toHaveBeenCalledTimes(2);  // "No game" at load, then Celeste.
  delete Router.MainRunningApp;  // A launcher blip: gone for one read only.
  await tick(2000);
  Router.MainRunningApp = celeste;
  await tick(6000);
  expect(decky.setGame).toHaveBeenCalledTimes(2);
  Router.MainRunningApp = { appid: 1145360, display_name: "Hades" };
  await tick(2000);
  expect(decky.setGame).toHaveBeenCalledTimes(2);  // Seen once.
  await tick(2000);
  expect(decky.setGame).toHaveBeenCalledTimes(3);
  expect(decky.setGame).toHaveBeenLastCalledWith("1145360", "Hades");
  delete Router.MainRunningApp;  // The game really ended: gone on two reads.
  await tick(2000);
  expect(decky.setGame).toHaveBeenCalledTimes(3);
  await tick(2000);
  expect(decky.setGame).toHaveBeenLastCalledWith("", "");
});

const open = (title: string) => fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${title}`) }));

it("keeps the open page while QAM hides and shows again", async () => {
  const { visibility } = await setup();
  open("Output");
  visibility(false);
  visibility(true);
  expect(screen.getByRole("heading", { name: "Output" })).toBeTruthy();
  expect(decky.perform).not.toHaveBeenCalled();
  expect(decky.checkUpdates).not.toHaveBeenCalled();
});

it("keeps updates in About and retains a requested result while QAM is hidden", async () => {
  const { visibility, plugin } = await setup();
  open("Output");
  expect(screen.queryByRole("button", { name: "Check for updates" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "‹ DeckyEQ" }));
  open("About");
  expect(screen.getByText(/BSD 3-Clause/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Check for updates" }).compareDocumentPosition(screen.getByText(/^Version \d+\.\d+\.\d+$/)))
    .toBe(Node.DOCUMENT_POSITION_FOLLOWING);  // The page's one action comes before the facts.
  expect(screen.queryByText(/A live equalizer/)).toBeNull();
  expect(screen.getByText(/no extra audio packages\.$/)).toBeTruthy();
  let resolve!: (value: unknown) => void;
  decky.checkUpdates.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
  visibility(false);
  await act(async () => resolve({ success: true, current_version: "1.0.0", update_available: false, download_available: false, no_release: true }));
  visibility(true);
  expect(screen.getByText("No public release yet")).toBeTruthy();
  expect(decky.checkUpdates).toHaveBeenCalledOnce();
  plugin.onDismount();
  fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
  expect(decky.checkUpdates).toHaveBeenCalledOnce();
});

it("pauses hidden polling but accepts an explicit preset selection", async () => {
  const { visibility } = await setup();
  const selectPreset = decky.selections.get("Preset")!;
  vi.useFakeTimers();
  visibility(false);
  const reads = decky.status.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(decky.status).toHaveBeenCalledTimes(reads);
  await act(async () => { selectPreset({ data: "go-s-original" }); await vi.advanceTimersByTimeAsync(100); });
  expect(engine.value.state.selected).toBe("go-s-original");
  visibility(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect((screen.getByRole("combobox", { name: "Preset" }) as HTMLSelectElement).value).toBe("go-s-original");
});

it("treats a closed file picker as a cancel, not an error", async () => {
  vi.mocked(openFilePicker).mockRejectedValueOnce("User canceled");
  await setup();
  open("Presets");
  fireEvent.click(screen.getByRole("button", { name: "Show import or export" }));
  fireEvent.click(screen.getByRole("button", { name: "Import presets…" }));
  await waitFor(() => expect(openFilePicker).toHaveBeenCalled());
  await waitFor(() => expect((screen.getByRole("button", { name: "Import presets…" }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(decky.perform.mock.calls.some(call => call[0] === "import")).toBe(false);
});
