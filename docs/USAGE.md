# DeckyEQ usage and implementation guide

[Back to README](../README.md)

## Installation and first use

1. Install the release `DeckyEQ-<version>.zip` (or `artifacts/DeckyEQ-<version>.zip` from `npm run package`) using Decky's local ZIP installation
   option in its developer settings.
2. Open **DeckyEQ** in the quick-access menu. Enable **Enable EQ**. The first
   activation selects the current speaker output if possible. If no suitable
   speaker output is found, choose an output under **Output**.
3. Play familiar audio and adjust **Bass**, **Treble** and the ten band sliders.
4. Open **Presets**, enter a name and choose **Save as new preset**.

Use the **View** dropdown to switch between Equalizer, Presets, Output and About.
Actions occupy separate full-width rows for the narrow quick-access menu.
The plugin keeps the view mounted while Steam opens native dropdown menus,
so View and advanced-band selections survive the temporary QAM hide.

The plugin uses the system's `pipewire`, `pw-cli`, `pw-dump`, `pactl` and `python3`.
It does not download audio packages, require root, install LSP/EasyEffects,
modify `/usr` or `/etc`, or edit existing PipeWire configuration. An unavailable
system tool or unsupported user session is reported in the UI. Preset management
remains available while audio is off.

The plugin must run without Decky's `root` flag, in the desktop user's session.
It starts its own PipeWire **client** process, not a replacement audio server.
Enabling EQ redirects default playback and existing streams on the selected or
previous default output through the virtual `DeckyEQ` sink. Playback explicitly
pinned to unrelated devices is left alone. Individual stream volumes are kept.

Before switching the default output, DeckyEQ copies the hardware volume and
mute state to its virtual sink, then sets and verifies the physical output at
**100%**. The physical output is briefly muted while streams are moved, then
unmuted. This removes the double volume control that would otherwise limit the
EQ sink's maximum volume to the hardware's old setting. Adjust playback volume
normally through Steam while EQ is active.

Disabling EQ transfers its current volume and mute state back before returning
playback to the hardware. Failed activation restores the original values.
A separate hardware-volume change made while EQ is running is respected.
The recovery journal tracks the latest observed EQ volume (normally every
1.5 seconds); after a crash, restoration can lag the very last slider movement.

## Presets and drafts

Slider edits are applied immediately and automatically stored as the current
draft. **Update selected preset** commits the draft to that library entry.
**Save as new preset** creates an independent copy. Switching away from an edited
draft asks before replacing it; **Revert to saved preset** restores the original.
Built-in presets cannot be overwritten, renamed or deleted. Their settings can
be edited live and saved as a new user preset.

**Legion Go S · Original** is available directly in **Current preset**. It
reproduces the native filters actually parsed from GAZuOOT's community file,
including its +8 dB input boost and unusual frequency values. Automatic headroom
is off for this preset. It may distort at high playback levels; reduce **Input
level** or enable automatic headroom to make your own adjusted version.
See [the exact settings and source notes](GO_S_PRESET.md). This is a Go S
community tuning preset, not a measured calibration for Go 2.

**Export current sound** includes the current draft, even if it has not been
saved to a named preset. **Export saved preset library** includes the saved user
presets, not the draft. An empty library exports Flat. Default exports go to
`~/Documents/DeckyEQ/`; a second export with the same name gets a numeric suffix
and never overwrites an existing file. You can also choose an export folder.

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

## Headroom and tone controls

The two tone sliders are additional shelves; their effects add to the ten bands.
The chart shows **individual band gains**, not a measured speaker response.

Automatic headroom sums the maximum gain of each filter, including resonance
from adjustable-Q shelves, and adds any positive input gain plus 0.5 dB margin.
For example, +3 dB bass and +2 dB on one bell gives -5.5 dB compensation. This
conservative bound also covers overlapping filters; even a shelf cut can have
a resonant peak at high Q. The **Input level** adjustment is added to the
compensation. Consequently the sound can
become quieter when boosting several bands. It is not a limiter, a loudness
normalizer or a guarantee against physical speaker distortion.

With automatic headroom off, the same bound is still capped: if the summed
boost exceeds +24 dB, the input level is lowered by the excess. This keeps an
imported preset from adding extreme gain while the hardware runs at 100%. The
original Go S preset sums to about +19 dB and is unaffected.

The runtime lowers the input level before altering filters, makes large gain
changes in several steps, then sets the final input level. The frontend coalesces
fast drags; all backend transactions are serialized. Final parameters are read
back before a change is treated as confirmed. Audible transition quality and
perceived latency still require the hardware checks below.

While enabled, the graph keeps processing even if no application is playing.
This makes idle control readback reliable on PipeWire 1.6.8. Disable EQ when you
want to stop its processing. The child uses isolated system Python and a clean
library environment, rather than Decky's frozen PluginLoader executable.

Each slot contains three native biquads in series; only the selected type has
nonzero gain. The fixed graph has 33 nodes per channel, including the input
level and two tone shelves. Type changes therefore do not restart audio.
The frequency slider covers 20 Hz – 20 kHz. PipeWire's single-precision
biquads become unstable below about 6 Hz and just under Nyquist, so values
between 0 and 20 Hz snap to 20 Hz and values between 20 and 24 kHz snap to
20 kHz, including in imported presets and saved settings. Exactly 0 Hz and
24–48 kHz are retained for compatibility with the original Go S file. Their
effect depends on filter type and the session sample rate; a bell at or above
Nyquist has no effect.

## Output switching and cleanup

**Speaker port only** is enabled initially. It checks the actual selected
output's active port; a missing/unknown port is bypassed rather than guessed.
Turn it off if you deliberately want to EQ a selected headset, USB device or
HDMI output. The monitor listens to PulseAudio events (`pactl subscribe`) and
reacts to output, card and server changes about 0.15 seconds after a burst of
events settles. Without events it checks every 15 seconds, including a full
PipeWire control readback while EQ is active; if the event stream is unavailable
it falls back to checking every 1.5 seconds. Every edit is read back at once.
After a failure, retries wait 8, 16, 32 and then 60 seconds. This is a software polling guard, not an instantaneous hardware
interlock; there can be a short transition before bypass.

A manual default-output change in Steam is respected. The physical output runs
at 100% while EQ is active, so selecting it directly in Steam also bypasses EQ
and transfers your volume back. EQ stays bypassed until another output is
selected and the target returns, or until **Refresh / reconnect EQ** explicitly
reactivates it. Enabling EQ or changing its output while Steam plays through
another device, such as Bluetooth headphones, also waits; only **Refresh /
reconnect EQ** takes playback over immediately.
Unplugging the target pauses EQ; reconnecting it can restore the saved settings.

Turning **Enable EQ** off or unloading the plugin restores routing and terminates
the owned filter process, while keeping the preset library. New streams that used
DeckyEQ are also moved back. The filter runs under a small supervisor attached to
Decky with a Linux parent-death signal. If the Decky backend dies, the supervisor
first returns your EQ volume to the hardware output, then stops the filter; the
audio session manager then moves playback to that output at your volume instead
of the 100% hardware level used while EQ runs.

A recovery journal records the previous default output and every volume that is
still owed. Audio routes keep their own volume per port, so when a headphone plug
or a disconnected Bluetooth device interrupts EQ, the restore waits for that
device and port to return instead of being lost; it never blocks EQ on another
output. If the hardware volume was changed in the meantime, that change is kept.

The plugin never terminates an unrelated process. A pre-existing DeckyEQ sink
that it does not own blocks activation with an explanatory error.

## Plugin updates

**View → About → Updates** uses the release flow shared with SpotiDeck and Legion
Go 2 Companion. It checks `Rayekkk/DeckyEQ` only when requested, then downloads a
newer stable `DeckyEQ-X.Y.Z.zip` with a matching GitHub asset digest. Installation
remains a separate action in Decky's Developer settings. A repository with no
release is a normal state. Downloads do not require extra Python packages.

Requests run outside the audio event loop. Frontend progress survives changing
views or closing QAM; unloading cancels the backend downloader before it can
publish an incomplete ZIP. The downloader checks SHA-256, size, archive paths,
entry points, plugin identity, non-root flags and both manifest versions.

## Build and test locally

Build dependencies: Node.js 20+ / npm and Python 3.10+. These are **development**
requirements; users install the prebuilt ZIP.

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
Regenerate them after an intended change with `DECKYEQ_UPDATE_FIXTURES=1 npm test`.

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
- `deckyeq/runtime.py`: process ownership, routing and verified control writes.
- `deckyeq/service.py`: serialized transactions, rollback, monitoring and RPC state.
- `main.py`: minimal Decky entry point with unprivileged lifecycle ownership.
- `src/controller.ts`: one frontend write queue retained across QAM remounts.
- `src/App.tsx`: native Decky controls and preset management.
- `deckyeq/updates.py` and `src/updates.tsx`: verified release downloads and About UI.

The core engine has no dependency on the Decky frontend, so a future Companion
integration can reuse it. Both implementations must not run simultaneously with
the same sink identity. Preserve version 2 filter types and migration support
when moving presets into another plugin.
