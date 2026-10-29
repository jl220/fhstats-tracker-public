# Data and privacy

This document describes the public 1.0.3 desktop source. It is not an independent security audit, a reverse engineering audit of third-party binaries or a complete server retention policy.

## Local storage

The usual data directory is `%LOCALAPPDATA%\FHStatsTracker`, with an override available through `FHSTATS_TRACKER_DATA_DIR`. Depending on enabled features, it stores settings, gamertag, race history, recognized text, screenshots, logs, telemetry status, local Rivals attempts, community timing evidence, SQLite queues, upload receipts and optional videos. Old garage files and profile backups are preserved.

Legacy screen capture can inspect connected monitors and save the matched monitor image. Other visible windows or notifications can therefore appear in local captures. Optional game recording has a separate capture path. Do not assume that all capture code is limited to the game window.

The public community reader requests read-only process query and memory access. It reads game state rather than modifying process memory. Optional overlay input handling and legacy components have their own input paths.

## Network activity

| Purpose | Information used |
| :--- | :--- |
| Processed online results | Gamertag, game label, timestamps, race or series context, track, discipline, car, class, positions, available times and parser version. |
| Community improvement submission | Observed gamertag, attempt and track identifiers, car, class, PI, time, recording timestamp, tracker version and evidence hash. The hash is not the full local evidence. |
| Diagnostics | Gamertag, tracker version, component, error type and message, timestamp and technical context. These reports are not anonymous. |
| Updates and catalogs | HTTPS requests for versions, release notes and reference data. The receiving infrastructure also sees normal connection metadata, including IP address. |

The processed result paths do not upload screenshots, recordings or the complete local evidence automatically. Review any files you choose to attach to an issue yourself.

The public source also contains an optional FHStats account client. The current community Home flow uses the in-game gamertag and game submission client, without an account sign-in form. It does not ask for a Microsoft or Xbox password.

## Defaults and controls

The distributed YAML enables result uploads and diagnostics. The dataclass fallback defaults differ, so do not infer effective settings from a class definition alone. Recording and local Data Out are disabled by default in the supplied configuration.

Use Settings, Privacy to change uploads and diagnostics. Community upload preferences update directly, while an already running legacy capture process must be restarted to use changed settings. These switches do not cancel a request already in flight, erase stored history, remove server records or turn off every network request.

Diagnostics apply bounded message sanitization. It should not be treated as a guarantee that every possible secret in arbitrary exception text is removed. Review logs before publishing them. Never place credentials in a bug report.

## Known limits

Game updates can break memory layouts. Read-only access does not establish approval by the game publisher. The client can be modified by its operator, and an evidence hash alone does not prove a submission is authentic.

Updates use HTTPS and compare a downloaded package with its supplied SHA256. This is not the same as independently signed source provenance or a reproducible build. Existing portable binaries are distributed separately from this source tree.

Local UDP reception does not authenticate a sender. Xbox Data Out route and clean-run checks are a separate service and do not provide the same evidence as the PC memory path.
