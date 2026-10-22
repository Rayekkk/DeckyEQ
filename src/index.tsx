import { callable, definePlugin, FileSelectionType, openFilePicker, useQuickAccessVisible } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { App, EqualizerIcon, FileUI } from "./App";
import { Controller } from "./controller";
import { Result, Snapshot } from "./types";
import { UpdateCheck, UpdateController, UpdateDownload } from "./updates";

const status = callable<[], Snapshot>("get_status");
const perform = callable<[string, Record<string, unknown>, number], Result>("perform");
const checkUpdates = callable<[], UpdateCheck>("updates_check");
const downloadUpdate = callable<[string], UpdateDownload>("updates_download");
// Decky rejects the picker promise when the user closes it; that is not an error.
const picked = (request: ReturnType<typeof openFilePicker>) =>
  request.then(result => result?.realpath || result?.path || null, () => null);
const files: FileUI = {
  importFile: home => picked(openFilePicker(FileSelectionType.FILE, home, true, false, undefined, ["json"], false, false)),
  exportFolder: home => picked(openFilePicker(FileSelectionType.FOLDER, home, false, true)),
};

export default definePlugin(() => {
  const controller = new Controller({ status, perform });
  const updates = new UpdateController({ check: checkUpdates, download: downloadUpdate });
  const Content = () => <App controller={controller} files={files} updates={updates} visible={useQuickAccessVisible()} />;
  return { name: "DeckyEQ", titleView: <div className={staticClasses.Title}>DeckyEQ</div>,
    // Steam's native dropdown menu temporarily hides QAM. Preserve the mounted
    // view and its selection callbacks; App gates only polling on visibility.
    alwaysRender: true,
    content: <Content />, icon: <EqualizerIcon />, onDismount: () => { updates.dispose(); controller.dispose(); } };
});
