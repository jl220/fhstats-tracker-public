<div align="center">

# FHStats Tracker

### Your car. Your tune. Your best time.

[![Source version](https://img.shields.io/badge/source-1.0.3-9ee82c)](CHANGELOG.md)
[![Desktop checks](https://github.com/jl220/fhstats-tracker-public/actions/workflows/ci.yml/badge.svg)](https://github.com/jl220/fhstats-tracker-public/actions/workflows/ci.yml)
[![Platform](https://img.shields.io/badge/platform-Windows_10_%2F_11-0078d4)](docs/getting-started.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A Windows companion for Forza Horizon, built around community competition, personal timing and useful race history.

[Website](https://fhstats.com) · [Creator Events](https://fhstats.com/dashboard) · [Getting started](docs/getting-started.md) · [Privacy](docs/privacy.md) · [Report an issue](https://github.com/jl220/fhstats-tracker-public/issues)

</div>

## What is available in 1.0.3?

| Area | Available functionality |
| :--- | :--- |
| Creator Events | Read the supported EventLab blueprint, in-game gamertag, car, class, PI and timing context from game memory. Keep clean improvements locally and submit them to FHStats when result uploads are enabled. |
| Clean timing | Circuits use a complete clean lap. Sprint and Cross Country use the complete clean run. Dirty, partial and aborted attempts do not replace clean bests. |
| Personal bests | Best clean community time for each supported track, car and class, with a persistent local upload queue and server receipts. |
| Home | Community track selector, blueprint codes, live timing and expandable Online and Rivals panels. |
| Online history | Saved race results, summaries and reference information. A separate memory reader observes supported series and translates known track IDs to English names. |
| Rivals | Local attempts, clean bests, live timing and sectors, plus comparisons with dated FHStats snapshots. Support varies by route and game state. |
| Meta Cars | Competitive car reference and local recommendations. |
| Videos | Optional game recordings and a local video library. |
| Overlay | Configurable race information overlay. |
| Settings and updates | English and Spanish desktop interface, recording and privacy controls, release notes and update checks. |

The legacy online result capture still contains screen recognition and OCR. Rivals also uses local Data Out timing. Community EventLab timing on PC uses its native memory reader. These are separate paths; 1.0.3 is not an entirely memory-only application. Read [how it works](docs/how-it-works.md).

## Four supported community blueprints

| Track | Format | Share code | Timing |
| :--- | :--- | :--- | :--- |
| Sportsland Sugawara | Road circuit | `477632104` | Best complete clean lap |
| Matsuri Netsu | Road sprint | `107181279` | Complete clean run |
| Kinkaku Ji S 4L | Dirt circuit | `437239865` | Best complete clean lap |
| Off-Road Capabilities Test | Cross Country | `219994682` | Complete clean run |

Choose your car and tune within your class. Reaching the maximum PI is not required. One complete clean lap is sufficient for a circuit, even when the blueprint has several laps. Edition dates, eligible classes, points and final standings are managed by the website, separately from local practice history.

For competition, claim your FHStats player card and meet the participation requirements shown on the event page. Set the tracker gamertag to match your active Forza player. The PC tracker reads the in-game name and checks that match; it does not require a Microsoft or Xbox password. A website account may still be required to manage your player card.

## PC and Xbox participation

On **PC**, keep the tracker running and enable processed race result uploads in Settings, Privacy. Enter one of the supported blueprints and complete a clean lap or run.

On **Xbox**, use the separate direct Data Out service. Your FHStats player page provides your personal destination IP and UDP port. Enter them in the separate Data Out fields in Forza. The Windows tracker cannot read an Xbox process through USB. Xbox route recognition and validation differ from PC memory capture; follow the current event instructions on FHStats.

## Run from source

Windows 10 or 11, Python 3.13 or later, and a compatible PC game build are required. Game updates can change memory layouts. FH6 is the current community event target; legacy components also recognize FH5, which does not imply equal support for every feature.

```powershell
git clone https://github.com/jl220/fhstats-tracker-public.git
cd fhstats-tracker-public
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m forza_tracker.ui.app
```

See [getting started](docs/getting-started.md) for optional Tesseract, FFmpeg, mpv, local Data Out and configuration details.

[Portable Windows downloads](https://fhstats.com/download) are built and distributed separately. This repository publishes reviewed desktop source, not a reproducible build attestation for an existing EXE. In particular, this source update does not rebuild or replace an already distributed 1.0.3 binary.

## Data and transparency

Local settings, race history, timing databases, captures and recordings normally live in `%LOCALAPPDATA%\FHStatsTracker`. Existing garage data is preserved, but garage scanning and its automatic recording are retired in 1.0.3.

The supplied configuration enables processed result uploads and diagnostic reports. Both have controls in Settings, Privacy. Restart an active legacy capture worker after changing these preferences. Disabling them does not disable update and catalog requests or erase server data. [Read the fields, defaults and limits](docs/privacy.md).

The native readers use read-only process access. This is not a guarantee of anti-cheat approval or perfect competitive validation. Client-submitted data and an evidence hash are not cryptographic proof of an unmodified client. The project has no official endorsement from Microsoft, Xbox, Playground Games or Turn 10 Studios.

## Public source boundary

This repository contains the public desktop source, default configuration, public reference assets and desktop tests. Private automation modules, their controls and resources are excluded from this 1.0.3 source tree. There is no hidden public switch that restores those modules.

The hosted API, website, scoring service, owner tools, personal data and private integration history are maintained separately. This independent repository is not a mirror of the private integration repository. See [repository structure](docs/how-it-works.md#repository-structure).

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp .test-tmp-local
.\.venv\Scripts\python.exe scripts/check_desktop_startup.py
```

Windows CI runs this repository with public Python packages. The startup check exercises the seven desktop pages in English and Spanish using isolated data and idle readers, without entering the game.

[Contributing](CONTRIBUTING.md) · [Security reports](SECURITY.md) · [Changelog](CHANGELOG.md)

## License

Source code is available under the [MIT license](LICENSE). Referenced game names and imagery belong to their respective owners.
