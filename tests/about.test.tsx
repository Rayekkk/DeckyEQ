import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AboutPage, UpdateCheck, UpdateClient, UpdateController, UpdateDownload, UpdateSection } from "../src/about";
import { version } from "../package.json";

const installed = `Installed version ${version}`;

const sessions: UpdateController[] = [];
afterEach(() => { cleanup(); sessions.splice(0).forEach(session => session.dispose()); });
const available: UpdateCheck = { ok: true, current_version: "1.0.0", latest_version: "1.0.1", update_available: true, download_available: true, size: 2097152 };
function setup(client: UpdateClient = {
  check: vi.fn().mockResolvedValue({ ...available, no_release: true, update_available: false, download_available: false }),
  download: vi.fn(),
}) {
  const controller = new UpdateController(client);
  sessions.push(controller);
  return { client, controller, page: render(<UpdateSection controller={controller} version={version} />) };
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
  await click("Check for updates");
  expect(client.check).toHaveBeenCalledOnce();
  expect(screen.getByText("No public release yet")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
});

it("downloads the selected version once and retains progress across page remounts", async () => {
  const pending = deferred<UpdateDownload>();
  const client = { check: vi.fn().mockResolvedValue(available), download: vi.fn(() => pending.promise) };
  const { controller, page } = setup(client);
  await click("Check for updates");
  await click("Download 1.0.1");
  await click("Downloading ZIP…");
  expect(client.download).toHaveBeenCalledExactlyOnceWith("1.0.1");
  page.unmount();
  const reopened = render(<UpdateSection controller={controller} version={version} />);
  expect((screen.getByRole("button", { name: "Check for updates" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Downloading ZIP…")).toBeTruthy();
  await act(async () => pending.resolve({ ok: true, version: "1.0.1", path: "/home/deck/Downloads/DeckyEQ-1.0.1.zip" }));
  expect(screen.getByText("Saved to /home/deck/Downloads/DeckyEQ-1.0.1.zip.")).toBeTruthy();
  expect(screen.getByText("To install it, turn on Developer mode in Decky settings → General, then use Developer → Install plugin from ZIP.")).toBeTruthy();
  expect(screen.getByText("1.0.1 downloaded")).toBeTruthy();
  expect(screen.queryByText("Update available")).toBeNull();  // The downloaded ZIP replaces the offer.
  expect(screen.queryByRole("button", { name: "Download 1.0.1" })).toBeNull();
  reopened.unmount();
  render(<UpdateSection controller={controller} version={version} />);
  expect(screen.getByText("1.0.1 downloaded")).toBeTruthy();
});

it("does not offer a download for equal, older or unverifiable releases", async () => {
  setup({ check: vi.fn()
    .mockResolvedValueOnce({ ...available, latest_version: "1.0.0", update_available: false, download_available: false })
    .mockResolvedValueOnce({ ...available, latest_version: "0.9.0", update_available: false, download_available: false })
    .mockResolvedValueOnce({ ...available, download_available: false, message: "Missing trusted digest." }), download: vi.fn() });
  await click("Check for updates");
  expect(screen.getByText("Up to date")).toBeTruthy();
  await click("Check for updates");
  expect(screen.getByText("No newer release")).toBeTruthy();
  await click("Check for updates");
  expect(screen.getByText("Version 1.0.1: Missing trusted digest. Check again later.")).toBeTruthy();  // A note, not a failure; one full stop.
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
});

it("serializes repeated checks and allows retry after an error", async () => {
  const pending = deferred<UpdateCheck>();
  const client = { check: vi.fn(() => pending.promise), download: vi.fn() };
  const { controller } = setup(client);
  await click("Check for updates");
  await act(async () => { void controller.run("check"); });
  expect(client.check).toHaveBeenCalledOnce();
  await act(async () => pending.resolve({ ...available, ok: false, error: "GitHub rate limit reached." }));
  expect(screen.getByRole("alert").textContent).toMatch(/rate limit/);
  client.check.mockResolvedValue(available);
  await click("Check for updates");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Download 1.0.1" })).toBeTruthy();
});

it("does not treat a failed or mismatched download as completed", async () => {
  setup({ check: vi.fn().mockResolvedValue(available), download: vi.fn()
    .mockResolvedValueOnce({ ok: false, error: "Release changed. Check again." })
    .mockResolvedValueOnce({ ok: true, version: "1.0.2", path: "/wrong.zip" }) });
  await click("Check for updates");
  await click("Download 1.0.1");
  expect(screen.getByRole("alert").textContent).toMatch(/Release changed/);
  await click("Download 1.0.1");
  expect(screen.getByRole("alert").textContent).toMatch(/Could not confirm/);
  expect(screen.queryByText(/ downloaded$/)).toBeNull();
});

it("ignores completion from an unloaded plugin session", async () => {
  const pending = deferred<UpdateCheck>();
  const { controller, page } = setup({ check: vi.fn(() => pending.promise), download: vi.fn() });
  await click("Check for updates");
  controller.dispose();
  page.unmount();
  setup();
  await act(async () => pending.resolve(available));
  expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
  expect(screen.getByText(installed)).toBeTruthy();
});

it("removes a stale download notice when the public release changes", async () => {
  const client = { check: vi.fn().mockResolvedValue(available),
    download: vi.fn().mockResolvedValue({ ok: true, version: "1.0.1", path: "/Downloads/DeckyEQ-1.0.1.zip" }) };
  setup(client);
  await click("Check for updates");
  await click("Download 1.0.1");
  client.check.mockResolvedValue({ ...available, latest_version: "1.0.2" });
  await click("Check for updates");
  expect(screen.queryByText(/ downloaded$/)).toBeNull();
  expect(screen.getByRole("button", { name: "Download 1.0.2" })).toBeTruthy();
});

it("drops an old offer when a later check fails", async () => {
  const client = { check: vi.fn().mockResolvedValueOnce(available).mockRejectedValueOnce(new Error("rate limit")), download: vi.fn() };
  setup(client);
  await click("Check for updates");
  expect(screen.getByRole("button", { name: "Download 1.0.1" })).toBeTruthy();
  await click("Check for updates");
  expect(screen.getByRole("alert").textContent).toBe("rate limit");
  expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
});

it("describes each state in UI.md's words, without repeating what the button says", async () => {
  const client = { check: vi.fn().mockResolvedValueOnce({ ...available, no_release: true, update_available: false, download_available: false })
    .mockResolvedValueOnce(available), download: vi.fn().mockResolvedValue({ ok: true, version: "1.0.1", path: "/Downloads/DeckyEQ-1.0.1.zip" }) };
  setup(client);
  expect(screen.getByText(installed)).toBeTruthy();
  expect(screen.getByText("Check for updates when you need them.")).toBeTruthy();
  await click("Check for updates");
  expect(screen.getByText("No public release yet").closest(".preview-field")!.querySelector("small")).toBeNull();
  await click("Check for updates");
  expect(screen.getByText("Update available")).toBeTruthy();
  expect(screen.getByText("Version 1.0.1 (2.0 MB).")).toBeTruthy();
  await click("Download 1.0.1");
  expect(screen.getByText("Saved to /Downloads/DeckyEQ-1.0.1.zip.")).toBeTruthy();
});

it("reads answers wrapped as D2 state too, as Legion Go 2 Companion's backend sends them", async () => {
  const { ok, ...fields } = available;
  setup({ check: vi.fn().mockResolvedValue({ ok, state: fields }),
    download: vi.fn().mockResolvedValue({ ok: true, state: { version: "1.0.1", path: "/home/deck/Downloads/DeckyEQ-1.0.1.zip" } }) });
  await click("Check for updates");
  await click("Download 1.0.1");
  expect(screen.getByText("1.0.1 downloaded")).toBeTruthy();
});

it("lays out About with the plugin's info first and Updates at the very bottom", () => {
  const controller = new UpdateController({ check: vi.fn(), download: vi.fn() });
  sessions.push(controller);
  const { container } = render(<AboutPage name="DeckyEQ" version="1.1.0" description="A live equalizer." license="BSD-3-Clause"
    source="github.com/Rayekkk/DeckyEQ" notes="Notices are included." credits={[["PipeWire", "Runs the filters."]]} updates={controller}>
    <section><h2>Extra</h2></section></AboutPage>);
  expect(Array.from(container.querySelectorAll("h2"), h => h.textContent)).toEqual(["DeckyEQ", "Extra", "Rayek's store", "Updates"]);
  expect(screen.getByText("Version 1.1.0")).toBeTruthy();
  expect(screen.getByText("Rayek · BSD-3-Clause · github.com/Rayekkk/DeckyEQ")).toBeTruthy();
  expect(screen.getByText("Runs the filters.")).toBeTruthy();
});

it("says a check could not complete, and shows the ZIP from the home folder", async () => {
  const client = { check: vi.fn().mockRejectedValueOnce(new Error("rate limit")).mockResolvedValue(available),
    download: vi.fn().mockResolvedValue({ ok: true, version: "1.0.1", path: "/home/deck/Downloads/DeckyEQ-1.0.1.zip" }) };
  const controller = new UpdateController(client);
  sessions.push(controller);
  render(<UpdateSection controller={controller} version={version} home="/home/deck" />);
  await click("Check for updates");
  expect(screen.getByText("Could not complete")).toBeTruthy();
  expect(screen.getByText(installed)).toBeTruthy();
  await click("Check for updates");
  await click("Download 1.0.1");
  expect(screen.getByText("Saved to ~/Downloads/DeckyEQ-1.0.1.zip.")).toBeTruthy();  // Not the user's name.
});
