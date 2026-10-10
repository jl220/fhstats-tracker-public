from __future__ import annotations

import struct

import pytest

from forza_tracker.telemetry.forza_udp import parse_forza_horizon_packet


def test_parse_forza_horizon_6_packet_extracts_race_fields() -> None:
    packet = bytearray(324)
    struct.pack_into("<i", packet, 0, 1)
    struct.pack_into("<I", packet, 4, 123_456)
    struct.pack_into("<i", packet, 212, 42_123)
    struct.pack_into("<i", packet, 216, 4)
    struct.pack_into("<i", packet, 220, 800)
    struct.pack_into("<i", packet, 224, 2)
    struct.pack_into("<i", packet, 228, 6)
    struct.pack_into("<I", packet, 232, 77)
    struct.pack_into("<f", packet, 236, 1.25)
    struct.pack_into("<f", packet, 240, 12.5)
    struct.pack_into("<f", packet, 256, 68.0)
    struct.pack_into("<f", packet, 292, 1_234.5)
    struct.pack_into("<f", packet, 296, 74.25)
    struct.pack_into("<f", packet, 300, 75.5)
    struct.pack_into("<f", packet, 304, 12.75)
    struct.pack_into("<f", packet, 308, 188.4)
    struct.pack_into("<H", packet, 312, 2)
    struct.pack_into("<B", packet, 314, 3)
    struct.pack_into("<B", packet, 315, 180)
    struct.pack_into("<B", packet, 316, 20)
    struct.pack_into("<b", packet, 320, -12)

    parsed = parse_forza_horizon_packet(bytes(packet))

    assert parsed.is_race_on is True
    assert parsed.timestamp_ms == 123_456
    assert parsed.car_ordinal == 42_123
    assert parsed.car_class_index == 4
    assert parsed.car_class == "S1"
    assert parsed.performance_index == 800
    assert parsed.drivetrain_type == 2
    assert parsed.num_cylinders == 6
    assert parsed.car_group == 77
    assert parsed.smashable_velocity_difference_mps == pytest.approx(1.25)
    assert parsed.smashable_mass_kg == pytest.approx(12.5)
    assert parsed.speed_kmh == pytest.approx(244.8)
    assert parsed.distance_traveled_m == pytest.approx(1_234.5)
    assert parsed.best_lap_seconds == pytest.approx(74.25)
    assert parsed.last_lap_seconds == pytest.approx(75.5)
    assert parsed.current_lap_seconds == pytest.approx(12.75)
    assert parsed.current_race_time_seconds == pytest.approx(188.4)
    assert parsed.lap_number == 2
    assert parsed.race_position == 3
    assert parsed.accel == 180
    assert parsed.brake == 20
    assert parsed.steer == -12


def test_parse_forza_horizon_packet_rejects_short_packets() -> None:
    with pytest.raises(ValueError, match="too short"):
        parse_forza_horizon_packet(b"\x00" * 32)
