import { createRoot } from "react-dom/client";
import { App, EqualizerIcon } from "../src/App";
import { Controller } from "../src/controller";
import { UpdateController } from "../src/updates";
import { version } from "../package.json";
import { PreviewEngine } from "./engine";
import "./style.css";

const controller = new Controller(new PreviewEngine());
const updates = new UpdateController({
  check: async () => ({ success: true, current_version: version, update_available: false, download_available: false, no_release: true }),
  download: async () => ({ success: false, error: "Downloads are simulated in this preview." }),
});
createRoot(document.getElementById("root")!).render(<div className="preview-shell">
  <aside className="preview-context"><div className="preview-kicker">DECKYEQ / {version}</div>
    <h1>Make it<br /><span>your sound.</span></h1><p>A live equalizer, tone controls and a library of your own presets.</p>
    <div className="preview-caption">UI preview · simulated backend<br />No audio is processed in this browser.</div>
  </aside>
  <main className="preview-panel"><header><EqualizerIcon /><strong>DeckyEQ</strong><span>UI preview</span></header>
    <App controller={controller} updates={updates} files={{ importFile: async () => "/home/deck/preview.json", exportFolder: async home => home }} />
  </main>
</div>);
