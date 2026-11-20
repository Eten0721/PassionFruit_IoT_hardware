"""Durable event log and a one-click ZIP of everything needed to explain a stuck system.

The ZIP never contains photos or credentials: runtime files are copied from an allowlist,
and Dataset temp folders contribute only their hidden state markers and file names.
"""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EVENT_LOG_NAME = 'diagnostic_events.jsonl'
EVENT_LOG_MAX_BYTES = 1_000_000
RUNTIME_STATE_FILES = (
    'motor_command_sequence.json', 'network_recovery.json', 'sorter_recovery.json',
    'gate3_recovery.json', 'dataset_options.json', 'hardware_sync.json',
)
TEMP_MARKERS = ('.capture-session.json', '.detection-terminal')


def append_event(runtime_root: Path, entry: dict) -> None:
    """Append one JSON line; past the size cap the older half is dropped. Never raises."""
    path = Path(runtime_root) / EVENT_LOG_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > EVENT_LOG_MAX_BYTES:
            lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
            path.write_text(''.join(lines[len(lines) // 2:]), encoding='utf-8')
        with path.open('a', encoding='utf-8') as log:
            log.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
    except OSError:
        logger.warning('Diagnostic event log write failed: %s', path, exc_info=True)


_last_rejection = None


def runtime_root() -> Path:
    from django.conf import settings
    return Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).parent


def record_rejection(status: int, reason, message) -> None:
    """Log a refused request once per run of identical refusals, so polling cannot flood the log."""
    global _last_rejection
    key = (status, reason)
    if key == _last_rejection:
        return
    _last_rejection = key
    append_event(runtime_root(), {'event': 'request_rejected', 'at': datetime.now().isoformat(timespec='seconds'),
                                  'status': status, 'reason': reason, 'message': message})


def write_export(stream, *, state: dict, runtime_root: Path, dataset_root: Path) -> str:
    """Write the diagnostics ZIP into ``stream`` and return its download file name."""
    runtime_root, dataset_root = Path(runtime_root), Path(dataset_root)
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('state.json', json.dumps(state, ensure_ascii=False, indent=2, default=str))
        for name in (*RUNTIME_STATE_FILES, EVENT_LOG_NAME):
            path = runtime_root / name
            if path.is_file():
                archive.write(path, f'runtime_config/{name}')
        listing = {}
        for fruit_dir in sorted(dataset_root.glob('*/temp/*')):
            if not fruit_dir.is_dir():
                continue
            relative = fruit_dir.relative_to(dataset_root).as_posix()
            listing[relative] = sorted(child.name for child in fruit_dir.iterdir())
            for marker in TEMP_MARKERS:
                if (fruit_dir / marker).is_file():
                    archive.write(fruit_dir / marker, f'dataset/{relative}/{marker}')
        archive.writestr('dataset/temp_listing.json', json.dumps(listing, ensure_ascii=False, indent=2))
    return f"diagnostics_{datetime.now():%Y%m%d_%H%M%S}.zip"
