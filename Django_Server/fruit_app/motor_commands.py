"""Persistent command-id allocation for the single ESP32 command slot."""

from __future__ import annotations

import json
import os
from pathlib import Path


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
    staging_path = path.with_name(f'{path.name}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with staging_path.open('w', encoding='utf-8') as sequence_file:
            json.dump({'last_issued_id': int(command_id)}, sequence_file, ensure_ascii=False, indent=2)
            sequence_file.write('\n')
        os.replace(staging_path, path)
    except OSError as exc:
        try:
            staging_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise CommandSequenceError(f'無法保存 motor command ID 設定：{exc}') from exc
