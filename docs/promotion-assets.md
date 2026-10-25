# Promotion asset provenance

## Mac screenshots and clips (`shots/`)

`packaging/capture_site_mac.py` makes every file in `shots/` from the production AppKit windows: the bar (`mac.popup.Popup`), its caption, the writer toast and Review window, Settings on Voices, and the Get started tour. Retake them on a Mac with `uv run python packaging/capture_site_mac.py`, or name scenes: `reading`, `rewriting`, `settings`, `onboarding`. It needs ffmpeg on `PATH` and no Screen Recording permission.

The script grabs only its own windows with `CGWindowListCreateImageFromArray`, so nothing else on the screen can appear, and the window server draws the shadows. Stills keep a transparent background. Clips are grabbed 30 times a second inside a plain `#1c1c1f` panel and encoded as silent H.264 MP4, with the last frame saved as the poster. Pixels are the main display's: 1× on a 1× display. The site shows each file at its pixel size and only shrinks it to fit narrow screens.

The Notes window is a stand-in document with sample text, and its selection is drawn in the system selection color so the script never takes focus. Callbacks do nothing. Source statuses are fixtures: Cursor, Codex, and macOS ready, Kokoro not downloaded. The rewrite result is `tour_copy.sample_fix`, not a model's output, and playback is scripted with no audio. Captions use the Settings → Captions background option so they read over white.

## Windows images

These files document the current Windows selection and playback UI at reference commit `777d50d83ca471d7645e84e5f9a44faebebdabdf`. The native component pixels come from owned sample windows, with no personal desktop content, network calls, sign-in, or clipboard mutation. Button callbacks are no-ops, and visible source statuses are synthetic fixtures, not detected account state.

| File | Intrinsic size | Source and role |
| --- | ---: | --- |
| `popup.png` | 1200 × 560 | Styled sample-document frame containing the native 742 × 112 idle selection toolbar at 1:1 pixels. Shows a sample selected passage and the current Windows toolbar. |
| `toolbar.png` | 742 × 112 | Exact native crop of the idle toolbar from `harness_discussion.win.popup.Popup` / `_BarButton`, captured with Tk at 200% rendering (`tk scaling=2.664044575549`). Reusable, unframed component detail. |
| `captions.png` | 1200 × 400 | Styled production-rendered sample within a light document and charcoal surround, using the production `GdiLine` caption line over the native 900 × 112 playing toolbar at 1:1 pixels with a 20 px visible gap. This is not a live playback screenshot. |
| `settings.png` | 946 × 1196 | Exact full native Windows Settings capture of `harness_discussion.win.settings_ui.SettingsWindow`, on Voices, at the process's actual scale (`1.3339898243886428`). |
| `social.png` | 1200 × 630 | Share card with the current `icon.png`, short selection/listening copy, and the same native toolbar pixels at 1:1. Prepared for repository social-preview upload after this PR merges. |

`toolbar.png` and `settings.png` are byte-for-byte copies of the preserved raw captures in `C:\Windows\Temp\harness-publish\raw`. Styled framing is generated with Windows Segoe UI and Pillow; it adds only typography, sample-document framing, and a selection highlight. No native UI pixels are redrawn, interpolated, or enlarged. `popup.png` and `social.png` reuse the 742 × 112 toolbar pixels. `captions.png` reuses the 620 × 52 caption and 900 × 112 playing toolbar captures.

The Settings fixture shows Cursor ready, Codex ready, Kokoro not downloaded, Edge ready, and Windows ready. Those are synthetic statuses. The Voices controls accurately show pickers only for Cursor and Kokoro. This image is not evidence of a signed-in account or downloaded Kokoro. These assets do not claim Windows Writer or Dictation is working.

To retake these at a high level, create an owned Tk root and sample selection, instantiate the production Windows `Popup` with no-op callbacks and synthetic source state, then capture the idle and playing components at the documented Tk scale. Open the production `SettingsWindow` on Voices at the process's actual display scale and capture its full client window. Render a sample sentence with `GdiLine` from `harness_discussion.win.captions` for the caption line. Save raw captures outside the repository, record dimensions and scale, and compose only the surrounding frames; never use personal windows or live services as sample content.

The live GitHub repository social preview is unchanged. After this PR merges, upload `docs/social.png` separately in GitHub repository settings. No second 1280-pixel export is included.
