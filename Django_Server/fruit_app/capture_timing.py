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
    step_ms: int,
    minimum_ms: int,
    maximum_ms: int,
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
            value = int(raw_value)
        except (TypeError, ValueError):
            raise TimingValidationError(
                f'{field} 必須是整數毫秒。',
                'capture_timing_invalid_value',
            ) from None

        field_minimum = 0 if field == 'final_gate_return_delay_ms' else minimum_ms
        if value < field_minimum or value > maximum_ms:
            raise TimingValidationError(
                f'{field} 必須介於 {field_minimum} 到 {maximum_ms} ms。',
                'capture_timing_out_of_range',
            )
        if value % step_ms != 0:
            raise TimingValidationError(
                f'{field} 必須以 {step_ms} ms 為間距。',
                'capture_timing_invalid_step',
            )
        timing[field] = value
    return timing


def read(path: Path, normalise_profile) -> tuple[dict[str, int], int] | None:
    path = Path(path)
    try:
        with path.open('r', encoding='utf-8') as timing_file:
            data = json.load(timing_file)
        timing = normalise_profile(data.get('capture_timing'))
        revision = int(data.get('revision'))
        if revision < 1:
            raise ValueError('invalid timing revision')
        return timing, revision
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError, TimingValidationError):
        return None


def write(path: Path, timing: dict[str, int], revision: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = path.with_suffix('.tmp')
    payload = {'revision': int(revision), 'capture_timing': dict(timing)}
    with staging_path.open('w', encoding='utf-8') as timing_file:
        json.dump(payload, timing_file, ensure_ascii=False, indent=2)
        timing_file.write('\n')
    os.replace(staging_path, path)
