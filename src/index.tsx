import { callable, definePlugin, FileSelectionType, openFilePicker, useQuickAccessVisible } from "@decky/api";
import { Router, staticClasses } from "@decky/ui";
import { App } from "./App";
import { EqualizerIcon, FileUI } from "./shared";
import { Controller } from "./controller";
import { Result, Snapshot } from "./types";
import { UpdateCheck, UpdateController, UpdateDownload } from "./about";
import { notify } from "./ui";

// A backend that never answers must not lock the panel: the controller treats a timeout like a
// lost connection, reports the change as not confirmed and polls again.
const within = <T,>(seconds: number, request: Promise<T>) => new Promise<T>((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("DeckyEQ did not answer in time.")), seconds * 1000);
  request.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
});
const getStatus = callable<[], Result>("get_status");
const performAction = callable<[string, Record<string, unknown>, number], Result>("perform");
// The backend itself waits up to 30 s for its start. A refusal (still starting, failed to start) is shown like a
// lost connection: the panel says why and polls again.
const status = () => within(35, getStatus()).then((result): Snapshot => {
  if (!result.ok || !result.state) throw new Error(result.error || "DeckyEQ sent no settings.");
  return result.state;
});
const perform = (action: string, payload: Record<string, unknown>, revision: number) =>
  within(45, performAction(action, payload, revision));  // Long enough for a filter rebuild.
const setGame = callable<[string, string], Result>("set_game");
const GAME_POLL_MS = 2000, GAME_RETRY_MS = 30_000;
const checkUpdates = callable<[], UpdateCheck>("updates_check");
const downloadUpdate = callable<[string], UpdateDownload>("updates_download");
// Decky rejects the picker promise when the user closes it; that is not an error.
const picked = (request: ReturnType<typeof openFilePicker>) =>
  request.then(result => result?.realpath || result?.path || null, () => null);
const files: FileUI = {
  importFile: home => picked(openFilePicker(FileSelectionType.FILE, home, true, false, undefined, ["json"], false, false)),
  exportFolder: home => picked(openFilePicker(FileSelectionType.FOLDER, home, false, true)),
};

const toast = (body: string) => notify("DeckyEQ", body);

export default definePlugin(() => {
  const controller = new Controller({ status, perform });
  const updates = new UpdateController({ check: checkUpdates, download: downloadUpdate }, "deckyeq");
  // Runs while the QAM is closed too: a game with its own sound gets it within two seconds of starting.
  // null: not reported yet, so the first tick also reports "no game" (a reboot may have left one active).
  // A change of game (or none) counts only once two reads in a row agree: launchers and restarts blip.
  let reported: string | null = null, refused: string | null = null, retryAt = 0, recheckAt = 0, closed = false, sending = false, request = 0;
  let seen: string | null = null, pending: string | null = null;
  const check = () => {
    let app: typeof Router.MainRunningApp;
    try { app = Router.MainRunningApp; } catch { return; }  // A Steam update may move the router.
    const appid = app ? String(app.appid) : "";
    if (appid === seen) pending = null;
    else if (seen !== null && pending !== appid) { pending = appid; return; }
    if (appid === reported) {  // A backend that restarted alone forgot the game: report it again.
      if (!appid || sending || Date.now() < recheckAt) return;  // Rare: no status call every tick during play.
      recheckAt = Date.now() + GAME_RETRY_MS;
      void controller.refresh().then(() => {
        if (!closed && !sending && reported === appid && (controller.getSnapshot().snapshot?.game?.appid ?? "") !== appid) reported = null;
      });
      return;
    }
    if (appid === refused && Date.now() < retryAt) return;
    reported = seen = appid;
    pending = null;
    recheckAt = Date.now() + GAME_RETRY_MS;
    sending = true;
    const sent = ++request;  // A late answer for a game already left must not touch the current one's state.
    void within(45, setGame(appid, app?.display_name ?? "")).then(async result => {
      if (sent !== request) return;
      sending = false;
      if (closed) return;
      if (!result.ok && result.starting) { reported = null; return; }  // Not up yet: try again on the next tick.
      const first = refused !== appid;
      if (!result.ok) { reported = null; refused = appid; retryAt = Date.now() + GAME_RETRY_MS; }  // Retry every 30 s, not every tick.
      else refused = null;
      await controller.refresh();
      // e.g. the filter could not switch: say so once. A backend that failed to start refuses every game and its
      // status too; the panel says why, so the retries stay quiet.
      if (!result.ok && first && !closed && controller.getSnapshot().connected) toast("The game's sound could not be applied.");
    }, () => { if (sent === request) { sending = false; reported = null; } });  // The backend is not answering yet: try again next tick.
  };
  check();
  const watch = setInterval(check, GAME_POLL_MS);
  const Content = () => <App controller={controller} files={files} updates={updates} toast={toast} visible={useQuickAccessVisible()} />;
  return { name: "DeckyEQ", titleView: <div className={staticClasses.Title}>DeckyEQ</div>,
    // Steam's native dropdown menu temporarily hides QAM. Preserve the mounted
    // view and its selection callbacks; App gates only polling on visibility.
    alwaysRender: true,
    content: <Content />, icon: <EqualizerIcon />, onDismount: () => {
      closed = true; clearInterval(watch); updates.dispose(); controller.dispose();
    } };
});
