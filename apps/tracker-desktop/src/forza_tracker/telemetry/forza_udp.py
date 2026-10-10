from __future__ import annotations

import struct
from dataclasses import asdict, dataclass
from typing import Any


FORZA_HORIZON_6_PACKET_BYTES = 324
CAR_CLASS_NAMES = {
    0: "D",
    1: "C",
    2: "B",
    3: "A",
    4: "S1",
    5: "S2",
    6: "R",
    7: "X",
}


@dataclass(frozen=True)
class ForzaHorizonTelemetry:
    packet_length: int
    is_race_on: bool
    timestamp_ms: int
    car_ordinal: int
    car_class_index: int
    car_class: str
    performance_index: int
    drivetrain_type: int
    num_cylinders: int
    car_group: int
    smashable_velocity_difference_mps: float
    smashable_mass_kg: float
    speed_mps: float
    speed_kmh: float
    distance_traveled_m: float
    best_lap_seconds: float
    last_lap_seconds: float
    current_lap_seconds: float
    current_race_time_seconds: float
    lap_number: int
    race_position: int
    accel: int
    brake: int
    clutch: int
    handbrake: int
    gear: int
    steer: int

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_forza_horizon_packet(data: bytes) -> ForzaHorizonTelemetry:
    if len(data) < FORZA_HORIZON_6_PACKET_BYTES:
        raise ValueError(
            f"Forza Horizon 6 UDP packet too short: {len(data)} bytes; "
            f"expected at least {FORZA_HORIZON_6_PACKET_BYTES}"
        )

    car_class_index = _int32(data, 216)
    speed_mps = _float32(data, 256)
    return ForzaHorizonTelemetry(
        packet_length=len(data),
        is_race_on=bool(_int32(data, 0)),
        timestamp_ms=_uint32(data, 4),
        car_ordinal=_int32(data, 212),
        car_class_index=car_class_index,
        car_class=CAR_CLASS_NAMES.get(car_class_index, str(car_class_index)),
        performance_index=_int32(data, 220),
        drivetrain_type=_int32(data, 224),
        num_cylinders=_int32(data, 228),
        car_group=_uint32(data, 232),
        smashable_velocity_difference_mps=_float32(data, 236),
        smashable_mass_kg=_float32(data, 240),
        speed_mps=speed_mps,
        speed_kmh=speed_mps * 3.6,
        distance_traveled_m=_float32(data, 292),
        best_lap_seconds=_float32(data, 296),
        last_lap_seconds=_float32(data, 300),
        current_lap_seconds=_float32(data, 304),
        current_race_time_seconds=_float32(data, 308),
        lap_number=_uint16(data, 312),
        race_position=_uint8(data, 314),
        accel=_uint8(data, 315),
        brake=_uint8(data, 316),
        clutch=_uint8(data, 317),
        handbrake=_uint8(data, 318),
        gear=_uint8(data, 319),
        steer=_int8(data, 320),
    )


def _int32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _uint32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _uint16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _uint8(data: bytes, offset: int) -> int:
    return struct.unpack_from("<B", data, offset)[0]


def _int8(data: bytes, offset: int) -> int:
    return struct.unpack_from("<b", data, offset)[0]


def _float32(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]
