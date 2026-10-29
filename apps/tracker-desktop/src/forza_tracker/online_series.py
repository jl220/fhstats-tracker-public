"""Resolve native online series IDs using English game assets, without OCR."""
from dataclasses import asdict, dataclass
import struct

from .online_track_catalog import TRACK_ROWS


MODE_DISCIPLINES = {
    'LapsRace': 'road', 'P2P': 'road', 'Scramble': 'dirt', 'TrailRace': 'dirt',
    'CrossCountryCircuit': 'cross-country', 'CrossCountry': 'cross-country',
    'StreetRace': 'street', 'Drag': 'drag', 'Touge': 'touge', 'Rush': 'rush',
    'Showcase': 'showcase', 'TeamInfected': 'playground-games',
    'TeamKing': 'playground-games', 'TeamFlagRush': 'playground-games',
}


@dataclass(frozen=True)
class TrackInfo:
    track_id: int
    name: str
    route_id: int
    name_key: str
    race_mode: str | None
    discipline: str | None


_TRACKS = {row[0]: TrackInfo(*row, MODE_DISCIPLINES.get(row[4])) for row in TRACK_ROWS}


def resolve_track(track_id):
    if type(track_id) is not int or track_id < 0:
        return None
    return _TRACKS.get(track_id)


def resolve_series(track_ids):
    tracks = []
    for track_id in track_ids:
        track = resolve_track(track_id)
        tracks.append(asdict(track) if track else dict(track_id=track_id, name=None,
                      route_id=None, name_key=None, race_mode=None, discipline=None))
    disciplines = {t['discipline'] for t in tracks}
    discipline = next(iter(disciplines)) if len(disciplines) == 1 else None
    return dict(tracks=tracks, track_names=[t['name'] for t in tracks],
                discipline=discipline,
                discipline_source='game_catalog_from_memory_ids' if discipline else None)


def read_series_state(read, address, count_vtable, tracks_vtable):
    """Constructor-derived fields. A coherent observation is not an active-race binding."""
    result = dict(address=hex(address), series_identity_verified=False, class_name=None)
    try:
        raw = read(address, 0x200)
        tables = tuple(struct.unpack_from('<Q', raw, off)[0] for off in (0x20, 0xd0, 0x110))
        if tables != (count_vtable, count_vtable, tracks_vtable):
            return {**result, 'status': 'identity_changed'}
        if read(address, 0x150) != raw[:0x150]:
            return {**result, 'status': 'changed_during_read'}
        count, completed = raw[0x38], raw[0xe8]
        ids = list(struct.unpack_from('<10I', raw, 0x128))
        result.update(event_count=count, completed_event_count=completed,
                      all_track_slots=ids, track_ids=ids[:min(count, 10)], raw=raw.hex())
        if not 0 <= completed <= count <= 10:
            result['status'] = 'invalid_counts'
        elif not count:
            result['status'] = 'empty'
        elif any(v == 0 for v in ids[:count]):
            result['status'] = 'unresolved_ids'
        else:
            result.update(status='candidate', **resolve_series(ids[:count]))
    except (OSError, OverflowError, ValueError, struct.error):
        result['status'] = 'unreadable'
    return result
