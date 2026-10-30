# Changelog

All notable changes to DeckyEQ, newest first.

## [Unreleased]

### ✨ Added

- **About:** **Use Rayek's store** makes Rayek's store Decky's store channel after you confirm, so Decky's Store
  tab lists Rayek's plugins and offers their updates.

### 🔧 Changed

**Decky log**
- DeckyEQ now writes what it does to Decky's log: EQ on or off, preset and output changes, a
  game's own sound, and why audio paused or a change was refused.
- Slider moves and the regular audio check are not logged, and nothing names your files.

**Updates**
- A Downloads folder that is a link (for example to a microSD card) now works, and no
  half-finished file sits in Downloads while an update downloads.

**About**
- Version, author and credits come first and Updates sits at the very bottom; a downloaded update
  shows where it was saved and how to install it in one place.

**Loading**
- The panel shows a spinner while DeckyEQ starts.

**Connection**
- If DeckyEQ cannot start or stops answering, the button is now **Try again**, and the main
  screen says how long ago the values were last read.

**Confirmations**
- Switching preset or discarding unsaved changes now warns that the changes will be lost,
  and the delete and forget dialogs say what is removed and what stays.

**Texts**
- Small wording fixes across the panel.

### 🐛 Fixed

- **Settings:** if the settings file is damaged, DeckyEQ restores your most recent settings instead of the
  ones before them, and keeps the damaged file beside it.
- **Errors:** an unexpected error, or a file DeckyEQ cannot write, shows a short message instead of details
  that could include folder names; the details go to Decky's log.
- **Errors:** when PipeWire reports why the equalizer stopped, your home folder appears as `~`.

## [1.1.0] - 2026-10-24

### ✨ Added

**Panel**
- The main screen holds **Enable EQ**, **Volume boost**, **Preset**, the curve, and **Bass**,
  **Treble** and **Stereo width**; **Save** and **Discard** appear there while the sound has unsaved changes.
- Rows under **More** open **Equalizer**, **Presets**, **Output** and **About**, each with a
  one-line summary; **B** goes back one page and the row you came from is selected again.

**Curve editor**
- Drag a dot of the curve up or down, or press **A** and use the D-pad: **←/→** choose the
  band, **↑/↓** change it in 0.5 dB steps, **X** sets it flat.
- **Edit full screen** shows the curve larger with the preset list, **Five sliders**,
  **Save**, **Discard** and **Done**; there **LT/RT** make a band wider or narrower and **Y** changes its
  filter type.

**Five sliders**
- Five sliders instead of ten, each moving two neighbouring bands; switching never changes
  your sound, and the choice belongs to this console, not to a preset.

**Games**
- While a game runs, **Own sound for &lt;game&gt;** gives it its own preset and edits, which come
  back whenever the game runs.
- **Presets → Games with their own sound** lists them with a **Forget** button; up to 200 games
  are kept.

**Volume boost**
- Up to +12 dB more for every preset and game, for speakers that are too quiet at full
  volume.

**Stereo width**
- From mono (0%) to twice as wide (200%), applied live.

**Limiter**
- An always-on limiter turns loud peaks down smoothly instead of letting them crackle, so boosts
  can stay loud; the bass has its own limiter, so a bass hit no longer turns down voices.

**Presets**
- New built-in presets **Clear voices** (brings dialogue forward), **More bass** (fuller low end)
  and **Legion Go 2** (a speaker tuning made by ear from the Legion Go S preset; choosing it also sets
  **Volume boost** to 9 dB).
- **About this preset** describes each built-in preset.
- Every preset uses the same ten bands, 31.5 Hz to 16 kHz, so the sliders and dots stay the same
  whichever preset you pick.

### 🔧 Changed

**Updating from 1.0**
- A band's frequency can no longer be changed; a band you moved in 1.0 keeps its own
  frequency and is named by it, and only its gain (in full screen also its width and type) changes.
- Going back from 1.1 to 1.0 may fail to read settings that use stereo width or other
  1.1-only settings, so export your presets first.

**Volume and output**
- Steam's volume and output stay untouched; the equalizer is now a WirePlumber smart
  filter in front of the output you choose, and the volume slider and buttons work exactly as without EQ.
- DeckyEQ no longer sets your speakers to 100%, changes the default output or moves
  streams; when Steam plays to another output, EQ waits (**Standby**).
- If the filter stops, playback continues on the output at your volume.

**Output**
- While EQ is on, Steam lists **DeckyEQ** as an output; choosing it is the same as choosing the
  output EQ applies to.
- Requires WirePlumber 0.5 or newer (SteamOS 3.7 and newer).
- **Speaker port only** is now **Speakers only**, and **Output** holds just **Apply EQ to** and
  **Speakers only**.
- **Refresh / reconnect EQ** is now **Reconnect EQ**.

**Headroom**
- The **Automatic headroom** switch is gone; headroom is always on and lowers the level only
  for EQ boosts above +6 dB, leaving the rest to the limiter.
- Most presets play louder than in 1.0, and presets that had the switch off may play slightly
  quieter (the Go S preset by about 0.6 dB); your **Input level**, **Volume boost** and stereo width are never
  taken back.

**Bands**
- Band frequencies are fixed and the frequency slider is gone; in full screen a band's width and
  filter type can still be changed.

**Presets**
- Import and export sit under **Show import or export** in **Presets**: **Export this sound**,
  **Export your presets** and **Export this sound to…**.
- **Save as new…** starts with the current preset's name, and a repeated name gets " (2)".
- **Legion Go S · Original** is now called **Legion Go S**.

**Panel**
- The **View** dropdown is gone.

**Updates**
- Only a ZIP that a GitHub Actions run in this repository uploaded to a release is offered; releases
  carry a build-provenance attestation that `gh attestation verify` can check.

**Messages**
- Errors stay on the page that caused them and also appear as a toast; outcomes such as an export
  path appear as a toast.
- While EQ is not live, the pages say why.

**Performance**
- No CPU is used while nothing plays; the filter sleeps with its output, and with EQ off DeckyEQ
  no longer runs audio tools in the background.

**Install**
- The plugin ZIP is about 200 KB instead of 2.7 MB.

### 🐛 Fixed

**Volume**
- In 1.0 the speakers could stay at 100% when Decky crashed, when you picked the speakers in Steam,
  or after headphones or Bluetooth came and went; DeckyEQ no longer touches volumes.
- A volume that 1.0 still owed is restored once at startup when its device returns.

**Bluetooth**
- Playback no longer moves to the speakers when you turn EQ on.

**Switching**
- The filter starts and stops flat and then moves to your settings, so turning EQ on or off is
  smooth.

**Stability**
- A slow audio tool (for example while a game loads) no longer pauses EQ, and a PipeWire restart
  rebuilds the filter.
- A filter that keeps failing retries after 8, 16, 32 and then 60 seconds instead of constantly.

**Errors**
- A change that cannot be applied puts back the previous sound, and a change lost in transit keeps
  its notice instead of silently reverting.

**File pickers**
- Import and Export to a folder open again after an earlier error, and closing the picker is
  no longer reported as an error.

**Presets**
- **Edited** no longer flickers on Flat, and Delete and Discard act on the preset they were opened
  for.

**Updates**
- A Downloads folder that is a link, read-only, full or not writable gets its own message.
- After a download About says **Ready to install**, and partial downloads left by an interrupted
  run are removed.

**Settings**
- Damaged settings fall back to the last good copy.
- Preset names with invisible characters that broke saving are refused, and frequencies where
  PipeWire's filters become unstable snap to 20 Hz or 20 kHz.

**Startup**
- DeckyEQ refuses to run as root, which would lock you out of your own settings.

**Unloading**
- Unloading no longer stalls on Decky 3.3.

## [1.0.0] - 2026-09-07

### ✨ Added

**Plugin**
- First public release.

**Equalizer**
- Ten bands, with changes applied while audio plays.

**Tone**
- **Bass** and **Treble** sliders, each from -9 to +9 dB.

**Filters**
- Bell, low shelf or high shelf for each band, with gain, frequency and width.

**Presets**
- Your own presets, up to 64; update, rename, copy, delete or revert to the saved sound.
- Built-in presets Flat and GAZuOOT's community **Legion Go S · Original**.

**Import and export**
- Single sounds or the whole library as JSON files, to `~/Documents/DeckyEQ/` or a
  folder you choose.

**Drafts**
- Your latest edits survive closing the menu or restarting Decky.

**Input level**
- With optional automatic headroom for boosts.

**Output**
- Output selection, with **Speaker port only** bypass on by default.

**Volume**
- The physical output is set to 100% and your volume moves to the virtual **DeckyEQ** output, and
  back when EQ is turned off.

**Panel**
- A compact panel with a **View** dropdown for **Equalizer**, **Presets**, **Output** and **About**.

**Updates**
- Check GitHub for a newer release and download its verified ZIP to your Downloads folder.
