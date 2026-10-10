# FHStats Tracker

[![Desktop checks](https://github.com/jl220/fhstats-tracker-public/actions/workflows/ci.yml/badge.svg)](https://github.com/jl220/fhstats-tracker-public/actions/workflows/ci.yml)

FHStats Tracker 0.4.12 is a Windows desktop companion for Forza Horizon. It records supported online race results, keeps a local history, displays personal Rivals attempts and clean bests, and provides car and circuit reference information.

[Download for Windows](https://fhstats.com/download) · [FHStats website](https://fhstats.com)

This repository contains the public desktop application and its tests. It has an independent history. The hosted website, API and internal operations are maintained separately.

FHStats is an independent community project and is not affiliated with or endorsed by Microsoft, Xbox, Playground Games or Turn 10 Studios.

## Features

| Page | Purpose |
| :--- | :--- |
| Home | Current event and live race context |
| Online | Race history and performance summaries |
| Videos | Local recordings and playback |
| Meta Cars | Competitive car reference |
| Rivals | Personal attempts, clean bests and snapshot comparisons |
| My Garage | Local collection scans, inventory and profile backups |
| What's new | Release notes and update information |
| Settings | Language, privacy, telemetry, recording and overlay controls |

The desktop supports English and Spanish. The website is maintained separately and supports additional languages.

## Run from source

Use Windows 10 or 11 and Python 3.13 or later. Install [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) with English and Spanish language data for screen recognition. The portable download includes its required OCR runtime.

```powershell
git clone https://github.com/jl220/fhstats-tracker-public.git
cd fhstats-tracker-public
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m forza_tracker.ui.app
```

Choose a language and enter your gamertag on the first launch. Configure Tesseract in Settings if it is installed outside the default location. Automatic capture starts when a supported game process and screen are detected.

For live telemetry, follow the Data Out instructions in Settings. Collection scanning starts from the game's My Cars screen, reads each card by moving through the list, and stops if the screen or window focus is lost.

## Recordings and local data

History, settings, garage scans, captures and recordings are stored under `%LOCALAPPDATA%\FHStatsTracker`. For isolated development or testing, set `FHSTATS_TRACKER_DATA_DIR` to a separate directory before launching.

Uploading processed race results and sanitized diagnostics is optional and can be controlled in Settings. Screenshots and local race history remain on your computer.

Recording from source requires FFmpeg. Embedded playback requires mpv, and capturing audio from the game process requires the Windows audio helper. These optional binaries are included in the portable distribution and are not stored in this source repository. Configure runtime paths in your local settings when running from source.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp .test-tmp-local
.\.venv\Scripts\python.exe scripts/check_desktop_startup.py
```

Windows CI installs only this repository and public Python packages. It runs the desktop suite and checks both supported languages without private repositories or repository secrets.

## Contributing

Keep user data and local settings out of commits. Changes to public desktop pages must preserve English and Spanish navigation, labels and validation. Run the tests before opening a pull request.

Windows binaries are published through the maintained release process. This source repository does not replace the guarded binary release workflow.

## License

Code is distributed under the [MIT license](LICENSE). Forza Horizon names and referenced game imagery remain the property of their respective owners.
