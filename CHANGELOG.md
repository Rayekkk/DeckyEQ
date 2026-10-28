# Changelog

All notable changes to DeckyEQ, newest first.

## [Unreleased]

### Changed

- **Decky log.** DeckyEQ now writes what it does to Decky's log: turning EQ on or off,
  preset and output changes, a game's own sound, and why audio paused or a change was refused.
  Slider moves and the regular audio check are not logged, and nothing names your files.
- **Updates.** A Downloads folder that is a link (for example to a microSD card) now works,
  and no half-finished file sits in Downloads while an update downloads.

### Fixed

- **Damaged settings.** If the settings file is ever damaged, DeckyEQ restores your most
  recent settings instead of the ones before them, and keeps the damaged file beside it.
- **Error messages.** An unexpected error shows a short message instead of details that
  could include folder names; the details go to Decky's log.

## [1.1.0] - 2026-10-24

This update rebuilds the panel around a curve you can edit by touch or controller, adds
volume boost, stereo width, a sound per game and three new presets, and changes how DeckyEQ
reaches your speakers: Steam's volume and output now stay completely untouched.

**Updating from 1.0:**

- A band's frequency can no longer be changed. A band you moved in 1.0 keeps its own
  frequency and is named by it; only its gain (in full screen also its width and type) changes.
- Going back from 1.1 to 1.0 may fail to read settings that use stereo width or other
  1.1-only settings. Export your presets first.

### New features

- **A new panel.** The main screen holds **Enable EQ**, **Volume boost**, **Preset**, the
  curve, and **Bass**, **Treble** and **Stereo width**; **Save** and **Discard** appear there
  while the sound has unsaved changes. Rows under **More** open **Equalizer**, **Presets**,
  **Output** and **About**, each with a one-line summary. **B** goes back one page and the row
  you came from is selected again. The **View** dropdown is gone.
- **Curve editor.** Drag a dot of the curve up or down, or press **A** and use the D-pad:
  **←/→** choose the band, **↑/↓** change it in 0.5 dB steps, **X** sets it flat. **Edit full
  screen** shows the curve larger with the preset list, **Five sliders**, **Save**, **Discard**
  and **Done**; there **LT/RT** make a band wider or narrower and **Y** changes its filter type.
- **Five sliders:** five sliders instead of ten, each moving two neighbouring bands. Switching
  never changes your sound; the choice belongs to this console, not to a preset.
- **Per-game sound:** while a game runs, **Own sound for &lt;game&gt;** gives it its own preset
  and edits, which come back whenever the game runs. **Presets → Games with their own sound**
  lists them with a **Forget** button. Up to 200 games are kept.
- **Volume boost:** up to +12 dB more for every preset and game, for speakers that are too quiet
  at full volume.
- **Stereo width:** from mono (0%) to twice as wide (200%), applied live.
- **An always-on limiter** turns loud peaks down smoothly instead of letting them crackle, so
  boosts can stay loud. The bass has its own limiter, so a bass hit no longer turns down voices.
  Volume boost, stereo width, the input level and the limiter run in DeckyEQ's own small audio
  stage; still no extra audio packages are needed.
- **New built-in presets:** **Clear voices** brings dialogue forward, **More bass** gives a
  fuller low end, and **Legion Go 2** is a speaker tuning for the Legion Go 2 made by ear from
  the Legion Go S preset; choosing it also sets **Volume boost** to 9 dB. **About this preset**
  describes each built-in preset. **Legion Go S · Original** is now called **Legion Go S**.
- **Same bands everywhere:** every preset uses the same ten bands, 31.5 Hz to 16 kHz, so the
  sliders and dots stay the same whichever preset you pick.

### Changed

- **Steam's volume and output stay untouched.** The equalizer is now a WirePlumber smart filter
  in front of the output you choose. DeckyEQ no longer sets your speakers to 100% behind a
  virtual output, never changes the default output and never moves streams; the volume slider
  and the volume buttons work exactly as without EQ. When Steam plays to another output, EQ
  waits (**Standby**). If the filter stops, playback continues on the output at your volume.
  While EQ is on, Steam lists **DeckyEQ** as an output; choosing it is the same as choosing the
  output EQ applies to. Requires WirePlumber 0.5 or newer (SteamOS 3.7 and newer).
- **Louder by default.** The **Automatic headroom** switch is gone: headroom is always on and
  lowers the level only for EQ boosts above +6 dB, leaving the rest to the limiter. Most presets
  play louder than in 1.0; presets that had the switch off may play slightly quieter (the Go S
  preset by about 0.6 dB). Your **Input level**, **Volume boost** and stereo width are never
  taken back.
- **Band frequencies are fixed.** The frequency slider is gone; in full screen a band's width
  and filter type can still be changed.
- **Speaker port only** is now **Speakers only**, and **Output** holds just **Apply EQ to** and
  **Speakers only**. **Refresh / reconnect EQ** is now **Reconnect EQ**.
- **Import and export** sit under **Show import or export** in **Presets**: **Export this sound**,
  **Export your presets** and **Export this sound to…**. **Save as new…** starts with the current
  preset's name, and a repeated name gets " (2)".
- **Safer updates:** only a ZIP that a GitHub Actions run in this repository uploaded to a
  release is offered. Releases carry a build-provenance attestation
  that `gh attestation verify` can check.
- **Clearer messages:** errors stay on the page that caused them and also appear as a toast;
  outcomes such as an export path appear as a toast. While EQ is not live, the pages say why.

### Fixes

- **Volume after a crash or an output change.** In 1.0 the speakers could stay at 100% when
  Decky crashed, when you picked the speakers in Steam, or after headphones or Bluetooth came
  and went. DeckyEQ no longer touches volumes; a volume that 1.0 still owed is restored once at
  startup when its device returns.
- **Bluetooth playback** no longer moves to the speakers when you turn EQ on.
- **Smooth on and off:** the filter starts and stops flat and then moves to your settings, so
  switching EQ on or off is smooth.
- **Stays playing through hiccups:** a slow audio tool (for example while a game loads) no
  longer pauses EQ, a PipeWire restart rebuilds the filter, and a filter that keeps failing
  retries after 8, 16, 32 and then 60 seconds instead of constantly.
- **Failed changes are undone:** a change that cannot be applied puts back the previous sound,
  and a change lost in transit keeps its notice instead of silently reverting.
- **File pickers:** Import and Export to a folder open again after an earlier error, and closing
  the picker is no longer reported as an error.
- **Edited** no longer flickers on Flat, and Delete and Discard act on the preset they were
  opened for.
- **Updates:** a Downloads folder that is a link, read-only, full or not writable gets its own
  message; after a download About says **Ready to install**; partial downloads left by an
  interrupted run are removed.
- **Settings:** damaged settings fall back to the last good copy, preset names with invisible
  characters that broke saving are refused, and frequencies where PipeWire's filters become
  unstable snap to 20 Hz or 20 kHz.
- DeckyEQ refuses to run as root, which would lock you out of your own settings, and unloading
  no longer stalls on Decky 3.3.

### Under the hood

- **No CPU while nothing plays:** the filter sleeps with its output, and the monitor follows
  audio events instead of checking every 1.5 seconds; with EQ off it no longer runs audio tools
  in the background.
- The plugin ZIP is about 200 KB instead of 2.7 MB; the README banner is no longer shipped.
- Shared limits (bands, ranges, the boost cap) live in one file read by the backend and the
  panel, and the panel's calculations are checked against values from the backend.
- Releases are built as drafts by CI from a tag, without install scripts or write access, and
  published by hand after the hardware checks.
- Many new tests for the backend, the native audio stage, the panel and the updater, from
  several full audits by independent reviewers; device measurements are in
  [VALIDATION.md](docs/VALIDATION.md).

## 1.0.0 - 2026-09-07

First public release.

### Added

- **Live equalizer** with ten bands, changes applied while audio plays.
- **Bass and treble** sliders, each from -9 to +9 dB.
- **Advanced filters:** bell, low shelf or high shelf for each band, with gain, frequency and width.
- **Your own presets:** up to 64; update, rename, copy, delete or revert to the saved sound.
- **Built-in presets:** Flat and GAZuOOT's community **Legion Go S · Original**.
- **Import and export** of single sounds or the whole library as JSON files, to
  `~/Documents/DeckyEQ/` or a folder you choose.
- **Saved drafts:** your latest edits survive closing the menu or restarting Decky.
- **Input level** with optional automatic headroom for boosts.
- **Output selection** with **Speaker port only** bypass, on by default.
- **Normal volume control:** the physical output is set to 100% and your volume moves to the
  virtual **DeckyEQ** output, and back when EQ is turned off.
- A compact panel with a **View** dropdown for **Equalizer**, **Presets**, **Output** and **About**.
- **Plugin updates:** check GitHub for a newer release and download its verified ZIP to your
  Downloads folder.
