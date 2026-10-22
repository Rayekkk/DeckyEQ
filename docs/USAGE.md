# DeckyEQ usage and implementation guide

[Back to README](../README.md)

## Installation and first use

1. Install the release `DeckyEQ-<version>.zip` (or `artifacts/DeckyEQ-<version>.zip` from `npm run package`) using Decky's local ZIP installation
   option in its developer settings.
2. Open **DeckyEQ** in the quick-access menu. Enable **Enable EQ**. The first
   activation selects the current speaker output if possible. If no suitable
   speaker output is found, choose an output under **Output and level**.
3. Play familiar audio and adjust **Bass**, **Treble** and, under **Equalizer
   bands**, the ten band sliders.
4. Open **Presets**, choose **Save as new…** and enter a name.

The main screen holds **Enable EQ**, **Preset**, **Volume boost**, **Bass**, **Treble**
and **Stereo width**. Below
them, rows with a one-line summary open **Equalizer bands**, **Presets**,
**Output and level** and **About**; each page starts with a back button that
takes the D-pad focus, **B** goes back one page, and going back returns focus to
the row you came from.
Technical facts and rare actions are under **Show details**. Outcomes such as
an export path appear as a Steam toast; errors stay on the page that caused them,
and a failure of an action confirmed in a dialog (which hides the menu) is also
toasted. A refused preset name reopens the dialog with the typed name and reason.
The plugin keeps the panel mounted while Steam opens native dropdown menus,
so the open page and band selections survive the temporary QAM hide.

The plugin uses the system's `pipewire`, `pw-cli`, `pw-dump`, `pactl` and `python3`.
It does not download audio packages, require root, install LSP/EasyEffects,
modify `/usr` or `/etc`, or edit existing PipeWire configuration. An unavailable
system tool or unsupported user session is reported in the UI. Preset management
remains available while audio is off.

The plugin must run without Decky's `root` flag, in the desktop user's session.
It starts its own PipeWire **client** process, not a replacement audio server.
The equalizer is a WirePlumber (0.5+) **smart filter** in front of the selected
output: WirePlumber links every stream aimed at that output, directly or as the
default, through the filter first. DeckyEQ never changes the default output, never
moves streams and never changes a volume. Steam's volume slider, the volume buttons
and Steam's output choice behave exactly as without EQ; a stream that Steam sends to
another output does not pass the filter, and the panel shows **Standby**. The
filter's own `DeckyEQ` sink is never chosen as the default output by WirePlumber.

If the filter process ends, including when the Decky backend is killed (a Linux
parent-death signal stops it), WirePlumber links playback straight to the output at
the user's volume. Nothing has to be restored.

DeckyEQ 1.0 instead held the hardware at 100% behind a virtual output and kept a
recovery journal. A journal left by a 1.0 crash is still settled at startup and
when an output appears: owed volumes are restored when their device and port are
present, a restore the device does not confirm is dropped instead of retried, it
never blocks EQ, and an unreadable journal is
set aside instead of blocking EQ.

## Presets and drafts

Slider edits are applied immediately and automatically stored as the current
draft. **Save changes** commits the draft to that library entry.
**Save as new…** creates an independent copy. Switching away from an edited
draft asks before replacing it; **Discard changes** restores the original.
Built-in presets cannot be overwritten, renamed or deleted. Their settings can
be edited live and saved as a new user preset.

**Legion Go S · Original** is available directly in **Preset**. It
reproduces the native filters actually parsed from GAZuOOT's community file,
including its +8 dB input boost and unusual frequency values; the limiter keeps it
from clipping. Reduce **Input level** if it sounds squashed, and save your own
adjusted version.
See [the exact settings and source notes](GO_S_PRESET.md). This is a Go S
community tuning preset, not a measured calibration for Go 2.

**Export this sound** includes the current draft, even if it has not been
saved to a named preset. **Export all presets** includes the saved user
presets, not the draft. An empty library exports Flat. Default exports go to
`~/Documents/DeckyEQ/`; a second export with the same name gets a numeric suffix
and never overwrites an existing file. **Show details** offers another export folder.

Import uses Decky's file picker. Place imported files in your home directory,
for example Downloads. Only bounded UTF-8 `.json` files inside the user's home
are accepted. Maximum file size: 256 KiB. Import validates every entry before
adding anything; it does not overwrite presets or automatically change playback.
Names that collide get a suffix. Files from EasyEffects, APO or other equalizers
are not silently approximated; this version accepts the DeckyEQ schema only.

`schemas/preset-v2.schema.json` describes the current exchange format. Version 1
files and settings are migrated automatically, retaining all previous values,
saved presets and the active draft. The public DeckyEQ 1.0.0 release uses preset schema version 2; the older
development builds using schema version 1 cannot import the new filter types.
The engine applies
stricter runtime validation as well. Device names, process identifiers, filesystem
paths and runtime preferences are deliberately excluded from shared presets.

## Headroom, limiter and tone controls

The two tone sliders are additional shelves; their effects add to the ten bands.
The chart shows **individual band gains**, not a measured speaker response.

After the filters, both channels pass DeckyEQ's own stereo stage, a small LADSPA
plugin shipped as `bin/deckyeq_dsp.so` (source: `native/deckyeq_dsp.c`). It applies
the **Input level** (minus any automatic headroom) and **Volume boost** (0 to +12 dB),
then **Stereo width** (mid/side: 0% is mono, 100% unchanged, 200% twice the side
signal), all gliding over about 10 ms instead of stepping, and finally a two-band
limiter. A 4th-order Linkwitz-Riley crossover splits off the bass below 150 Hz (the two
bands add up flat; only the phase of the lowest notes turns), and the bass gets its own
limiter 3 dB below the ceiling, so a loud bass note no longer turns down voices and the
rest: with a single limiter a 1 kHz tone ducked by up to 7.8 dB on every bass hit at
+11 dB input, with two bands by under 2.5 dB (the measurement's own floor). A second
limiter on the whole signal then guarantees a -1 dBFS ceiling that also covers peaks
between samples (its detector looks at the signal 4x oversampled). Each limiter looks
1.5 ms ahead, holds for 25 ms so it does not trace each cycle of a low note (half a
cycle at 20 Hz) and releases over 100 ms; together they delay the sound by about 3 ms.
Both always run; below their ceilings they change nothing. Very large boosts limited
this way can sound compressed; automatic headroom prevents that (below). Turning EQ
on starts the filter neutral (flat, 0 dB) and moves it to your settings in steps;
turning it off moves it back to neutral before it ends. WirePlumber linking the filter
in or out of the playback path is therefore inaudible apart from its 3 ms delay.
The volume of the Legion Go 2 speakers is set in hardware after the digital signal,
so a boost that crosses full scale would clip at any volume; the limiter turns such
peaks down smoothly instead. On the device, with a -1 dBFS test ceiling, a +9 dB
boosted 70 Hz tone stayed at -1.00 dBFS with 0.4–0.6% THD, where the unlimited
signal reached +6.9 dBFS. The
filter graph is therefore stereo: every filter runs once per channel (`band0_l`,
`band0_r`, ...), sharing the same values. Presets and settings store `width` and
`limiter` only when they differ from 100% and on, so DeckyEQ 1.0 still reads
everything that does not use them; files without them get those defaults. DeckyEQ 1.0
refuses a whole settings file in which any preset or the current sound uses them: before
going back to 1.0, set width to 100% everywhere, or export your presets first. (The
`limiter` field of 1.1's earlier builds is still read but no longer used.)

**Volume boost** is a global setting, not part of a preset: the Legion Go 2 plays
quieter under SteamOS than under Windows, whose audio driver adds its own gain and
processing, while SteamOS's hardware mixer tops out at 0 dB. Automatic headroom never
cancels the boost, and the limiter keeps it from clipping. Large boosts on already loud
material are limited hard and can sound compressed. Moving the slider is a live edit
like the EQ sliders; it is saved a second after the last change.

Automatic headroom computes the combined response of all filters (bands and tone
shelves, including resonance from adjustable-Q shelves, at 48 kHz) and lowers the
level only by the part of its peak above +6 dB, so moderate boosts keep full loudness
and the limiter catches their peaks. Boosts in different ranges do not add up (+9 dB
bass and +9 dB treble peak at about +9 dB), while filters stacked on the same frequency
do. A raised **Input level**, the **Volume boost** and stereo widening are never taken
back: they are your deliberate gain, and the limiter keeps them from clipping; a
lowered input level still counts as room. The panel shows the current compensation.
(An earlier 1.1 build also had a mode that made room for every boost, the input level
and widening; it made boosted presets far quieter than no EQ at all and was removed.)
Neither is a loudness normalizer or a guarantee against physical speaker distortion.

Automatic headroom is always on (an earlier build had a switch for it, which changed
about 0.6 dB on the Go S preset and only confused). Presets still carry the old
`auto_headroom` field for compatibility; it is no longer read.

The runtime lowers the input level before altering filters, makes large filter
gain changes in several steps, reads the filters back, and only then raises the
input level; the stage glides each level change. The frontend coalesces fast drags; all backend transactions
are serialized. While audio plays, every change is read back from PipeWire before
it is confirmed; a failed or interrupted write makes the next change read the
filter afresh, so a rollback really restores the previous sound. Slider edits
are kept in memory at once and saved to disk about one second after the last
change (any other action saves immediately; unloading saves too).

When nothing plays, the filter suspends together with its output and uses no CPU.
A suspended filter keeps reporting its previous controls but stores new ones and
applies them as soon as audio resumes (checked on PipeWire 1.6.8), so edits made
while it is suspended are written without readback and verified by the monitor
once audio flows. The child uses isolated system Python and a clean library
environment, rather than Decky's frozen PluginLoader executable.

Each slot contains three native biquads in series; only the selected type has
nonzero gain. The fixed graph has 32 filters per channel (with the two tone
shelves) and the stage. Type changes, also from switching presets, are therefore
live control updates: audio never restarts and never plays without EQ. (One filter
per slot would save about 1% of a core but needs a rebuild for every type change.)
Large frequency jumps are written in steps of half an octave or less.
A shelf's Q is capped at 1.4: above it the shelf resonates, so a "cut" boosts near its
corner. Imported presets are capped the same way.
The frequency slider covers 20 Hz – 20 kHz. PipeWire's single-precision
biquads become unstable below about 6 Hz and just under Nyquist, so values
between 0 and 20 Hz snap to 20 Hz and values between 20 and 24 kHz snap to
20 kHz, including in imported presets and saved settings. Exactly 0 Hz and
24–48 kHz are retained for compatibility with the original Go S file. Their
effect depends on filter type and the session sample rate; a bell at or above
Nyquist has no effect.

## Output switching and cleanup

**Speakers only** is enabled initially. It checks the actual selected
output's active port; a missing/unknown port is bypassed rather than guessed.
Turn it off if you deliberately want to EQ a selected headset, USB device or
HDMI output. The monitor listens to PulseAudio events (`pactl subscribe`) and
reacts to output, card and server changes about 0.15 seconds after a burst of
events settles; changes of DeckyEQ's own sink are ignored. Without events it only
re-reads the filter's controls every 15 seconds while EQ is active; if the event
stream is unavailable it falls back to checking every 1.5 seconds.
A tool that does not answer (for example `pw-dump` timing out while a game loads)
leaves the filter playing; only three such misses in a row, or a real mismatch, pause
EQ. If PipeWire restarts under the filter, DeckyEQ builds it again without an error.
After a failure, retries wait 8, 16, 32 and then 60 seconds, and a filter that keeps
failing keeps that backoff until it has run for a minute. This is a software polling
guard, not an instantaneous hardware interlock; there can be a short transition
before bypass. `pactl subscribe` and the filter both run through `worker.py`, so they
end with the plugin even if Decky is killed.

Steam's output choice is never overridden. Unplugging the target pauses EQ;
reconnecting it resumes with the saved settings.

Turning **Enable EQ** off or unloading the plugin only terminates the owned filter
process, while keeping the preset library.

The plugin never terminates an unrelated process. A pre-existing DeckyEQ sink
that it does not own blocks activation with an explanatory error.

## Plugin updates

**About → Updates** uses the release flow shared with SpotiDeck and Legion
Go 2 Companion. It checks `Rayekkk/DeckyEQ` only when requested, then downloads a
newer stable `DeckyEQ-X.Y.Z.zip` uploaded by the release workflow (`github-actions[bot]`)
with a matching GitHub asset digest; a hand-uploaded ZIP is never offered. Installation
remains a separate action in Decky's Developer settings. A repository with no
release is a normal state. Downloads do not require extra Python packages.

Requests run outside the audio event loop. Frontend progress survives changing
views or closing QAM; unloading cancels the backend downloader before it can
publish an incomplete ZIP. The downloader checks SHA-256, size, archive paths,
entry points, plugin identity, non-root flags and both manifest versions.

## Build and test locally

Build dependencies: Node.js 20+ / npm, Python 3.10+ and gcc for the native audio
stage (on Windows, gcc inside WSL; `scripts/build_native.py` calls it). These are
**development** requirements; users install the prebuilt ZIP, which contains the
compiled x86-64 library. `tests/test_dsp.py` drives the library through its LADSPA
interface on Linux (ceiling, delay, width, distortion of a boosted bass tone).

```sh
npm ci
npm run check
```

Individual commands: `npm run typecheck`, `npm test`, `npm run test:ui`,
`npm run build`, `npm run package`. The package builder verifies metadata,
required runtime files, ZIP integrity and absence of preview code. It produces
a deterministic ZIP and a SHA-256 sidecar in `artifacts/`; `npm run package`
always rebuilds `dist/` first so the bundle matches the sources it ships with.

Shared limits (band frequencies, value ranges, the boost cap) live in
`deckyeq/limits.json` and are read by both the backend and the panel.
`tests/fixtures/` holds data generated by the Python backend that the frontend
tests replay: a real status snapshot and headroom/frequency parity vectors.
Regenerate them after an intended change with `DECKYEQ_UPDATE_FIXTURES=1 npm test`
(PowerShell: `$env:DECKYEQ_UPDATE_FIXTURES=1; npm test`). `npm run typecheck` checks
the tests' types too.

### Releases

Pushing a tag `vX.Y.Z` that matches `plugin.json` runs `.github/workflows/release.yml`:
the build job installs dependencies without their install scripts, runs the full
check without write access, and the publish job attests the ZIP's build
provenance and creates a **draft** release. Publish the draft by hand after the
hardware checks in VALIDATION.md; the in-plugin updater only sees published
releases.

`npm run dev` opens a browser-only UI preview at `http://127.0.0.1:5178/`.
It uses the actual application components with browser adapters and a simulated
backend. It does **not** process audio or replace a Steam/Decky acceptance test.
Preview source and test dependencies never enter the production bundle.

See [VALIDATION.md](VALIDATION.md) for the local evidence and remaining acceptance checks.

## Architecture and future integration

- `deckyeq/model.py`: preset schema, bounds, serialization and library operations.
- `deckyeq/storage.py`: atomic storage and bounded file exchange.
- `deckyeq/graph.py`: one fixed native PipeWire graph and named live controls.
- `deckyeq/runtime.py`: smart filter process ownership and verified control writes.
- `deckyeq/service.py`: serialized transactions, rollback, monitoring and RPC state.
- `main.py`: minimal Decky entry point with unprivileged lifecycle ownership.
- `src/controller.ts`: one frontend write queue retained across QAM remounts.
- `src/App.tsx`: native Decky controls and preset management.
- `deckyeq/updates.py` and `src/updates.tsx`: verified release downloads and About UI.

The core engine has no dependency on the Decky frontend, so a future Companion
integration can reuse it. Both implementations must not run simultaneously with
the same sink identity. Preserve version 2 filter types and migration support
when moving presets into another plugin.
