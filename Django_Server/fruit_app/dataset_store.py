"""Dataset storage primitives used by the capture HTTP façade.

Only the storage primitive lives here: validation and state-machine decisions
remain in the capture service, so a failed atomic write can safely re-open the
same camera request without advancing a gate.
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import Callable, Iterable


def save_station_image(
    fruit_dir: Path,
    station_index: int,
    image_file,
    *,
    image_filenames: Iterable[str],
    staging_suffix: str,
    safe_unlink: Callable[[Path], None],
    safe_replace: Callable[[Path, Path], None],
) -> None:
    """Atomically save exactly one station image through a staging filename."""
    filenames = list(image_filenames)
    fruit_dir = Path(fruit_dir)
    filename = filenames[station_index - 1]
    target = fruit_dir / filename
    staging = fruit_dir / f'{filename}{staging_suffix}'
    try:
        safe_unlink(staging)
        with staging.open('wb') as output:
            for chunk in image_file.chunks():
                output.write(chunk)
        safe_replace(staging, target)
    except Exception:
        safe_unlink(staging)
        raise


def save_capture_session(
    fruit_dir: Path,
    capture_time: str,
    work_mode: str = 'collection',
    hardware_mode: str = 'photo_only',
) -> None:
    """Persist the selected modes before exposing a camera request."""
    target = fruit_dir / '.capture-session.json'
    staging = target.with_suffix('.json.tmp')
    try:
        staging.write_text(json.dumps({
            'hardware_mode': hardware_mode,
            'work_mode': work_mode,
            'capture_time': capture_time,
        }), encoding='utf-8')
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)


def read_capture_options(fruit_dir: Path) -> dict:
    """Legacy fruit folders require hardware; only explicit markers skip it."""
    try:
        payload = json.loads((fruit_dir / '.capture-session.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'hardware_mode': 'hardware', 'work_mode': 'collection'}
    if (
        not isinstance(payload, dict)
        or payload.get('hardware_mode') not in ('hardware', 'photo_only')
    ):
        raise ValueError('Invalid capture session hardware mode')
    if payload.get('work_mode', 'collection') not in ('collection', 'detection'):
        raise ValueError('Invalid capture session work mode')
    payload.setdefault('work_mode', 'collection')
    return payload
