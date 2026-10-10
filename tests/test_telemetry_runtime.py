from __future__ import annotations

import struct
from pathlib import Path

from forza_tracker.config.settings import load_settings
from forza_tracker.telemetry.forza_udp import parse_forza_horizon_packet
from forza_tracker.telemetry.listener import handle_telemetry_packet
from forza_tracker.telemetry.status import read_telemetry_status, write_telemetry_status


def _fh6_packet(*, race_position: int = 3, race_time: float = 12.5) -> bytes:
    packet = bytearray(324)
    struct.pack_into("<i", packet, 0, 1)
    struct.pack_into("<I", packet, 4, 123_456)
    struct.pack_into("<i", packet, 212, 2119)
    struct.pack_into("<i", packet, 216, 2)
    struct.pack_into("<i", packet, 220, 600)
    struct.pack_into("<i", packet, 224, 2)
    struct.pack_into("<i", packet, 228, 10)
    struct.pack_into("<I", packet, 232, 17)
    struct.pack_into("<f", packet, 256, 42.0)
    struct.pack_into("<f", packet, 292, 777.0)
    struct.pack_into("<f", packet, 304, 4.5)
    struct.pack_into("<f", packet, 308, race_time)
    struct.pack_into("<H", packet, 312, 1)
    struct.pack_into("<B", packet, 314, race_position)
    struct.pack_into("<B", packet, 315, 255)
    struct.pack_into("<B", packet, 319, 4)
    return bytes(packet)


def test_settings_loads_fh6_telemetry_defaults(tmp_path: Path) -> None:
    config = tmp_path / "tracker.yaml"
    config.write_text("app:\n  player_name: Test\n", encoding="utf-8")

    settings = load_settings(config)

    assert settings.telemetry.enabled is False
    assert settings.telemetry.host == "0.0.0.0"
    assert settings.telemetry.port == 9999
    assert settings.telemetry.game == "fh6"
    assert settings.telemetry.auto_configure is False


def test_settings_accepts_custom_telemetry_values(tmp_path: Path) -> None:
    config = tmp_path / "tracker.yaml"
    config.write_text(
        "\n".join(
            [
                "telemetry:",
                "  enabled: true",
                "  host: 127.0.0.1",
                "  port: 10001",
                "  auto_configure: true",
                "  stale_after_seconds: 4.5",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.telemetry.enabled is True
    assert settings.telemetry.host == "127.0.0.1"
    assert settings.telemetry.port == 10001
    assert settings.telemetry.auto_configure is True
    assert settings.telemetry.stale_after_seconds == 4.5


def test_write_telemetry_status_includes_latest_fh6_fields(tmp_path: Path) -> None:
    status_path = tmp_path / "runtime" / "telemetry-status.json"
    packet = parse_forza_horizon_packet(_fh6_packet(race_position=2, race_time=31.5))

    write_telemetry_status("connected", packet=packet, message="FH6 telemetry connected", path=status_path)

    payload = read_telemetry_status(status_path)
    assert payload["state"] == "connected"
    assert payload["connected"] is True
    assert payload["message"] == "FH6 telemetry connected"
    assert payload["game"] == "fh6"
    assert payload["packet_length"] == 324
    assert payload["race_position"] == 2
    assert payload["current_race_time_seconds"] == packet.current_race_time_seconds
    assert payload["car_class"] == "B"
    assert payload["performance_index"] == 600
    assert payload["car_display_name"] == "1984 Honda CRXMugen"
    assert payload["car_name_confidence"] == "id-from-game-asset-name-auto-cleaned"
    assert "car_asset" not in payload
    assert payload["updated_at"]


def test_handle_telemetry_packet_writes_connected_status(tmp_path: Path) -> None:
    status_path = tmp_path / "runtime" / "telemetry-status.json"

    parsed = handle_telemetry_packet(_fh6_packet(race_position=1), ("127.0.0.1", 9999), status_path=status_path)

    payload = read_telemetry_status(status_path)
    assert parsed.race_position == 1
    assert payload["state"] == "connected"
    assert payload["remote"] == "127.0.0.1:9999"
    assert payload["race_position"] == 1


def test_handle_telemetry_packet_writes_invalid_status(tmp_path: Path) -> None:
    status_path = tmp_path / "runtime" / "telemetry-status.json"

    parsed = handle_telemetry_packet(b"\x00" * 12, ("127.0.0.1", 9999), status_path=status_path)

    payload = read_telemetry_status(status_path)
    assert parsed is None
    assert payload["state"] == "invalid_packet"
    assert payload["connected"] is False
    assert "too short" in payload["message"]


def test_write_telemetry_status_retries_temporary_windows_replace_denial(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import forza_tracker.telemetry.status as telemetry_status

    status_path = tmp_path / "runtime" / "telemetry-status.json"
    real_replace = telemetry_status.os.replace
    calls: list[tuple[object, object]] = []

    def flaky_replace(source, destination) -> None:
        calls.append((source, destination))
        if len(calls) < 3:
            raise PermissionError(5, "Acceso denegado")
        real_replace(source, destination)

    monkeypatch.setattr(telemetry_status.os, "replace", flaky_replace)
    monkeypatch.setattr(telemetry_status.time, "sleep", lambda _seconds: None)

    write_telemetry_status("listening", message="ready", path=status_path)

    assert len(calls) == 3
    assert read_telemetry_status(status_path)["state"] == "listening"
    assert not list(status_path.parent.glob("telemetry-status*.tmp"))


def test_read_status_recovers_from_windows_replacement_lock(monkeypatch, tmp_path):
    import forza_tracker.telemetry.status as status
    path = tmp_path / 'status.json'
    path.write_text('{"connected": true, "timestamp_ms": 123}', encoding='utf8')
    original = Path.open
    failures = [PermissionError(13, 'Permission denied'), PermissionError(13, 'Permission denied')]
    def opening(self, *args, **kwargs):
        if self == path and failures:
            raise failures.pop()
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', opening)
    monkeypatch.setattr(status.time, 'sleep', lambda _: None)
    assert status.read_telemetry_status(path) == {'connected': True, 'timestamp_ms': 123}


def test_read_status_permanent_failure_is_bounded_and_never_reuses_old_data(monkeypatch, tmp_path):
    import forza_tracker.telemetry.status as status
    calls = []
    def opening(*args, **kwargs):
        calls.append(1)
        raise PermissionError(13, 'Permission denied')
    monkeypatch.setattr(Path, 'open', opening)
    monkeypatch.setattr(status.time, 'sleep', lambda _: None)
    assert status.read_telemetry_status(tmp_path / 'status.json') == {}
    assert 1 <= len(calls) <= 4


def test_live_listener_discards_queued_old_packets_before_status_write(monkeypatch, tmp_path):
    from forza_tracker.telemetry import listener
    queue = [(_fh6_packet(race_time=t), ('127.0.0.1', 123)) for t in (10, 11, 12)]
    ended = False
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): pass
        def settimeout(self, value): pass
        def setblocking(self, value): pass
        def recvfrom(self, size):
            if queue: return queue.pop(0)
            raise BlockingIOError()
    written = []
    def writer(state, **kwargs):
        nonlocal ended
        if state == 'connected':
            written.append(kwargs['packet'].current_race_time_seconds)
            ended = True
    monkeypatch.setattr(listener.socket, 'socket', lambda *a: Socket())
    monkeypatch.setattr(listener, 'write_telemetry_status', writer)
    monkeypatch.setattr(listener.time, 'monotonic', lambda: 2 if ended else 0)
    listener.listen_forza_horizon_6_udp(duration_seconds=1, status_path=tmp_path / 'status.json', emit_summary=False)
    assert written == [12]


def test_listener_survives_a_status_file_write_failure(monkeypatch, tmp_path):
    from forza_tracker.telemetry import listener
    ended = False
    received = 0
    class Socket:
        blocking = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): pass
        def settimeout(self, value): self.blocking = True
        def setblocking(self, value): self.blocking = value
        def recvfrom(self, size):
            nonlocal received
            if not self.blocking: raise BlockingIOError()
            received += 1
            return _fh6_packet(race_time=received), ('127.0.0.1', 123)
    written = []
    def writer(state, **kwargs):
        nonlocal ended
        if state == 'connected':
            if received == 1:
                raise PermissionError(5, 'Access denied during replacement')
            written.append(kwargs['packet'].current_race_time_seconds)
            ended = True
    monkeypatch.setattr(listener.socket, 'socket', lambda *a: Socket())
    monkeypatch.setattr(listener, 'write_telemetry_status', writer)
    monkeypatch.setattr(listener.time, 'monotonic', lambda: 2 if ended else 0)
    listener.listen_forza_horizon_6_udp(duration_seconds=1, status_path=tmp_path / 'status.json', emit_summary=False)
    assert written == [2]
