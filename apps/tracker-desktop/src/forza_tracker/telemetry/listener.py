from __future__ import annotations

import json
import logging
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

from forza_tracker.telemetry.forza_udp import ForzaHorizonTelemetry, parse_forza_horizon_packet
from forza_tracker.telemetry.status import STATUS_PATH, write_telemetry_status

log = logging.getLogger(__name__)


def handle_telemetry_packet(
    data: bytes,
    address: tuple[str, int],
    *,
    status_path: Path = STATUS_PATH,
) -> ForzaHorizonTelemetry | None:
    remote = f"{address[0]}:{address[1]}"
    try:
        packet = parse_forza_horizon_packet(data)
    except ValueError as error:
        write_telemetry_status(
            "invalid_packet",
            message=str(error),
            remote=remote,
            path=status_path,
        )
        return None

    write_telemetry_status(
        "connected",
        packet=packet,
        message="FH6 telemetry connected",
        remote=remote,
        path=status_path,
    )
    return packet


def listen_forza_horizon_6_udp(
    *,
    host: str = "0.0.0.0",
    port: int = 9999,
    duration_seconds: int = 0,
    output_path: Path | None = None,
    status_path: Path = STATUS_PATH,
    summary_every_seconds: float = 2.0,
    flush_every: int = 30,
    emit_summary: bool = True,
) -> None:
    deadline = time.monotonic() + duration_seconds if duration_seconds > 0 else None
    packet_count = 0
    parsed_count = 0
    invalid_count = 0
    last_summary = 0.0
    last_packet: ForzaHorizonTelemetry | None = None
    last_write_warning = float('-inf')

    write_telemetry_status("listening", message=f"Listening on {host}:{port}", path=status_path)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, port))
        sock.settimeout(1.0)
        output_file = output_path.open("a", encoding="utf-8") if output_path is not None else None
        try:
            while deadline is None or time.monotonic() < deadline:
                try:
                    data, address = sock.recvfrom(2048)
                except socket.timeout:
                    if emit_summary:
                        _print_summary(packet_count, parsed_count, invalid_count, last_packet)
                    continue

                # Live consumers need the newest frame. Disk writes (including
                # Windows replacement retries) must not leave us replaying a
                # socket backlog and timestamping old game data as fresh.
                # Explicit JSONL recording retains every packet as before.
                if output_file is None:
                    sock.setblocking(False)
                    try:
                        for _ in range(255):
                            try:
                                data, address = sock.recvfrom(2048)
                            except BlockingIOError:
                                break
                    finally:
                        sock.settimeout(1.0)

                packet_count += 1
                try:
                    packet = handle_telemetry_packet(data, address, status_path=status_path)
                except OSError as error:
                    # A locked status destination must not kill the UDP
                    # receiver. Discard this publication and try the next
                    # newest frame, with no stale cached data substitution.
                    now = time.monotonic()
                    if now - last_write_warning >= 5:
                        log.warning('Telemetry status write failed; receiver continues: %s', error)
                        last_write_warning = now
                    continue
                if packet is None:
                    invalid_count += 1
                    continue

                parsed_count += 1
                last_packet = packet
                if output_file is not None:
                    row = {
                        "received_at": datetime.now(timezone.utc).isoformat(),
                        "remote": f"{address[0]}:{address[1]}",
                        **packet.to_json_dict(),
                    }
                    output_file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                    if parsed_count % flush_every == 0:
                        output_file.flush()

                now = time.monotonic()
                if now - last_summary >= summary_every_seconds:
                    if emit_summary:
                        _print_summary(packet_count, parsed_count, invalid_count, last_packet)
                    last_summary = now
        finally:
            if output_file is not None:
                output_file.close()


def _print_summary(
    packet_count: int,
    parsed_count: int,
    invalid_count: int,
    packet: ForzaHorizonTelemetry | None,
) -> None:
    if packet is None:
        print(f"packets={packet_count} parsed={parsed_count} invalid={invalid_count} waiting...", flush=True)
        return
    print(
        "packets={packets} parsed={parsed} invalid={invalid} race={race} "
        "pos={pos} lap={lap} race_time={race_time:.1f}s speed={speed:.1f}km/h class={car_class}{pi}".format(
            packets=packet_count,
            parsed=parsed_count,
            invalid=invalid_count,
            race=1 if packet.is_race_on else 0,
            pos=packet.race_position,
            lap=packet.lap_number,
            race_time=packet.current_race_time_seconds,
            speed=packet.speed_kmh,
            car_class=packet.car_class,
            pi=packet.performance_index,
        ),
        flush=True,
    )
