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
| **Your own presets** | Save up to 64 user presets; update, rename, copy, delete or revert to the saved sound |
| **Built-in presets** | Start from Flat or the community **Legion Go S · Original** preset |
| **Import and export** | Share individual sounds or your saved preset library as portable JSON files |
| **Saved drafts** | Keeps your latest edits when you close QAM or restart Decky |
| **Level and headroom** | Adjust input gain and optionally compensate for frequency boosts |
| **Output selection** | Apply EQ to your chosen output, with speaker-only bypass enabled by default |
| **Normal volume control** | Sets the physical output to 100% before routing through EQ; keeps your listening volume on the virtual output |
| **Compact QAM interface** | Native Decky sliders, dropdowns and full-width action buttons |
| **Plugin updates** | Check GitHub for a newer release and download its verified installation ZIP to your Downloads folder |

---

## Requirements

| Requirement | Details |
|---|---|
| System | SteamOS with [Decky Loader](https://decky.xyz) and a working desktop audio session |
| Tested handheld | Lenovo Legion Go 2 on **SteamOS 3.10** with **PipeWire 1.6.8** |
| System tools | `pipewire`, `pw-cli`, `pw-dump`, `pactl` and `python3` |
| Extra audio packages | None on the tested stock system; no EasyEffects or external effects plugins required |
| Internet | Only for checking and downloading plugin updates |

Native filter updates, preset exchange, volume transfer and routing restoration were
verified on Legion Go 2. Listening tests, physical controller navigation and suspend/resume
acceptance remain open. Other handhelds have not been validated. See the
[validation record](docs/VALIDATION.md) for the scope of testing.

---

## Installation

1. Install [Decky Loader](https://decky.xyz) if it is not already installed.
2. Download the **DeckyEQ-<version>.zip** asset from the [latest release](https://github.com/Rayekkk/DeckyEQ/releases/latest), or build it from source below.
3. In Gaming Mode, open **Quick Access Menu → Decky → Settings → Developer**.
4. Choose **Install Plugin from ZIP** and select the archive.
5. Open **DeckyEQ** in the Decky menu and turn on **Enable EQ**.

The package is `artifacts/DeckyEQ-1.0.0.zip`, containing one `DeckyEQ` folder.
If no suitable speaker output is selected automatically, choose one in **View → Output**.

---

## Usage

| View | Controls |
|---|---|
| **Equalizer** | Choose a preset, adjust Bass, Treble and band gains, or enable advanced filter editing |
| **Presets** | Name and save sounds, manage your library, and import or export JSON files |
| **Output** | Choose an audio output, configure headroom and input level, and reset the current controls |
| **About** | Plugin version, author, license and GitHub updates |

Play familiar audio, make your adjustments, then open **Presets**, enter a name and
select **Save as new preset**. Use **Update selected preset** to save later changes
to that entry. Built-in presets can be edited and saved as your own copies.

**Export current sound** includes your latest edits. **Export saved preset library**
includes saved user presets. Exports go to `~/Documents/DeckyEQ/` by default; you can
also choose a folder. Import accepts DeckyEQ JSON files, including the earlier preset
format. EasyEffects and Equalizer APO files need conversion before use.

<details>
<summary><b>Legion Go S · Original preset</b></summary>

<br>

The included preset follows the filters actually parsed by PipeWire from GAZuOOT's
community Go S configuration, including its +8 dB input boost and unusual frequency
values. Automatic headroom is off for this preset. It can distort at high levels;
lower **Input level** or enable **Automatic headroom** to adjust it for your speakers.

This is community tuning for Go S, not a measured Go 2 calibration. The
[source notes and exact parameters](docs/GO_S_PRESET.md) describe the mapping.

</details>

---

## Updates

Open **View → About → Updates** and select **Check for Updates**. When a newer stable
release is available, select **Download**. The plugin saves the installation ZIP to
your Downloads folder after checking its GitHub SHA-256 digest, size, contents and version.

Install the downloaded ZIP through **Decky Settings → Developer → Install Plugin from ZIP**.
Checks and downloads run only when requested. The plugin does not install updates itself.
If the repository has no public release yet, it shows **No public release yet**.

---

## How it works

DeckyEQ processes stereo playback with PipeWire's built-in filters. Slider and filter
changes update the running graph without restarting PipeWire. It uses your existing
audio session and runs without root privileges or extra audio packages.

Before switching playback to the virtual **DeckyEQ** output, the plugin sets the physical
output to **100%** and moves your previous volume and mute setting to DeckyEQ. Steam's
normal volume controls then adjust that virtual output. Disabling EQ transfers the current
volume back and restores hardware playback.

**Speaker port only** bypasses EQ when the selected device switches away from its speaker
port. Turn it off to apply EQ to a selected headset or HDMI output. A manual output change
in Steam is respected: selecting the physical speaker directly bypasses EQ and returns
your volume to it, instead of playing at the 100% hardware level. Use **Refresh** in
**Output** to resume. Disabling or unloading the plugin stops its audio processing and
keeps your preset library.

Automatic headroom can make boosted presets quieter by reserving room for their peaks.
It is not a limiter. DeckyEQ provides sound tuning; it does not reproduce Windows/Nahimic
processing or claim to fix a confirmed Go 2 driver defect.

See the [usage and implementation guide](docs/USAGE.md) for filter ranges, preset formats,
headroom calculations and output recovery details.

---

## Troubleshooting

<details>
<summary><b>EQ is unavailable or paused</b></summary>

<br>

Read the reason shown in the plugin. Confirm the intended device under **View → Output**,
then use **Refresh / reconnect EQ**. With **Speaker port only** enabled, an active
headphone port or an unknown port causes bypass. A missing system tool is reported
in the interface.

</details>

<details>
<summary><b>The sound is quieter or distorted</b></summary>

<br>

Automatic headroom lowers the input level to make room for boosts. Large or overlapping
boosts may need substantial compensation. If you hear distortion with headroom off,
reduce **Input level** or enable **Automatic headroom** in **View → Output**.
The built-in Go S preset intentionally retains the original gain settings.

</details>

<details>
<summary><b>My edits are not in the exported library</b></summary>

<br>

Library export contains saved presets. Save or update the preset first, or use
**Export current sound** to export the current draft directly. Importing a file adds
presets without overwriting existing entries or changing playback automatically.

</details>

<details>
<summary><b>An update cannot be downloaded</b></summary>

<br>

Check your connection and retry later if GitHub reports a request limit. A release must
contain a matching **DeckyEQ-X.Y.Z.zip** with trusted GitHub size and digest metadata.
If a release changes between checking and downloading, check for updates again.
Incomplete or unverified archives are not offered as installation files.

</details>

---

## Development

Build requirements: Node.js 20+ / npm and Python 3.10+. Users install the prebuilt ZIP.

```bash
npm ci
npm run typecheck  # check frontend types
npm test           # Python backend tests
npm run test:ui    # UI and lifecycle regressions
npm run build      # compile the frontend
npm run package    # package the current build
```

`npm run check` runs all validation and packaging steps. The archive includes frontend
source, build inputs and dependency notices, with a SHA-256 sidecar in `artifacts/`.
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
