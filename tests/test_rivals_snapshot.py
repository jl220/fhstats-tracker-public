from __future__ import annotations

import json
from pathlib import Path

import pytest

from forza_tracker.rivals_snapshot import SnapshotCatalog, compare_attempt


def _entries(count: int, *, start_ms: int = 50_000) -> list[dict]:
    return [
        {"rank": rank, "player": f"Driver {rank}", "player_id": f"id-{rank}", "time": f"0:{(start_ms + rank * 10) / 1000:06.3f}"}
        for rank in range(1, count + 1)
    ]


def _payload(entries: list[dict], *, captured_at: str = "2026-08-11T09:35:27+02:00") -> dict:
    return {
        "mode": "rivals",
        "discipline": "dirt",
        "events": [{
            "index": 10,
            "slug": "sekibe-scramble",
            "name_en": "Sekibe Scramble",
            "name_es": "Circuito de tierra de Sekibe",
            "event_type": "dirt",
            "classes": [{"class": "A", "label": "A 700", "captured_at": captured_at, "entries": entries}],
        }],
    }


def _write(root: Path, payload: dict) -> None:
    (root / "dirt-rivals.json").write_text(json.dumps(payload), encoding="utf-8")


def test_player_snapshot_is_available_without_a_locally_recorded_clean_lap(tmp_path):
    from forza_tracker.rivals_snapshot import snapshot_summary
    entries = _entries(50)
    entries[24].update(player='Jolu4327', car='Alfa Romeo 33S', car_id=1549)
    _write(tmp_path, _payload(entries))
    board = SnapshotCatalog(tmp_path).board('dirt:sekibe-scramble', 'A')
    result = snapshot_summary(board, 'jolu4327')
    assert result['player_result']['rank'] == 25
    assert result['player_result']['time_ms'] == 50250
    assert result['player_result']['car'] == 'Alfa Romeo 33S'
    assert result['references']['top_50_ms'] == 50500
    assert result['captured_at'] == board['captured_at']
    assert snapshot_summary(board, 'other')['player_result'] is None
    assert snapshot_summary(None, 'Jolu4327') is None


def test_player_snapshot_does_not_guess_names_or_ambiguous_matches(tmp_path):
    from forza_tracker.rivals_snapshot import snapshot_summary
    entries = _entries(50)
    entries[0]['player'] = 'Jolu4327'
    entries[1]['player'] = 'JOLU4327'
    _write(tmp_path, _payload(entries))
    board = SnapshotCatalog(tmp_path).board('dirt:sekibe-scramble', 'A')
    assert snapshot_summary(board, 'Jolu4327')['player_result'] is None
    assert snapshot_summary(board, 'Jolu')['player_result'] is None


def test_board_lookups_hash_large_catalog_only_once_until_file_changes(monkeypatch, tmp_path):
    from forza_tracker import rivals_snapshot
    payload = _payload(_entries(50))
    _write(tmp_path, payload)
    original = rivals_snapshot._content_hash
    full_hashes = []
    def measured(value):
        if isinstance(value, dict) and 'events' in value:
            full_hashes.append(1)
        return original(value)
    monkeypatch.setattr(rivals_snapshot, '_content_hash', measured)
    catalog = SnapshotCatalog(tmp_path)
    for _ in range(30):
        assert catalog.board('dirt:sekibe-scramble', 'A')['references']['top_50_ms'] == 50500
    assert len(full_hashes) == 1
    payload['events'][0]['classes'][0]['entries'][-1]['time'] = '0:51.000'
    payload['revision'] = 'new file size'
    _write(tmp_path, payload)
    assert catalog.board('dirt:sekibe-scramble', 'A')['references']['top_50_ms'] == 51000
    assert len(full_hashes) == 2


def test_catalog_preserves_event_and_board_snapshot_metadata(tmp_path: Path) -> None:
    _write(tmp_path, _payload(_entries(50)))

    catalog = SnapshotCatalog(tmp_path)
    event = catalog.events()[0]
    board = catalog.board("dirt:sekibe-scramble", "A700")

    assert event == {
        "event_id": "dirt:sekibe-scramble", "event_name": "Circuito de tierra de Sekibe",
        "name_en": "Sekibe Scramble", "name_es": "Circuito de tierra de Sekibe",
        "name_fr": "Epreuve mixte de Sekibe", "name_ru": "Кольцевой кросс Сэкибэ",
        "event_kind": "circuit", "discipline": "dirt", "slug": "sekibe-scramble",
    }
    assert board is not None
    assert board["car_class"] == "A"
    assert board["captured_at"] == "2026-08-11T09:35:27+02:00"
    assert board["downloaded_at"] is None
    assert board["cache_status"] == "unchecked"
    assert board["coverage"] == "complete_top50"
    assert board["entries"][-1]["time_ms"] == 50_500
    assert board["snapshot_id"] == board["hash"]


def test_comparison_estimates_rank_and_exposes_references_for_complete_top50(tmp_path: Path) -> None:
    _write(tmp_path, _payload(_entries(50)))
    board = SnapshotCatalog(tmp_path).board("dirt:sekibe-scramble", "A 700")

    result = compare_attempt({"time_ms": 50_255, "completed": True, "validity": "clean"}, board)

    assert result["status"] == "estimated_rank"
    assert result["estimated_rank"] == 26
    assert result["cutoff_ms"] == 50_500
    assert result["gap_top50_ms"] == -245
    assert result["references"] == {"top_1_ms": 50_010, "top_10_ms": 50_100, "top_25_ms": 50_250, "top_50_ms": 50_500}


def test_comparison_does_not_invent_rank_outside_or_at_tied_cutoff(tmp_path: Path) -> None:
    _write(tmp_path, _payload(_entries(50)))
    board = SnapshotCatalog(tmp_path).board("dirt:sekibe-scramble", "A")

    tied = compare_attempt({"time_ms": 50_500.4, "completed": True, "validity": "clean"}, board)
    outside = compare_attempt({"time_ms": 54_315.502, "completed": True, "validity": "clean"}, board)

    assert tied["status"] == "tied_cutoff"
    assert tied["estimated_rank"] is None
    assert outside["status"] == "outside_top50"
    assert outside["estimated_rank"] is None
    assert outside["gap_top50_ms"] == pytest.approx(3815.502)


def test_partial_board_only_provides_known_references(tmp_path: Path) -> None:
    _write(tmp_path, _payload(_entries(11)))
    board = SnapshotCatalog(tmp_path).board("dirt:sekibe-scramble", "A")

    result = compare_attempt({"time_ms": 60_000, "completed": True, "validity": "clean"}, board)

    assert result["status"] == "partial"
    assert result["estimated_rank"] is None
    assert result["cutoff_ms"] is None
    assert result["references"] == {"top_1_ms": 50_010, "top_10_ms": 50_100}


def test_stable_identity_replaces_own_old_row_and_ties_are_uncertain(tmp_path: Path) -> None:
    _write(tmp_path, _payload(_entries(50)))
    board = SnapshotCatalog(tmp_path).board("dirt:sekibe-scramble", "A")

    replaced = compare_attempt({"time_ms": 50_205, "completed": True, "validity": "clean", "player_id": "id-10"}, board)
    tied = compare_attempt({"time_ms": 50_200.4, "completed": True, "validity": "clean"}, board)

    assert replaced["estimated_rank"] == 20
    assert tied["status"] == "estimated_rank"
    assert tied["rank_uncertain"] is True


def test_compare_revalidates_caller_supplied_board() -> None:
    malformed = {"coverage": "complete_top50", "entries": [{"rank": 1, "time_ms": 50_000}]}

    result = compare_attempt({"time_ms": 50_100, "completed": True, "validity": "clean"}, malformed)

    assert result["status"] == "malformed_snapshot"


@pytest.mark.parametrize("captured_at", [None, "", "2026-08-11T09:35:27", "not-a-date"])
def test_comparison_requires_timezone_aware_capture_date(tmp_path: Path, captured_at: str | None) -> None:
    _write(tmp_path, _payload(_entries(50), captured_at=captured_at))
    board = SnapshotCatalog(tmp_path).board("dirt:sekibe-scramble", "A")

    result = compare_attempt({"time_ms": 50_255, "completed": True, "validity": "clean"}, board)

    assert result["status"] == "malformed_snapshot"
    assert result["estimated_rank"] is None


def test_comparison_rejects_event_or_class_mismatch_and_includes_download_date(tmp_path: Path) -> None:
    _write(tmp_path, _payload(_entries(50)))
    board = SnapshotCatalog(tmp_path).board("dirt:sekibe-scramble", "A")
    board["downloaded_at"] = "2026-09-12T12:00:00+00:00"

    wrong_event = compare_attempt({"event_id": "dirt:other", "car_class": "A", "time_ms": 50_255, "completed": True, "validity": "clean"}, board)
    wrong_class = compare_attempt({"event_id": "dirt:sekibe-scramble", "car_class": "S1 800", "time_ms": 50_255, "completed": True, "validity": "clean"}, board)

    assert wrong_event["status"] == "not_comparable"
    assert wrong_class["status"] == "not_comparable"
    assert wrong_event["downloaded_at"] == "2026-09-12T12:00:00+00:00"


@pytest.mark.parametrize("attempt", [
    {"time_ms": 50_000, "completed": False, "validity": "clean"},
    {"time_ms": 50_000, "completed": True, "validity": "invalid"},
    {"time_ms": None, "completed": True, "validity": "clean"},
])
def test_ineligible_attempt_is_not_comparable(attempt: dict) -> None:
    assert compare_attempt(attempt, {"coverage": "partial", "entries": []})["status"] == "not_comparable"


def test_malformed_rank_order_is_guarded(tmp_path: Path) -> None:
    entries = _entries(50)
    entries[1]["rank"] = 3
    _write(tmp_path, _payload(entries))
    board = SnapshotCatalog(tmp_path).board("dirt:sekibe-scramble", "A")

    assert board is not None
    assert board["coverage"] == "invalid"
    assert compare_attempt({"time_ms": 50_000, "completed": True, "validity": "clean"}, board)["status"] == "malformed_snapshot"


class _Response:
    def __init__(self, payload: dict) -> None:
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        return None


def test_failed_refresh_preserves_old_file_and_board(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    old = _payload(_entries(11))
    _write(tmp_path, old)
    old_bytes = (tmp_path / "dirt-rivals.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr("forza_tracker.rivals_snapshot.requests.get", fail)
    catalog = SnapshotCatalog(tmp_path)

    assert catalog.refresh("dirt")["status"] == "error"
    assert (tmp_path / "dirt-rivals.json").read_bytes() == old_bytes
    assert len(catalog.board("dirt:sekibe-scramble", "A")["entries"]) == 11


def test_refresh_validates_whole_payload_before_atomic_replace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write(tmp_path, _payload(_entries(11)))
    invalid = _payload(_entries(50))
    invalid["discipline"] = "road"
    monkeypatch.setattr("forza_tracker.rivals_snapshot.requests.get", lambda *a, **k: _Response(invalid))

    result = SnapshotCatalog(tmp_path).refresh("dirt")

    assert result["status"] == "error"
    assert json.loads((tmp_path / "dirt-rivals.json").read_text())["events"][0]["classes"][0]["entries"] == _entries(11)


@pytest.mark.parametrize("captured_at", ["not-a-date", "2026-08-11T09:35:27"])
def test_refresh_rejects_invalid_capture_date_before_committing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, captured_at: str,
) -> None:
    old = _payload(_entries(11))
    _write(tmp_path, old)
    old_bytes = (tmp_path / "dirt-rivals.json").read_bytes()
    downloaded = _payload(_entries(50), captured_at=captured_at)
    monkeypatch.setattr("forza_tracker.rivals_snapshot.requests.get", lambda *a, **k: _Response(downloaded))

    result = SnapshotCatalog(tmp_path).refresh("dirt")

    assert result["status"] == "error"
    assert (tmp_path / "dirt-rivals.json").read_bytes() == old_bytes
    assert not (tmp_path / "rivals-snapshot-manifest.json").exists()


def test_explicit_refresh_reports_changed_then_unchanged_and_sets_download_date(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _payload(_entries(50))
    _write(tmp_path, _payload(_entries(11)))
    monkeypatch.setattr("forza_tracker.rivals_snapshot.requests.get", lambda *a, **k: _Response(payload))
    catalog = SnapshotCatalog(tmp_path)

    first = catalog.refresh("dirt")
    second = catalog.refresh("dirt")
    board = catalog.board("dirt:sekibe-scramble", "A")

    assert first["status"] == "changed"
    assert second["status"] == "unchanged"
    assert first["downloaded_at"]
    assert board["downloaded_at"] == second["downloaded_at"]
    assert board["cache_status"] == "verified"


def test_manifest_write_failure_does_not_replace_old_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    old = _payload(_entries(11))
    new = _payload(_entries(50))
    _write(tmp_path, old)
    old_bytes = (tmp_path / "dirt-rivals.json").read_bytes()
    monkeypatch.setattr("forza_tracker.rivals_snapshot.requests.get", lambda *a, **k: _Response(new))
    from forza_tracker import rivals_snapshot

    write_atomic = rivals_snapshot._write_atomic

    def fail_manifest(path: Path, text: str) -> None:
        if path.name == "rivals-snapshot-manifest.json":
            raise OSError("disk full")
        write_atomic(path, text)

    monkeypatch.setattr("forza_tracker.rivals_snapshot._write_atomic", fail_manifest)

    result = SnapshotCatalog(tmp_path).refresh("dirt")

    assert result["status"] == "error"
    assert (tmp_path / "dirt-rivals.json").read_bytes() == old_bytes


def test_catalog_replace_failure_keeps_old_bytes_and_does_not_claim_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    old = _payload(_entries(11))
    new = _payload(_entries(50))
    _write(tmp_path, old)
    old_bytes = (tmp_path / "dirt-rivals.json").read_bytes()
    monkeypatch.setattr("forza_tracker.rivals_snapshot.requests.get", lambda *a, **k: _Response(new))
    from forza_tracker import rivals_snapshot

    write_atomic = rivals_snapshot._write_atomic

    def fail_catalog(path: Path, text: str) -> None:
        if path.name == "dirt-rivals.json":
            raise OSError("replace failed")
        write_atomic(path, text)

    monkeypatch.setattr("forza_tracker.rivals_snapshot._write_atomic", fail_catalog)
    catalog = SnapshotCatalog(tmp_path)

    result = catalog.refresh("dirt")

    assert result["status"] == "error"
    assert (tmp_path / "dirt-rivals.json").read_bytes() == old_bytes
    assert catalog.board("dirt:sekibe-scramble", "A")["cache_status"] == "unchecked"
