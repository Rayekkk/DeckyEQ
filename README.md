<div align="center">
<img src="DeckyEQ.png" alt="DeckyEQ - PipeWire equalizer for Decky" width="800">

[![Release](https://img.shields.io/github/v/release/Rayekkk/DeckyEQ?style=for-the-badge&label=release&color=C2410C&labelColor=141417)](https://github.com/Rayekkk/DeckyEQ/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Rayekkk/DeckyEQ/total?style=for-the-badge&label=downloads&color=15803D&labelColor=141417)](https://github.com/Rayekkk/DeckyEQ/releases)
[![Requires](https://img.shields.io/badge/requires-Decky_Loader-0969DA?style=for-the-badge&labelColor=141417)](https://decky.xyz)
[![License](https://img.shields.io/github/license/Rayekkk/DeckyEQ?style=for-the-badge&label=license&color=424A53&labelColor=141417)](LICENSE)

**Shape your sound, band by band.**
A live equalizer, bass and treble controls, and your own presets inside Gaming Mode.

[Features](#features) · [Requirements](#requirements) · [Installation](#installation) · [Usage](#usage) · [Updates](#updates) · [How it works](#how-it-works) · [Troubleshooting](#troubleshooting)

</div>

---

## Features

| | |
|---|---|
| **Live equalizer** | Ten adjustable bands with changes applied while audio is playing |
| **Bass and treble** | Separate tone sliders, each adjustable from -9 to +9 dB |
| **Advanced filters** | Choose bell, low shelf or high shelf for each band; adjust gain, frequency and width |
| **Your own presets** | Save up to 64 user presets; save changes, rename, copy, delete or discard changes |
| **Built-in presets** | Start from Flat or the community **Legion Go S · Original** preset |
| **Import and export** | Share individual sounds or your saved preset library as portable JSON files |
| **Saved drafts** | Keeps your latest edits when you close QAM or restart Decky |
| **Volume boost** | Up to +12 dB louder than SteamOS's maximum, for every preset, kept from clipping by the limiter |
| **Stereo width** | From mono to twice as wide, applied live |
| **Limiter** | A look-ahead peak limiter keeps every boost from clipping; boosts can stay loud |
| **Level and headroom** | Adjust input gain and optionally compensate for large boosts |
| **Output selection** | Apply EQ to your chosen output, with speaker-only bypass enabled by default |
| **Normal volume control** | EQ sits in front of the chosen output; Steam's volume, volume buttons and output choice stay untouched |
| **Compact QAM interface** | Native Decky sliders, dropdowns and full-width action buttons |
| **Plugin updates** | Check GitHub for a newer release and download its integrity-checked installation ZIP to your Downloads folder |

---

## Requirements

| Requirement | Details |
|---|---|
| System | SteamOS with [Decky Loader](https://decky.xyz) and a working desktop audio session |
| Tested handheld | Lenovo Legion Go 2 on **SteamOS 3.10** with **PipeWire 1.6.8** |
| System tools | `pipewire`, `pw-cli`, `pw-dump`, `pactl` and `python3` |
| Extra audio packages | None on the tested stock system; no EasyEffects or external effects plugins required |
| Internet | Only for checking and downloading plugin updates |

On a Legion Go 2 (SteamOS 3.10, WirePlumber 0.5.14) the smart-filter routing, live filter
updates, untouched default output and volume, crash behaviour and idle CPU use were measured,
and EQ was checked in Game Mode. Headphone and Bluetooth transitions, suspend/resume and
longer listening remain open. Other handhelds have not been validated. See the
[validation record](docs/VALIDATION.md) for the scope of testing.

---

## Installation

1. Install [Decky Loader](https://decky.xyz) if it is not already installed.
2. Download the **DeckyEQ-<version>.zip** asset from the [latest release](https://github.com/Rayekkk/DeckyEQ/releases/latest), or build it from source below.
3. In Gaming Mode, open **Quick Access Menu → Decky → Settings → Developer**.
4. Choose **Install Plugin from ZIP** and select the archive.
5. Open **DeckyEQ** in the Decky menu and turn on **Enable EQ**.

The package is `artifacts/DeckyEQ-<version>.zip`, containing one `DeckyEQ` folder.
If no suitable speaker output is selected automatically, choose one in **Output and level**.

---

## Usage

The main screen holds what you use every day: **Enable EQ**, the **Preset** list,
**Volume boost** and the **Bass**, **Treble** and **Stereo width** sliders. Each row below it shows its current state and opens a page:

| Page | Controls |
|---|---|
| **Equalizer bands** | The ten band sliders, **Edit one band** (filter type, frequency, width) and **Reset to flat** |
| **Presets** | Your library, saving and naming sounds, and importing or exporting JSON files |
| **Output and level** | The output EQ applies to, speakers-only bypass and the input level |
| **About** | Plugin version, author, license and GitHub updates |

Technical facts and rare actions sit under **Show details** at the bottom of a page;
**B** goes back one page.
Play familiar audio, make your adjustments, then open **Presets** and select
**Save as new…**. **Save changes** stores later edits in that preset and **Discard changes**
restores it. Built-in presets can be edited and saved as your own copies.

**Export this sound** includes your latest edits; **Export all presets** saves your
saved library. Exports go to `~/Documents/DeckyEQ/`; another folder can be chosen
under **Show details**. Import accepts DeckyEQ JSON files, including the earlier preset
format. EasyEffects and Equalizer APO files need conversion before use.

<details>
<summary><b>Legion Go S · Original preset</b></summary>

<br>

The included preset follows the filters actually parsed by PipeWire from GAZuOOT's
community Go S configuration, including its +8 dB input boost and unusual frequency
values. The limiter keeps its loud setting from clipping; lower **Input level** if it
sounds squashed on your speakers.

This is community tuning for Go S, not a measured Go 2 calibration. The
[source notes and exact parameters](docs/GO_S_PRESET.md) describe the mapping.

</details>

---

## Updates

Open **About** and select **Check for updates**. When a newer stable
release is available, select **Download**. The plugin saves the installation ZIP to
your Downloads folder after checking that DeckyEQ's release workflow uploaded it, and its
GitHub SHA-256 digest, size, contents and version. The digest proves the file arrived intact,
not who built it; `gh attestation verify DeckyEQ-X.Y.Z.zip --repo Rayekkk/DeckyEQ` checks
that GitHub Actions built it from this repository.

Install the downloaded ZIP through **Decky Settings → Developer → Install Plugin from ZIP**.
Checks and downloads run only when requested. The plugin does not install updates itself.
If the repository has no public release yet, it shows **No public release yet**.

---

## How it works

DeckyEQ processes stereo playback with PipeWire's built-in filters and its own small
stage for level, width, boost and limiting. Every change, including switching presets,
updates the running filter live. It uses your existing
audio session and runs without root privileges or extra audio packages.

The equalizer is a WirePlumber **smart filter** placed in front of the output you choose.
WirePlumber sends whatever plays to that output through it, so DeckyEQ never changes the
default output, moves streams or touches volumes. Steam's volume slider and the volume
buttons keep working as usual, and when Steam plays to another output (for example
Bluetooth headphones), EQ simply waits. If the filter stops for any reason, playback
continues on the output directly at your volume. Requires WirePlumber 0.5 or newer
(SteamOS 3.7+).

**Speakers only** bypasses EQ when the selected device switches away from its speaker
port. Turn it off to apply EQ to a selected headset or HDMI output. Disabling or unloading
the plugin stops its audio processing and keeps your preset library.

A two-band limiter always guards the output: it turns down peaks that would clip before
they reach the speakers, and leaves audio that stays below -1 dBFS untouched. The bass has
its own limiter, so a loud bass note turns down only the bass, not voices and everything
else; very large boosts can still sound compressed. Automatic headroom lowers the level only
for EQ boosts above +6 dB, which the limiter alone would squash. It never takes back your
**Input level**, the **Volume boost** or stereo widening: those are yours, and the limiter
keeps them from clipping.
**Volume boost** raises everything after the filters, for all presets: use it when the
speakers are too quiet at full volume. Neither
protects speakers from physical overload at high volume. DeckyEQ provides sound tuning;
it does not reproduce Windows/Nahimic
processing or claim to fix a confirmed Go 2 driver defect.

See the [usage and implementation guide](docs/USAGE.md) for filter ranges, preset formats,
headroom calculations and output recovery details.

---

## Troubleshooting

<details>
<summary><b>EQ is unavailable or paused</b></summary>

<br>

Read the reason shown in the plugin. Confirm the intended device under **Output and level**,
then use **Reconnect EQ** (shown when something is wrong, and under **Show details**).
With **Speakers only** enabled, an active
headphone port or an unknown port causes bypass. A missing system tool is reported
in the interface.

</details>

<details>
<summary><b>The sound is quieter or distorted</b></summary>

<br>

Only EQ boosts above +6 dB lower the level, by the part above it. Raise the **Input level**
or the **Volume boost** for more loudness. Once the limiter is working hard, more gain only
compresses the sound instead of making it louder (the output cannot exceed full scale); lower
the gain if it sounds squashed.
The built-in Go S preset intentionally retains the original gain settings.

</details>

<details>
<summary><b>My edits are not in the exported library</b></summary>

<br>

**Export all presets** contains saved presets. Save the preset first, or use
**Export this sound** to export the current draft directly. Importing a file adds
presets without overwriting existing entries or changing playback automatically.

</details>

<details>
<summary><b>An update cannot be downloaded</b></summary>

<br>

Check your connection and retry later if GitHub reports a request limit. A release must
contain a matching **DeckyEQ-X.Y.Z.zip** with trusted GitHub size and digest metadata.
If a release changes between checking and downloading, check for updates again.
Incomplete or unverified archives are not offered as installation files. The check proves the
ZIP matches what GitHub reports for the release; the ZIP itself is not signed, so trust rests on
the GitHub repository. Releases built by CI also carry a build-provenance attestation that can
be checked with `gh attestation verify`.

</details>

---

## Development

Build requirements: Node.js 20+ / npm, Python 3.10+ and gcc (on Windows: WSL with gcc and
python3, which `npm test` also uses to run the native-stage and Linux-only tests). Users
install the prebuilt ZIP.

```bash
npm ci
npm run typecheck  # check frontend types
npm test           # Python backend tests
npm run test:ui    # UI and lifecycle regressions
npm run build      # compile the frontend
npm run package    # package the current build
```

`npm run check` runs all validation and packaging steps, starting with the native audio
stage (`native/deckyeq_dsp.c` → `bin/deckyeq_dsp.so`, compiled with gcc; on Windows through
WSL). The archive includes frontend and C source, build inputs and dependency notices, with a
SHA-256 sidecar in `artifacts/`.
Linux-specific filesystem checks skip on Windows. Tests use a simulated audio backend;
native acceptance is recorded separately in [VALIDATION.md](docs/VALIDATION.md).

`npm run dev` starts a browser UI preview with simulated audio and update responses.
It does not process audio or contact GitHub. See [USAGE.md](docs/USAGE.md) for development
details and the reusable EQ engine layout.

---

## Credits

- [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) and [Decky UI](https://github.com/SteamDeckHomebrew/decky-frontend-lib) - the plugin platform and native controls
- [PipeWire](https://pipewire.org) - the audio graph and built-in filters
- [GAZuOOT's Legion Go S audio preset](https://www.reddit.com/r/LegionGo/comments/1m7632y/legion_go_s_steam_os_audio_fix_pipewire_eq/) - the community preset included as **Legion Go S · Original**

Built by Rayek. DeckyEQ is an independent project and is not affiliated with Lenovo.
Full attribution and dependency terms are recorded in [NOTICE](NOTICE).

---

## License

BSD 3-Clause - see [LICENSE](LICENSE). Bundled dependency notices and source information
are listed in [NOTICE](NOTICE).

---

<div align="left">

*Vibe coded with AI assistance 🤖*

</div>
