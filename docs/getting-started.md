# Getting started

## Before the first run

1. Install Python 3.13 or later on Windows 10 or 11 and follow the commands in the README.
2. Choose English or Spanish at first launch. Set your gamertag to match the active Forza gamertag exactly. This is required for community capture and submissions as well as legacy history and Rivals matching. The memory reader checks the observed name against this setting.
3. Open Settings, Privacy before playing and choose whether to send processed race results and diagnostics. The supplied YAML enables both. Restart a running capture worker after changing these preferences.
4. Open Forza and keep the tracker running. For community participation, claim your player card on FHStats and follow the edition rules on the website.

## Community timing

Select a supported track on Home to see its share code. Search that exact blueprint in EventLab. A circuit needs one full clean lap, while a sprint or Cross Country needs a complete clean run. A partial first lap, rewind, invalid attempt or abandoned restart must not replace a clean best. The local queue retries supported uploads and keeps receipts. Acceptance and event scoring remain server decisions.

## Optional runtimes

| Runtime | Used by | Setup from source |
| :--- | :--- | :--- |
| Tesseract | Legacy screen recognition and OCR | Install Tesseract with English and Spanish language data, then check its path in Settings. |
| FFmpeg | Optional video recording | Supply a compatible binary and configure its path. Recording is off by default. |
| mpv | Embedded video playback | Supply a compatible runtime and configure its path. |
| Windows audio helper | Optional process audio capture | The portable distribution supplies this separately built helper. Its binary is not included in this source tree. |

The source tree does not bundle the game or these optional runtime binaries.

## Local Data Out

Some Rivals and overlay features use UDP telemetry alongside memory reads. If enabled, configure Forza to send to the PC and the port shown in Settings, normally 9999. For same-PC reception, use `127.0.0.1` as the game destination. The configured listener can bind all interfaces; restrict firewall access according to your setup. Enabling local telemetry does not authenticate its sender.

The former optional PC forwarding introduced in 1.0.2 is retired in 1.0.3. Old forwarding settings do not send raw UDP to the event server. PC community result uploads use HTTPS instead.

Xbox participation uses the independent server destination shown in your player page, not this local PC port. IP and port are separate game settings, not a web URL or a USB connection.

## Configuration and storage

The repository contains defaults in `config/tracker.yaml`. Personal overrides normally live in `%LOCALAPPDATA%\FHStatsTracker\config\tracker.local.yaml`. Keep personal files out of Git.

For isolated development, set `FHSTATS_TRACKER_DATA_DIR` to a separate directory before launching. Do not point a test suite at your normal data directory. Existing histories, recordings and garage backups should be retained when updating.

## Troubleshooting

| Symptom | Check |
| :--- | :--- |
| Community timing waits indefinitely | The game is running, the exact supported blueprint is active, the configured gamertag matches the active Forza player, and the game build is compatible with the reader. A `profile_mismatch` status means the configured and observed names differ. |
| A dirty run is not saved | Complete a new full clean lap or run. Do not force an invalid attempt into a clean best. |
| A local improvement has not appeared online | Result uploads, connection, queue status, claimed profile and current event eligibility. A local best is not proof of server acceptance. |
| OCR is unavailable | Tesseract executable path and installed language data. This affects legacy capture, not native community blueprint identification. |
| Video does not record or play | Optional FFmpeg, mpv and audio helper paths, available disk space and recording settings. |

Report the tracker version, game build, affected blueprint and steps to reproduce. Review logs before sharing and remove tokens, email addresses, private paths and unrelated screen content.
