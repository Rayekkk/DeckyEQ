# Legion Go S

Author of the community settings: **GAZuOOT**. Verified against the original
download and native PipeWire 1.6.8 on Legion Go 2 on 2026-09-07.

- [Original community post](https://www.reddit.com/r/LegionGo/comments/1m7632y/legion_go_s_steam_os_audio_fix_pipewire_eq/)
- [Author's original archive](https://drive.google.com/file/d/16DVfDOjTxMydcb2Qrs5lBwZJ8AOf1yo3/view)
- [PipeWire filter-chain documentation](https://pipewire.pages.freedesktop.org/pipewire/page_module_filter_chain.html)
- [PipeWire 1.6.8 biquad implementation](https://github.com/PipeWire/pipewire/blob/1.6.8/spa/plugins/audioconvert/biquad.c)

Choose **Legion Go S** from the plugin's preset dropdown. It is built
in; no import or extra dependency is needed. Edit it live and choose **Save as
new…** to keep your version. The original remains available. A standalone
DeckyEQ export is included at `presets/legion-go-s-original.json`.

## Exact mapping

The source uses native biquads despite its `convolver.conf` filename. It has no
convolution impulse, compressor or limiter. Every filter uses Q = 1.

| Source filter | Frequency in file | Actual native frequency | Gain | DeckyEQ equivalent |
| --- | ---: | ---: | ---: | --- |
| High shelf | 0 Hz | 0 Hz | +8 dB | Input level +8 dB |
| Low shelf | 31.25 Hz | 31.25 Hz | -10 dB | Band 1, low shelf |
| Bell | `62.5.0` | 0 Hz | -4 dB | Band 2, bell at 0 Hz |
| Bell | 125 Hz | 125 Hz | +5 dB | Band 3 |
| Bell | 250 Hz | 250 Hz | +5 dB | Band 4 |
| Bell | 500 Hz | 500 Hz | -5 dB | Band 5 |
| High shelf | 1000 Hz | 1000 Hz | -5 dB | Band 6, high shelf |
| Bell | 2000 Hz | 2000 Hz | -5 dB | Band 7 |
| Bell | 40000 Hz | 40000 Hz | -5 dB | Band 8 |
| High shelf | 80000 Hz | 48000 Hz | 0 dB | Band 9, high shelf |
| Bell | 16000 Hz | 16000 Hz | 0 dB | Band 10 |

Bass and treble tone sliders are both 0 dB. Automatic headroom, always on since 1.1,
lowers its level by about 0.6 dB; the limiter catches the rest.
The source's high shelf at 0 Hz is a constant +8 dB gain, so DeckyEQ represents
it with a linear input multiplier of 10^(8/20), approximately 2.511886.

The malformed `62.5.0` is read as 0 Hz on the tested native parser. The 80 kHz
control is clamped to 48 kHz. These are observed values, not proposed fixes for
the author's likely intentions. The 40 kHz entry is deliberately not changed
to 4 kHz. At a 48 kHz sample rate, the bells at 0 and 40 kHz are identity filters;
the two 0 dB filters also leave the signal unchanged. At other sample rates,
keeping the same native parameters preserves the corresponding behavior.

In practice the active EQ reduces the lowest bass, boosts 125/250 Hz, cuts
500 Hz and 2 kHz, lowers the region above 1 kHz and adds input gain. Shelves at
Q = 1 have some resonance, so this is not a set of hard frequency boundaries.
The extra gain would clip without the limiter, which now holds it at -1 dBFS.
Lowering **Input level** trades loudness for less limiting.

This reproduces the parsed filter settings and intended DSP response, subject
to floating-point rounding. It is not a claim of identical PCM bytes, Windows
audio processing, or identical acoustic results on the different Go 2 speakers.
PipeWire routing, hardware mixer levels and source volumes remain governed by
the user's audio session; DeckyEQ does not touch them.

## Source identity

- Downloaded archive: 38,634 bytes.
- Archive SHA-256: `e138215cb310092e97ec258724f5cb53f000e595d4844cdf43b6e7f002a1ca14`
- Configuration: `pipewire/pipewire.conf.d/convolver.conf`.
- Configuration SHA-256: `ab8a2e4fa126be081b88d998b2c43ad35b1aa65015ff50061e696151422dd63d`.

The source archive and configuration are not shipped with DeckyEQ.
