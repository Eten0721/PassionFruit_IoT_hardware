"""Validation and loading of the maintainer-edited hardware parameter file."""

from __future__ import annotations

import json
import tomllib
import zlib
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


def load(path: Path, fields: Iterable[str]) -> tuple[dict[str, Any], int]:
    """Read the maintainer-edited TOML file; the revision is derived from its values."""
    path = Path(path)
    try:
        with path.open('rb') as params_file:
            raw = tomllib.load(params_file)
    except FileNotFoundError:
        raise TimingValidationError(
            f'找不到硬體參數檔：{path}。', 'hardware_params_missing',
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise TimingValidationError(
            f'硬體參數檔格式錯誤：{exc}。', 'hardware_params_invalid_toml',
        ) from None
    unknown = sorted(set(raw) - set(fields))
    if unknown:
        raise TimingValidationError(
            f'硬體參數檔有未知欄位：{"、".join(unknown)}。', 'hardware_params_unknown_field',
        )
    canonical = json.dumps({field: raw.get(field) for field in fields}, sort_keys=True)
    # Same values keep the same revision across restarts, so ESP32 only re-applies real changes.
    revision = zlib.crc32(canonical.encode('utf-8')) % 999_999 + 1
    return raw, revision
