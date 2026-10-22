import { cloneElement, ReactElement } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PreviewEngine } from "../preview/engine";
import { openFilePicker } from "@decky/api";

const decky = vi.hoisted(() => ({
  visible: true,
  status: vi.fn(),
  perform: vi.fn(),
  checkUpdates: vi.fn(),
  downloadUpdate: vi.fn(),
  selections: new Map<string, (option: { data: unknown }) => void>(),
}));

vi.mock("@decky/api", () => ({
  definePlugin: (factory: unknown) => factory,
  callable: (name: string) => ({ get_status: decky.status, perform: decky.perform,
    updates_check: decky.checkUpdates, updates_download: decky.downloadUpdate })[name],
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
});
afterEach(() => {
  cleanup();
  plugins.splice(0).forEach(plugin => plugin.onDismount());
  vi.useRealTimers();
});

async function setup() {
  // Use the actual plugin descriptor, not a direct <App /> mount. Decky removes
  // the component tree on hide unless that descriptor opts into alwaysRender.
  const plugin = (factory as unknown as () => Plugin)();
  plugins.push(plugin);
  // Clone triggers the visibility-hook update without replacing the component.
  function Host() { return decky.visible || plugin.alwaysRender ? cloneElement(plugin.content) : null; }
  const rendered = render(<Host />);
  await screen.findByRole("combobox", { name: "Preset" });
  const visibility = (value: boolean) => { decky.visible = value; rendered.rerender(<Host />); };
  return { visibility, plugin };
}

const open = (title: string) => fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${title}`) }));

it("keeps the open page while QAM hides and shows again", async () => {
  const { visibility } = await setup();
  open("Output and level");
  visibility(false);
  visibility(true);
  expect(screen.getByRole("heading", { name: "Output and level" })).toBeTruthy();
  expect(decky.perform).not.toHaveBeenCalled();
  expect(decky.checkUpdates).not.toHaveBeenCalled();
});

it("keeps updates in About and retains a requested result while QAM is hidden", async () => {
  const { visibility, plugin } = await setup();
  open("Output and level");
  expect(screen.queryByRole("button", { name: "Check for Updates" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "‹ DeckyEQ" }));
  open("About");
  expect(screen.getByText(/BSD 3-Clause/)).toBeTruthy();
  let resolve!: (value: unknown) => void;
  decky.checkUpdates.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  fireEvent.click(screen.getByRole("button", { name: "Check for Updates" }));
  visibility(false);
  await act(async () => resolve({ success: true, current_version: "1.0.0", update_available: false, download_available: false, no_release: true }));
  visibility(true);
  expect(screen.getByText("No public release yet")).toBeTruthy();
  expect(decky.checkUpdates).toHaveBeenCalledOnce();
  plugin.onDismount();
  fireEvent.click(screen.getByRole("button", { name: "Check for Updates" }));
  expect(decky.checkUpdates).toHaveBeenCalledOnce();
});

it("keeps advanced band selection and applies filter choices while QAM is hidden", async () => {
  const { visibility } = await setup();
  open("Equalizer bands");
  fireEvent.click(screen.getByRole("button", { name: /^Edit one band/ }));
  const selectBand = decky.selections.get("Band")!;
  visibility(false);
  act(() => selectBand({ data: 5 }));
  visibility(true);
  expect((screen.getByRole("combobox", { name: "Band" }) as HTMLSelectElement).value).toBe("5");
  const selectType = decky.selections.get("Filter type")!;
  visibility(false);
  act(() => selectType({ data: "highshelf" }));
  await waitFor(() => expect(engine.value.state.working.bands[5].type).toBe("highshelf"));
  visibility(true);
  expect((screen.getByRole("combobox", { name: "Filter type" }) as HTMLSelectElement).value).toBe("highshelf");
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
  fireEvent.click(screen.getByRole("button", { name: "Import preset…" }));
  await waitFor(() => expect(openFilePicker).toHaveBeenCalled());
  await waitFor(() => expect((screen.getByRole("button", { name: "Import preset…" }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(decky.perform.mock.calls.some(call => call[0] === "import")).toBe(false);
});
