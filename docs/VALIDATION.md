# Validation record

Initial development and UI preview ran on Windows. After the user authorized
installation, native verification and deployment ran on Legion Go 2 on 2026-09-07.

The public release version is **1.0.0**. The 1.0.0, 1.1.0 and 1.1.1 numbers
in the historical device records below identify internal development builds,
not published releases. The public build includes the later audio and dropdown
fixes. Its GitHub preparation and updater checks run locally; that prepared
archive has not been installed on the device.

## Unreleased hardening (not yet validated on a device)

- `npm run check` passes: 119 Python tests on Windows (13 POSIX/symlink tests
  skip there; all 119 pass on Linux), 32 UI tests, the production build and
  packaging.
- Covered by tests with a protocol fake, not by hardware: bypass when the
  physical output is chosen in Steam, the restore journal surviving an
  interrupted stop, no takeover of Bluetooth playback on enable, idle polling
  and retry backoff, the +24 dB manual boost cap, stable filter frequency
  ranges and settings recovery from malformed values.
- Hardware acceptance on the Legion Go is still required before release.

## Public 1.0.0 local preparation

- `npm run check` passes type checking, Python tests, UI tests, the production
  Rollup build and deterministic packaging. The Windows run discovers 101 Python
  tests: 89 pass and 12 skip (11 POSIX download filesystem tests and one symlink
  test requiring host privileges). All 28 UI tests pass.
- About contains the version, author, BSD 3-Clause license and explicit GitHub
  update controls. The actual plugin factory is tested through native-menu hide
  callbacks for all four View options; About is preserved along with the other
  pages. Update progress survives hidden QAM and page changes.
- Adapted updater tests cover stable releases, an unpublished repository,
  network errors, caching, concurrent requests, release changes, SHA-256 and
  archive validation, and cancellation. The real backend RPC entry points remain
  responsive during a slow update check and work after an audio startup failure.
- The prepared ZIP passes the same archive validator used by the updater. Its
  runtime, frontend source, artwork, license and manifests match the local files.
  Preset exchange remains schema version 2; the product version is 1.0.0.
- The About preview and no-release response were inspected at a 300 px panel
  width with no horizontal overflow. The preview uses simulated update responses;
  this does not verify a real GitHub download on SteamOS.
- No console installation, Git commit, tag or public release was performed during
  this local preparation. Linux filesystem download tests and native UI acceptance
  remain outside this Windows run.

## Automated local coverage

- Python validation and storage: parameter ranges, non-finite values, oversized
  input, duplicate JSON keys, schema versions, library limits, atomic bank import,
  exports without overwrite, failed disk commit and recovery from last-good state.
- Native graph contract: built-in filters only, stereo graph duplication,
  named runtime controls, SPA parameter struct, separate tone shelves and
  headroom for overlapping positive bands.
- Service transactions: activation, live edits, persistence failure, partial
  audio failure, rollback, stale revisions and cancellation while a write is active.
- Routing with a protocol fake: existing/new playback, manual output changes,
  missing graph, changed controls, failed activation and foreign-node rejection.
- Runtime regressions: private configuration basename, isolated system Python,
  clean shared-library environment, active idle graph and verifying filters
  before raising the final input level.
- React/controller behavior: coalescing, serialized writes, draft flush before
  save/export, delayed status responses, disconnects, rejected edits, plugin
  unload, QAM remounts, preset confirmation and file-picker integration.
- TypeScript typecheck, production Rollup build and deterministic ZIP validation.
- Browser UI inspection of Equalizer, Presets and Output using a simulated backend.

The symbolic-link test is skipped on Windows hosts without symlink creation
privileges. It runs on a normal Linux development host.

The audio protocol tests use fake responses. They verify control flow and failure
handling. Native evidence from the later authorized device session follows.

## Development dropdown regression (internal build 1.1.1)

The test uses the actual plugin descriptor and simulates Decky's unmount-on-hide
behavior, including delivery of a native menu callback while QAM is hidden.
Before the fix, View reset to Equalizer and the advanced-band selector vanished.
The plugin now sets `alwaysRender: true`, matching Legion Go 2 Companion.
Visibility continues to gate polling, without blocking explicit selections.

Regression coverage includes all three View pages, advanced band/type changes,
hidden polling and a preset choice delivered while QAM is hidden. Existing
browser-only dropdown tests did not exercise the surrounding Decky lifecycle.

## Development audio acceptance (internal build 1.1.0)

- 61 Python tests passed on the Legion, with no skips. On Windows the same
  suite has one host-specific symlink skip. All 17 React/controller tests,
  TypeScript checking and the production build passed.
- The original Go S configuration was loaded in a separate native probe.
  PipeWire read `62.5.0` as 0 Hz and clamped 80000 Hz to 48000 Hz. The built-in
  preset preserves these parsed settings; full parameters and sources are in
  `GO_S_PRESET.md`.
- A private native graph verified all three filter types, switching them live,
  the original +8 dB preset, and startup at an attenuated level. The same child
  PID survived the changes. No default routing or user volume changed in this probe.
- Native lifecycle verification confirmed hardware at 100%, the preceding
  hardware volume on the EQ sink, both stereo output links and restoration.
- A localhost WebSocket test called the **installed Decky backend**, verified
  the factory preset, enabled an attenuated copy, read back the real controls,
  changed filter type without changing the graph serial, and confirmed full
  hardware volume. After setting different left/right virtual volumes, disabling
  EQ transferred both values correctly to hardware. The user's original draft,
  library, selection, disabled state and volume were then restored.
- Earlier RPC verification saved, exported and imported the Go S preset; all
  numeric values and filter types survived the round trip exactly.
- Updated QAM navigation uses one View dropdown. Action buttons use the native
  below-label, full-width layout. The browser adapter was visually inspected at
  a 300 px panel width. This supplements, rather than claims to replace, checking
  Steam's live layout and controller navigation.

Native testing exposed two integration cases absent from the initial fake:
`pactl` returns a comma-separated channel map on this device, and `pw-dump`
rounds SPA floats to six decimal places. Both are handled, with regression tests.
Readback tolerance includes half the last printed decimal unit; it does not
claim exact binary equality of the underlying audio samples.

## Initial development device record (internal build 1.0.0)

Device: Lenovo Legion Go 8ASP2 / 83N0, SteamOS 3.10, kernel
7.2.0-valve1-1-neptune-72-gd39b4282853d, PipeWire 1.6.8, system Python 3.14.
The active port was the internal speaker port. The stock system supplied every
required tool; no audio dependency was installed.

- The private client graph started under UID 1000. All ten gains, band Q, an
  adjusted frequency, both shelves and headroom were changed and read back from
  PipeWire. The child PID stayed unchanged, and both playback channels had
  active links to the hardware output.
- Stopping removed the virtual sink and restored the original default output.
  Hardware volume remained at its original 70% on both channels in the initial internal build.
  The later audio build deliberately manages hardware volume as described above.
- Installation was staged outside Decky's scan directory. Decky was stopped and
  its service cgroup drained before the plugin directory was replaced.
- The installed backend was identified through its `/proc` command line and log;
  every packaged file was compared against the installed bytes.
- Real Decky RPC enabled EQ, applied a draft, saved a preset, exported it and
  imported it. Imported EQ values matched exactly. Test presets were removed and
  the initial disabled / Flat state was restored.

The enable-plus-edit RPC check completed in 562 ms on this run. This is a single
integration observation, not an audio-latency measurement or performance promise.
Local outputs are under `artifacts/live/`; they are excluded from the ZIP.

Native testing caught and fixed configuration path handling and stale control
readback while idle. Testing inside Decky additionally caught the frozen-loader
executable issue that a terminal run could not expose.

## Required before calling the release hardware-validated

Basic setup, control readback, preset exchange and restoration below have been
checked as described above. Listening, native controller navigation, game playback,
headphone transitions, power use and suspend/resume remain to be checked:

1. Record system versions, default output, active port and existing audio filters.
2. Install the ZIP; verify that a stock system meets the reported tool requirements.
3. Enable EQ and check the live child command line, process UID, owned sink and
   routing of a game that was already playing. Confirm no feedback loop.
4. Change bass, treble, each band, frequency and Q. Verify actual PipeWire control
   readback and audible behavior, including rapid slider drags and no graph restart.
5. Save, switch and export presets; import the same file and confirm equal controls.
6. Repeat at quiet and normal listening levels, with headroom enabled. Evaluate
   transition clicks, latency and CPU use without assuming Windows parity.
7. Close/reopen QAM, restart Decky, suspend/resume and restart PipeWire. Confirm
   truthful status and recovery of the most recent draft and selected preset.
8. Insert/remove wired headphones and switch Bluetooth/HDMI outputs. Confirm
   the intended bypass/restore behavior, including the polling transition interval.
9. Disable/unload/uninstall while a game is playing. Verify real audio returns,
   the current virtual volume is transferred back and no DeckyEQ child or virtual
   sink remains. Check failed activation and restoration from the volume journal.

Treat any failed check as a defect to resolve before publishing a verified release.
