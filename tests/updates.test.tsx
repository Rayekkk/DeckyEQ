import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UpdateCheck, UpdateClient, UpdateController, UpdateDownload, UpdateSection } from "../src/updates";
import { version } from "../package.json";

const installed = new RegExp(`Installed version: ${version.replace(/\./g, "\.")}`);

const sessions: UpdateController[] = [];
afterEach(() => { cleanup(); sessions.splice(0).forEach(session => session.dispose()); });
const available: UpdateCheck = { success: true, current_version: "1.0.0", latest_version: "1.0.1", update_available: true, download_available: true, size: 2097152 };
function setup(client: UpdateClient = {
  check: vi.fn().mockResolvedValue({ ...available, no_release: true, update_available: false, download_available: false }),
  download: vi.fn(),
}) {
  const controller = new UpdateController(client);
  sessions.push(controller);
  return { client, controller, page: render(<UpdateSection controller={controller} />) };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  return { promise: new Promise<T>(done => { resolve = done; }), resolve };
}
async function click(label: string) {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: label })); });
}

it("checks only on request and handles an unpublished repository", async () => {
  const { client } = setup();
  expect(client.check).not.toHaveBeenCalled();
  expect(screen.getByText(installed)).toBeTruthy();
  await click("Check for Updates");
  expect(client.check).toHaveBeenCalledOnce();
  expect(screen.getByText("No public release yet")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
});

it("downloads the selected version once and retains progress across page remounts", async () => {
  const pending = deferred<UpdateDownload>();
  const client = { check: vi.fn().mockResolvedValue(available), download: vi.fn(() => pending.promise) };
  const { controller, page } = setup(client);
  await click("Check for Updates");
  await click("Download 1.0.1");
  await click("Downloading ZIP…");
  expect(client.download).toHaveBeenCalledExactlyOnceWith("1.0.1");
  page.unmount();
  const reopened = render(<UpdateSection controller={controller} />);
  expect((screen.getByRole("button", { name: "Check for Updates" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Downloading ZIP…")).toBeTruthy();
  await act(async () => pending.resolve({ success: true, version: "1.0.1", path: "/home/deck/Downloads/DeckyEQ-1.0.1.zip" }));
  expect(screen.getByText("/home/deck/Downloads/DeckyEQ-1.0.1.zip")).toBeTruthy();
  expect(screen.getByText(/Install the ZIP through Decky/)).toBeTruthy();
  expect(screen.getByText("Ready to install")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Download 1.0.1" })).toBeNull();
  reopened.unmount();
  render(<UpdateSection controller={controller} />);
  expect(screen.getByText("Version 1.0.1 downloaded")).toBeTruthy();
});

it("does not offer a download for equal, older or unverifiable releases", async () => {
  setup({ check: vi.fn()
    .mockResolvedValueOnce({ ...available, latest_version: "1.0.0", update_available: false, download_available: false })
    .mockResolvedValueOnce({ ...available, latest_version: "0.9.0", update_available: false, download_available: false })
    .mockResolvedValueOnce({ ...available, download_available: false, error: "Missing trusted digest." }), download: vi.fn() });
  await click("Check for Updates");
  expect(screen.getByText("Up to date")).toBeTruthy();
  await click("Check for Updates");
  expect(screen.getByText("No newer release")).toBeTruthy();
  await click("Check for Updates");
  expect(screen.getByText(/Missing trusted digest\./)).toBeTruthy();  // A note about the release, not a failure.
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
});

it("serializes repeated checks and allows retry after an error", async () => {
  const pending = deferred<UpdateCheck>();
  const client = { check: vi.fn(() => pending.promise), download: vi.fn() };
  const { controller } = setup(client);
  await click("Check for Updates");
  await act(async () => { void controller.run("check"); });
  expect(client.check).toHaveBeenCalledOnce();
  await act(async () => pending.resolve({ ...available, success: false, error: "GitHub rate limit reached." }));
  expect(screen.getByRole("alert").textContent).toMatch(/rate limit/);
  client.check.mockResolvedValue(available);
  await click("Check for Updates");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Download 1.0.1" })).toBeTruthy();
});

it("does not treat a failed or mismatched download as completed", async () => {
  setup({ check: vi.fn().mockResolvedValue(available), download: vi.fn()
    .mockResolvedValueOnce({ success: false, error: "Release changed. Check again." })
    .mockResolvedValueOnce({ success: true, version: "1.0.2", path: "/wrong.zip" }) });
  await click("Check for Updates");
  await click("Download 1.0.1");
  expect(screen.getByRole("alert").textContent).toMatch(/Release changed/);
  await click("Download 1.0.1");
  expect(screen.getByRole("alert").textContent).toMatch(/Could not confirm/);
  expect(screen.queryByText(/Version .* downloaded/)).toBeNull();
});

it("ignores completion from an unloaded plugin session", async () => {
  const pending = deferred<UpdateCheck>();
  const { controller, page } = setup({ check: vi.fn(() => pending.promise), download: vi.fn() });
  await click("Check for Updates");
  controller.dispose();
  page.unmount();
  setup();
  await act(async () => pending.resolve(available));
  expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
  expect(screen.getByText(installed)).toBeTruthy();
});

it("removes a stale download notice when the public release changes", async () => {
  const client = { check: vi.fn().mockResolvedValue(available),
    download: vi.fn().mockResolvedValue({ success: true, version: "1.0.1", path: "/Downloads/DeckyEQ-1.0.1.zip" }) };
  setup(client);
  await click("Check for Updates");
  await click("Download 1.0.1");
  client.check.mockResolvedValue({ ...available, latest_version: "1.0.2" });
  await click("Check for Updates");
  expect(screen.queryByText(/Version .* downloaded/)).toBeNull();
  expect(screen.getByRole("button", { name: "Download 1.0.2" })).toBeTruthy();
});
