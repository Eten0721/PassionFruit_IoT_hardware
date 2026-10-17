"""Dataset storage primitives used by the capture HTTP façade.

Only the storage primitive lives here: validation and state-machine decisions
remain in the capture service, so a failed atomic write can safely re-open the
same camera request without advancing a gate.
"""

from __future__ import annotations

import json
import uuid

from pathlib import Path
from time import sleep
from typing import Callable, Iterable

# Windows refuses to replace a file another handle holds open; readers finish
# in milliseconds, while Excel holds it until closed.
REPLACE_ATTEMPTS = 5
REPLACE_DELAY_SECONDS = 0.1


def atomic_write(path: Path, write: Callable[[Path], object]) -> None:
    """Fill ``<name>.tmp`` with ``write`` and swap it in; never leave the staging file.

    A target still locked after ``REPLACE_ATTEMPTS`` raises ``PermissionError``
    naming the file so the operator knows what to close.
    """
    path = Path(path)
    staging = path.with_name(f'{path.name}.tmp')
    try:
        write(staging)
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                staging.replace(path)
                return
            except PermissionError as error:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise PermissionError(
                        f'{path.name} 正被其他程式開啟（例如 Excel），請關閉後再試。'
                    ) from error
                sleep(REPLACE_DELAY_SECONDS)
    finally:
        staging.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write(path, lambda staging: staging.write_text(text, encoding='utf-8'))


def atomic_write_json(path: Path, payload, *, indent: int | None = 2) -> None:
    """Write UTF-8 JSON (non-ASCII kept) with a trailing newline when indented."""
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    atomic_write_text(path, text + '\n' if indent is not None else text)


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
    """Persist the selected modes before exposing a camera request.

    ``result_id`` stays stable across restarts and never depends on the
    resettable fruit counter.
    """
    atomic_write_text(fruit_dir / '.capture-session.json', json.dumps({
        'hardware_mode': hardware_mode,
        'work_mode': work_mode,
        'capture_time': capture_time,
        'result_id': uuid.uuid4().hex,
    }))


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
