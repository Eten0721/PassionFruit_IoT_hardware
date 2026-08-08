"""Validation and atomic persistence for capture timing profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable


class TimingValidationError(ValueError):
    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.message = message
        self.reason = reason


def normalise(
    raw_timing: Any,
    *,
    fields: Iterable[str],
    require_all: bool,
    default_step: int,
    default_minimum: int,
    default_maximum: int,
    field_limits: dict[str, tuple[int, int]] | None = None,
    field_steps: dict[str, int] | None = None,
    field_units: dict[str, str] | None = None,
) -> dict[str, int]:
    raw_timing = raw_timing if isinstance(raw_timing, dict) else {}
    timing: dict[str, int] = {}
    for field in fields:
        raw_value = raw_timing.get(field)
        if raw_value is None:
            if require_all:
                raise TimingValidationError(
                    f'缺少停穩時間欄位：{field}。',
                    'capture_timing_field_missing',
                )
            continue
        try:
            if isinstance(raw_value, float) and not raw_value.is_integer():
                raise ValueError
            value = int(raw_value)
        except (TypeError, ValueError, OverflowError):
            raise TimingValidationError(
                f'{field} 必須是整數毫秒。',
                'capture_timing_invalid_value',
            ) from None

        field_minimum = 0 if field == 'final_gate_return_delay_ms' else default_minimum
        field_maximum = default_maximum
        if field_limits and field in field_limits:
            field_minimum, field_maximum = field_limits[field]
        unit = (field_units or {}).get(field, 'ms')
        if value < field_minimum or value > field_maximum:
            raise TimingValidationError(
                f'{field} 必須介於 {field_minimum} 到 {field_maximum} {unit}。',
                'capture_timing_out_of_range',
            )
        field_step = (field_steps or {}).get(field, default_step)
        if value % field_step != 0:
            raise TimingValidationError(
                f'{field} 必須以 {field_step} {unit} 為間距。',
                'capture_timing_invalid_step',
            )
        timing[field] = value
    return timing


def read(
    path: Path,
    normalise_profile,
    *,
    migration_defaults: dict[str, int] | None = None,
    migrate_profile=None,
) -> tuple[dict[str, int | bool], int, bool] | None:
    path = Path(path)
    try:
        with path.open('r', encoding='utf-8') as timing_file:
            data = json.load(timing_file)
        raw_timing = data.get('capture_timing')
        raw_timing = dict(raw_timing) if isinstance(raw_timing, dict) else raw_timing
        migrated = False
        if isinstance(raw_timing, dict):
            if migrate_profile:
                raw_timing, migrated = migrate_profile(raw_timing)
            for field, default_value in (migration_defaults or {}).items():
                if field not in raw_timing:
                    raw_timing[field] = default_value
                    migrated = True
        timing = normalise_profile(raw_timing)
        revision = int(data.get('revision'))
        if revision < 1:
            raise ValueError('invalid timing revision')
        return timing, revision, migrated
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError, TimingValidationError):
        return None


def write(path: Path, timing: dict[str, int | bool], revision: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = path.with_suffix('.tmp')
    payload = {'revision': int(revision), 'capture_timing': dict(timing)}
    with staging_path.open('w', encoding='utf-8') as timing_file:
        json.dump(payload, timing_file, ensure_ascii=False, indent=2)
        timing_file.write('\n')
    os.replace(staging_path, path)
