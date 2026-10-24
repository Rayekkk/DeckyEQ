# Validation record

Initial development and UI preview ran on Windows. After the user authorized
installation, native verification and deployment ran on Legion Go 2 on 2026-09-07.

The public release version is **1.0.0**. The 1.0.0, 1.1.0 and 1.1.1 numbers
in the historical device records below identify internal development builds,
not published releases. The public build includes the later audio and dropdown
fixes. Its GitHub preparation and updater checks run locally; that prepared
archive has not been installed on the device.

## 1.1.0 (unreleased)

The public update is numbered 1.1.0. The test device still runs the internal
build 1.1.1, which the in-plugin updater treats as newer, so install 1.1.0 there
from the ZIP (Decky Settings → General: Developer mode, then Developer → Install Plugin from ZIP).

- `npm run check` (type checking, Python and UI tests, production build and
  packaging) passes locally and in CI; POSIX-only tests are skipped on Windows
  and run on Linux. Test counts are left to the CI log rather than this record.
- Covered by tests with protocol fakes: the smart-filter configuration, start and
  stop without any change to outputs, streams or volumes, a clear error when
  WirePlumber does not insert the filter, readback and rollback after failed or
  interrupted writes, suspended-filter writes, ramped level changes, the
  combined-response headroom (checked against a dense brute-force scan and
  replayed by the frontend), settings recovery, deferred
  draft saving, and DeckyEQ 1.0 journal recovery that never blocks EQ.
- The panel was rebuilt as a main screen (EQ switch, volume boost, preset, bass,
  treble, stereo width) with
  rows opening Equalizer, Presets, Output, and About. UI tests
  cover navigation, B to go back and focus hand-off, the name dialog (refused
  names reopen it), toasts that do not repeat on remount, preset actions, band
  editing and selections made while QAM is hidden; the browser preview was
  inspected at the 300 px panel width. Gamepad navigation on the device is
  still to be checked, in particular that B stays inside the plugin.
- Stereo width and the look-ahead limiter (native LADSPA stage), measured on
  the Legion Go 2 through the real stereo graph as a smart filter into a float
  null sink with a -1 dBFS ceiling (shipped since the full audit; briefly 0 dBFS): a
  -1 dBFS 70 Hz tone with +9 dB bass reached +6.93 dBFS without the limiter and
  exactly -1.00 dBFS with it, also when switched on live; THD of a
  boosted 70 Hz tone 0.37–0.63% (the CAPS compressor shipped with SteamOS measured
  8–58%, so it was rejected). Width 0 produced identical channels, width 2 the
  computed -2.50 / -12.04 dBFS for a one-sided tone. All 198 controls read back
  and confirmed. CPU while playing: 2.1% of one core (1.8% before the stage);
  the volume boost (stage gain before the limiter) is covered by the DSP tests;
  idle remains 0% because the filter suspends with its output.
- After the full adversarial audit (four independent reviewers: security,
  backend, DSP, frontend) and a second independent re-audit, measured again on
  the Legion Go 2: input level in the stage, limiter always on with a 25 ms hold
  and a -1 dBFS ceiling, the fixed graph (three filters per band slot, so type
  and preset changes stay live; a one-filter-per-slot variant measured 1.0% CPU
  but let unequalized audio through on every type change and was reverted).
  A -1 dBFS tone with +9 dB bass peaked at -1.00 dBFS with 0.02% THD at 70 Hz
  (0.63% before the audit) and 0.00% at 40 Hz; width, boost and a band switched
  to a low shelf behaved as computed; a live fade to -96 dB reached -102 dBFS;
  all 195 controls read back and confirmed; CPU while playing 2.0% of one core.
  The plugin's start/stop fades and the B button were checked by ear in Game Mode.
- Two-band limiter, after the user heard dips "on lower notes": at the user's
  +11.4 dB stage level a single wideband limiter ducked a steady 1 kHz tone by up
  to 7.8 dB on every 70 Hz bass hit; with the bass band (below 150 Hz) limited on
  its own 3 dB under the ceiling, by under 2.5 dB, the measurement's floor
  (tests/test_dsp.py keeps it under 3 dB). The automatic-headroom switch was
  removed: headroom is always on and changed only ~0.6 dB on the Go S preset.
- Loudness, after the user found the post-audit build "twice as quiet as no EQ":
  their Go S preset (+12 dB input, 200% width) in the removed "make room" mode
  ran the stage at -13.1 dB. With headroom limited to EQ boosts above +6 dB, a
  music-like test signal measured RMS -13.1 dBFS without EQ, -13.1 with Flat,
  -8.5 with the factory Go S preset and -8.5 with the user's settings (stage
  level +11.4 dB), peaks at -1.0 dBFS throughout (Legion Go 2, float null sink).
- Third full audit (four fresh reviewers) fixed and measured on the Legion Go 2:
  the limiter's detector now sees peaks between samples (4x oversampled), so a
  12 kHz tone at 45 degrees with +6 dB boost, whose samples sat at -4.10 dBFS,
  peaked at -1.08 dBTP (8x-oversampled measurement); boosted bass THD 0.01% at
  70 Hz, 0.00% at 40 Hz; allowance then 3 dB (back to 6 dB after the listening test below); all 195 controls confirmed; CPU while
  playing 2.0% of one core. The plugin's neutral start/stop (filter linked in and
  out at flat, then stepped to the settings) needs a listening check in Game Mode.
- Checked on the device (below): routing, DSP level, untouched session, crash
  behaviour, CPU (0% idle, 1.8% of one core while playing) and the Game Mode
  basics. Still open: headphone and Bluetooth transitions, suspend/resume and a
  PipeWire restart.

The sections below record earlier builds as they were tested then. UI they name, such
as the View dropdown, the advanced band page, **Live EQ** or the auto-headroom note, has
since been replaced by the panel described in [USAGE.md](USAGE.md).

## Smart filter checks on the Legion Go 2 (2026-10-22)

SteamOS 3.10, PipeWire 1.6.8, WirePlumber 0.5.14. Run over SSH as the desktop user,
with the backend started directly (Decky installation of this build still pending).

- Routing, with temporary null sinks as targets: a stream pinned to the target and a
  stream following the default both pass the filter; a stream on another default
  output does not; switching the default to the target while playing moves the
  stream through the filter; `filter.smart.disabled` in the `filters` metadata
  bypasses and restores it live; killing the filter relinks the stream straight
  to the target. WirePlumber never selected the filter as the default output.
- DSP: a 1 kHz tone measured at the target's monitor rises by exactly 12.0 dB for a
  +12 dB bell, identical to the plain filter-chain. An auto-connected recorder aimed
  at the target is itself redirected to the filter input, so it measures pre-EQ audio.
- Real speaker (muted during the test): enable 0.22 s, slider edits with readback
  0.12-0.13 s; default output, volume, mute and active port unchanged after enable
  and after disable. Killing the backend left no filter node or process behind.
- `node.always-process` cost about 2.8% of one core while nothing played. Without
  it a suspended filter reports stale controls (written 6 dB, read 0 dB) but keeps
  the writes: a +12 dB bell written while suspended measured exactly +12 dB once
  audio flowed. The flag was removed; edits to a suspended filter are written
  without readback and verified by the monitor once it runs. Idle cost is now 0%,
  edits take 36-71 ms.
- Steam lists a virtual sink by its node name, so the filter's sink is named
  `DeckyEQ`. A sink with media class `Audio/Sink/Internal` would be hidden from
  Steam, but WirePlumber then no longer routes playback through the smart filter.
- SteamOS ships CAPS 0.9.26 LADSPA plugins (`/usr/lib/ladspa/caps.so`, including a
  compressor/limiter) that filter-chain can load; noted for a possible limiter mode.

Installed through Decky (`DeckyEQ 1.1.0 loaded ... audio=active`, also after a
reboot) and checked by the user in Game Mode: the panel loads, enabling EQ shows
**Live EQ**, Steam's output list shows **DeckyEQ** next to the speaker,
bass and treble changes are audible, the auto-headroom note appears, and the
volume buttons behave normally. Previous plugin kept as a backup on the device.

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
4. Change bass, treble, each band's gain, and in full screen its Q and filter type. Verify actual PipeWire control
   readback and audible behavior, including rapid slider drags and no graph restart.
5. Save, switch and export presets; import the same file and confirm equal controls.
6. Repeat at quiet and normal listening levels. Evaluate
   transition clicks, latency and CPU use without assuming Windows parity.
7. Close/reopen QAM, restart Decky, suspend/resume and restart PipeWire. Confirm
   truthful status and recovery of the most recent draft and selected preset.
8. Insert/remove wired headphones and switch Bluetooth/HDMI outputs. Confirm the
   speaker-only bypass, Standby when Steam plays elsewhere, and that headphones
   never receive speaker EQ for more than a moment.
9. Disable/unload/uninstall and kill Decky while a game is playing. Verify audio
   continues on the output at the same volume, the default output is unchanged
   and no DeckyEQ process or sink remains.

10. Per-game sound is not yet validated on hardware: start a game with its own sound,
    leave the game, return to it, and suspend/resume while it runs. Check that
    `Router.MainRunningApp` reports the right game (including non-Steam shortcuts and the
    overlay) and that each switch sounds clean.

Treat any failed check as a defect to resolve before publishing a verified release.
