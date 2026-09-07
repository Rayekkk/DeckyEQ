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
const files: FileUI = {
  async importFile(home) {
    const result = await openFilePicker(FileSelectionType.FILE, home, true, false, undefined, ["json"], false, false);
    return result?.realpath || result?.path || null;
  },
  async exportFolder(home) {
    const result = await openFilePicker(FileSelectionType.FOLDER, home, false, true);
    return result?.realpath || result?.path || null;
  },
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
