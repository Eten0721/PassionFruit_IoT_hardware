"""Persistent command-id allocation for the single ESP32 command slot."""

from __future__ import annotations

import json
from pathlib import Path

from . import dataset_store


class CommandSequenceError(Exception):
    pass


def allocate(path: Path, in_memory_last_id: int) -> int:
    """Atomically reserve and persist the next positive command id."""
    path = Path(path)
    persisted_last_id = _read_last_issued_id(path)
    next_id = max(int(in_memory_last_id or 0), persisted_last_id) + 1
    _write_last_issued_id(path, next_id)
    return next_id


def _read_last_issued_id(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open('r', encoding='utf-8') as sequence_file:
            payload = json.load(sequence_file)
        value = int(payload['last_issued_id'])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise CommandSequenceError(f'無法讀取 motor command ID 設定：{exc}') from exc
    if value < 0:
        raise CommandSequenceError('motor command ID 設定不得小於 0。')
    return value


def _write_last_issued_id(path: Path, command_id: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        dataset_store.atomic_write_json(path, {'last_issued_id': int(command_id)})
    except OSError as exc:
        raise CommandSequenceError(f'無法保存 motor command ID 設定：{exc}') from exc
