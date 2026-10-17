"""Dataset storage primitives used by the capture HTTP façade.

File lifecycles and persisted marker validation live here. HTTP integration,
work-mode selection and state-machine recovery decisions remain in views.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import time
import uuid
from datetime import datetime
from functools import cache
from pathlib import Path
from time import sleep
from typing import Callable, Iterable

# Windows refuses to replace a file another handle holds open; readers finish
# in milliseconds, while Excel holds it until closed.
REPLACE_ATTEMPTS = 5
REPLACE_DELAY_SECONDS = 0.1


TEMP_DIRNAME = 'temp'
DETECTION_OUTPUT_DIRNAME = 'results'
COUNTER_FILENAME = 'counter.json'
METADATA_FILENAME = 'metadata.csv'
RESET_STATE_FILENAME = 'reset_state.json'
DISCARD_STATE_FILENAME = 'discard_state.json'
DETECTION_SUMMARY_FILENAME = 'detection_summary.csv'


FRUIT_ID_PATTERN = re.compile(r'^fruit_(\d{3,})$')
METADATA_FIELDNAMES = [
    'fruit_id',
    'label',
    'capture_time',
    'path',
    'capture_count',
    'station_01_ok',
    'station_02_ok',
    'station_03_ok',
    'note',
]
FILE_OPERATION_RETRIES = 2
FILE_OPERATION_RETRY_DELAY_SECONDS = 0.05
IMAGE_COUNT = 3
IMAGE_FILENAMES = [f'img_{idx:02d}.jpg' for idx in range(1, IMAGE_COUNT + 1)]


class DatasetFileBusyError(Exception):
    pass


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


def remove_empty_label_scaffold(root, *, labels):
    """Drop empty label folders and a header-only metadata.csv; keep content."""
    for label in labels:
        try:
            (root / label).rmdir()
        except OSError:
            pass  # Missing, non-empty or busy: leave it where it is.
    metadata = root / METADATA_FILENAME
    try:
        with metadata.open('r', encoding='utf-8-sig', newline='') as csv_file:
            has_rows = any(True for _row in csv.DictReader(csv_file))
        if not has_rows:
            metadata.unlink()
    except OSError:
        pass


def write_recovery_payload(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)


def write_sorter_recovery_intent(path, fruit_id, label, classification_code):
    write_recovery_payload(path, {
        'command_id': None,
        'fruit_id': fruit_id,
        'label': label,
        'classification_code': classification_code,
        'phase': 'dataset_committing',
        'reason': None,
    })


def read_sorter_recovery_marker(path, *, labels, classification_codes):
    try:
        with path.open('r', encoding='utf-8') as recovery_file:
            payload = json.load(recovery_file)
        raw_command_id = payload.get('command_id')
        command_id = None if raw_command_id is None else int(raw_command_id)
        fruit_id = str(payload['fruit_id'])
        label = str(payload['label'])
        classification_code = str(payload['classification_code'])
        if (
            (command_id is not None and command_id <= 0)
            or not fruit_id
            or label not in labels
            or classification_code not in classification_codes
        ):
            raise ValueError('invalid sorter recovery fields')
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        payload = {}
        command_id = None
        classification_code = None

    return payload, command_id, classification_code


def clear_recovery_marker(path):
    path.unlink(missing_ok=True)


def write_gate3_recovery_marker(path, fruit_id):
    write_recovery_payload(path, {
        'fruit_id': fruit_id,
        'reason': 'gate3_manual_removal_required',
    })


@cache
def load_counter(root):
    try:
        with (Path(root) / COUNTER_FILENAME).open('r', encoding='utf-8') as counter_file:
            data = json.load(counter_file)
        return max(int(data.get('next_id', 1)), 1)
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        write_counter(root, 1)
        return 1


def write_counter(root, next_id):
    write_recovery_payload(root / COUNTER_FILENAME, {'next_id': int(next_id)})
    load_counter.cache_clear()


def format_fruit_id(number):
    return f'fruit_{int(number):03d}'


def parse_fruit_number(fruit_id):
    match = FRUIT_ID_PATTERN.match(fruit_id or '')
    return int(match.group(1)) if match else None


def next_available_number(start_number, fruit_id_exists):
    number = max(int(start_number), 1)
    while fruit_id_exists(format_fruit_id(number)):
        number += 1
    return number


def ensure_metadata_header(root):
    path = root / METADATA_FILENAME
    if not path.exists():
        with path.open('w', encoding='utf-8-sig', newline='') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
            writer.writeheader()
        return

    with path.open('r', encoding='utf-8-sig', newline='') as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames == METADATA_FIELDNAMES:
            return
        rows = list(reader)

    upgraded_rows = []
    for row in rows:
        upgraded_rows.append({
            'fruit_id': row.get('fruit_id', ''),
            'label': row.get('label', ''),
            'capture_time': row.get('capture_time', ''),
            'path': row.get('path', ''),
            'capture_count': row.get('capture_count') or '',
            'station_01_ok': row.get('station_01_ok') or '',
            'station_02_ok': row.get('station_02_ok') or '',
            'station_03_ok': row.get('station_03_ok') or '',
            'note': row.get('note', ''),
        })

    with path.open('w', encoding='utf-8-sig', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
        writer.writeheader()
        writer.writerows(upgraded_rows)


def append_metadata(root, fruit_id, label, capture_time, relative_path, note, fruit_dir):
    ensure_metadata_header(root)
    station_ok = [
        str((Path(fruit_dir) / filename).exists()).lower()
        for filename in IMAGE_FILENAMES
    ]
    with (root / METADATA_FILENAME).open('a', encoding='utf-8-sig', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
        writer.writerow({
            'fruit_id': fruit_id,
            'label': label,
            'capture_time': capture_time,
            'path': relative_path,
            'capture_count': temp_image_count(fruit_dir),
            'station_01_ok': station_ok[0],
            'station_02_ok': station_ok[1],
            'station_03_ok': station_ok[2],
            'note': note,
        })


def temp_image_count(fruit_dir):
    return sum(1 for filename in IMAGE_FILENAMES if (fruit_dir / filename).exists())


def is_reusable_temp_dir(fruit_dir):
    fruit_dir = Path(fruit_dir)
    if not fruit_dir.exists() or not fruit_dir.is_dir():
        return True
    if temp_image_count(fruit_dir) > 0:
        return False
    return not any(fruit_dir.iterdir())


def safe_rmtree(path):
    path = Path(path)
    if not path.exists():
        return
    last_error = None
    for _ in range(FILE_OPERATION_RETRIES):
        try:
            shutil.rmtree(path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(FILE_OPERATION_RETRY_DELAY_SECONDS)
    raise DatasetFileBusyError(f'無法刪除 {path}，可能正被瀏覽器預覽、檔案總管或其他程式使用。請關閉相關視窗後再試。') from last_error


def discard_temp_fruit(root, fruit_dir, fruit_id, *, now_string, fruit_id_exists):
    fruit_dir = Path(fruit_dir)
    if not fruit_dir.exists():
        return {'mode': 'deleted', 'cleanup_path': None, 'next_fruit_id': None}

    try:
        safe_rmtree(fruit_dir)
        if not fruit_dir.exists():
            return {'mode': 'deleted', 'cleanup_path': None, 'next_fruit_id': None}
        raise DatasetFileBusyError(f'刪除 {fruit_dir} 後資料夾仍存在。')
    except DatasetFileBusyError as delete_error:
        target = unique_delete_pending_path(
            root, fruit_dir,
            reason=datetime.now().strftime('discard_%Y%m%d_%H%M%S'),
        )
        try:
            safe_move(fruit_dir, target)
        except DatasetFileBusyError:
            record_discard_cleanup(root, fruit_dir, fruit_id=fruit_id, blocked_in_temp=True, now_string=now_string)
            next_fruit_id = advance_counter_after_deferred_discard(root, fruit_id, fruit_id_exists=fruit_id_exists)
            return {
                'mode': 'deferred_cleanup',
                'cleanup_path': dataset_relative_path(root, fruit_dir),
                'next_fruit_id': next_fruit_id,
                'warning': str(delete_error),
            }

        if fruit_dir.exists():
            record_discard_cleanup(root, fruit_dir, fruit_id=fruit_id, blocked_in_temp=True, now_string=now_string)
            next_fruit_id = advance_counter_after_deferred_discard(root, fruit_id, fruit_id_exists=fruit_id_exists)
            return {
                'mode': 'deferred_cleanup',
                'cleanup_path': dataset_relative_path(root, fruit_dir),
                'next_fruit_id': next_fruit_id,
                'warning': str(delete_error),
            }

        record_discard_cleanup(
            root, fruit_dir,
            fruit_id=fruit_id,
            cleanup_path=target,
            blocked_in_temp=False,
            now_string=now_string,
        )
        return {
            'mode': 'quarantined',
            'cleanup_path': dataset_relative_path(root, target),
            'next_fruit_id': None,
            'warning': str(delete_error),
        }


def advance_counter_after_deferred_discard(root, fruit_id, *, fruit_id_exists):
    fruit_number = parse_fruit_number(fruit_id) or load_counter(root)
    next_number = next_available_number(max(load_counter(root), fruit_number + 1), fruit_id_exists)
    write_counter(root, next_number)
    return format_fruit_id(next_number)


def read_discard_state(root):
    try:
        with (root / DISCARD_STATE_FILENAME).open('r', encoding='utf-8') as state_file:
            data = json.load(state_file)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {'pending_paths': []}

    pending_paths = data.get('pending_paths') if isinstance(data, dict) else None
    if not isinstance(pending_paths, list):
        pending_paths = []
    return {
        'pending_paths': [
            entry for entry in pending_paths
            if (
                isinstance(entry, dict)
                and is_safe_dataset_relative_path(entry.get('source_path'))
                and is_safe_dataset_relative_path(entry.get('cleanup_path'))
            )
        ],
    }


def write_discard_state(root, data):
    (root / DISCARD_STATE_FILENAME).parent.mkdir(parents=True, exist_ok=True)
    with (root / DISCARD_STATE_FILENAME).open('w', encoding='utf-8') as state_file:
        json.dump(data, state_file, ensure_ascii=False, indent=2)
        state_file.write('\n')


def record_discard_cleanup(root, source_path, *, fruit_id, cleanup_path=None, blocked_in_temp, now_string):
    source_relative_path = dataset_relative_path(root, source_path)
    cleanup_relative_path = dataset_relative_path(root, cleanup_path or source_path)
    state = read_discard_state(root)
    entries = [
        entry for entry in state['pending_paths']
        if entry.get('source_path') != source_relative_path
    ]
    entries.append({
        'fruit_id': fruit_id,
        'source_path': source_relative_path,
        'cleanup_path': cleanup_relative_path,
        'blocked_in_temp': bool(blocked_in_temp),
        'recorded_at': now_string(),
    })
    write_discard_state(root, {'pending_paths': entries})


def is_safe_dataset_relative_path(value):
    if not isinstance(value, str) or not value.strip():
        return False
    path = Path(value)
    return not path.is_absolute() and '..' not in path.parts


def is_deferred_discard_path(root, path):
    relative_path = dataset_relative_path(root, path)
    return any(
        entry.get('blocked_in_temp') and entry.get('source_path') == relative_path
        for entry in read_discard_state(root)['pending_paths']
    )


def safe_unlink(path):
    path = Path(path)
    if not path.exists():
        return
    last_error = None
    for _ in range(FILE_OPERATION_RETRIES):
        try:
            path.unlink()
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(FILE_OPERATION_RETRY_DELAY_SECONDS)
    raise DatasetFileBusyError(f'無法刪除 {path}，可能正被其他程式使用。請關閉相關視窗後再試。') from last_error


def safe_move(src, dest):
    src = Path(src)
    dest = Path(dest)
    last_error = None
    for _ in range(FILE_OPERATION_RETRIES):
        try:
            shutil.move(str(src), str(dest))
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(FILE_OPERATION_RETRY_DELAY_SECONDS)
    raise DatasetFileBusyError(f'無法移動 {src} 到 {dest}，可能正被其他程式使用。請關閉預覽後再試。') from last_error


def safe_replace(src, dest):
    src = Path(src)
    dest = Path(dest)
    last_error = None
    for _ in range(FILE_OPERATION_RETRIES):
        try:
            src.replace(dest)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(FILE_OPERATION_RETRY_DELAY_SECONDS)
    raise DatasetFileBusyError(f'無法替換 {dest}，可能正被其他程式使用。請關閉預覽後再試。') from last_error


def clear_temp_images(fruit_dir, *, clear_image_cache):
    fruit_dir = Path(fruit_dir)
    try:
        for image_file in fruit_dir.glob('img_*.jpg'):
            safe_unlink(image_file)
    finally:
        if FRUIT_ID_PATTERN.match(fruit_dir.name):
            clear_image_cache()


def reset_dataset_contents(root, *, collects_by_label, labels, now_string, reset_summary, clear_image_cache):
    delete_warnings = []
    moved_paths = []
    ignored_paths = []
    reset_id = datetime.now().strftime('reset_%Y%m%d_%H%M%S')
    parents = [root / TEMP_DIRNAME]
    if collects_by_label:
        parents.extend(root / label for label in labels)
    else:
        parents.append(root / DETECTION_OUTPUT_DIRNAME)
    for parent in parents:
        parent.mkdir(parents=True, exist_ok=True)
        for child in parent.iterdir():
            result = remove_or_archive_reset_path(root, child, reset_id)
            if result['warning']:
                delete_warnings.append(result['warning'])
            if result['moved_to']:
                moved_paths.append({
                    'from': result['relative_path'],
                    'to': result['moved_to'],
                })
            if result['ignored']:
                ignored_paths.append(result['relative_path'])
    if collects_by_label:
        with (root / METADATA_FILENAME).open('w', encoding='utf-8-sig', newline='') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
            writer.writeheader()
    else:
        reset_summary(root / DETECTION_SUMMARY_FILENAME)
    clear_image_cache()
    write_counter(root, 1)
    write_reset_state(root, {
        'reset_at': now_string(),
        'ignored_paths': ignored_paths,
        'moved_paths': moved_paths,
    })
    return delete_warnings


def remove_or_archive_reset_path(root, path, reset_id):
    path = Path(path)
    relative_path = dataset_relative_path(root, path)
    result = {
        'relative_path': relative_path,
        'warning': None,
        'moved_to': None,
        'ignored': False,
    }

    try:
        if path.is_dir():
            safe_rmtree(path)
        else:
            safe_unlink(path)
        return result
    except DatasetFileBusyError as delete_error:
        moved_warning = move_reset_leftover_to_pending(root, path, reason=reset_id)
        if not path.exists():
            result['warning'] = f'{relative_path} 刪除失敗，已移到 {moved_warning}。'
            result['moved_to'] = moved_warning
            return result

        result['warning'] = f'{delete_error}；搬移到 _delete_pending 也失敗，已在 reset_state.json 標記為忽略。'
        result['ignored'] = True
        return result


def move_reset_leftover_to_pending(root, path, reason):
    path = Path(path)
    if not path.exists():
        return ''

    target = unique_delete_pending_path(root, path, reason)
    try:
        safe_move(path, target)
    except DatasetFileBusyError as exc:
        return str(exc)
    return dataset_relative_path(root, target)


def unique_delete_pending_path(root, path, reason):
    relative_path = dataset_relative_path(root, path)
    target = root / '_delete_pending' / reason / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        return target

    suffix = 1
    while True:
        candidate = target.with_name(f'{target.name}_{suffix}')
        if not candidate.exists():
            return candidate
        suffix += 1


def dataset_relative_path(root, path):
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return Path(path).as_posix()


def read_reset_state(root):
    try:
        with (root / RESET_STATE_FILENAME).open('r', encoding='utf-8') as reset_file:
            data = json.load(reset_file)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {'reset_at': None, 'ignored_paths': [], 'moved_paths': []}

    ignored_paths = data.get('ignored_paths')
    moved_paths = data.get('moved_paths')
    return {
        'reset_at': data.get('reset_at'),
        'ignored_paths': ignored_paths if isinstance(ignored_paths, list) else [],
        'moved_paths': moved_paths if isinstance(moved_paths, list) else [],
    }


def write_reset_state(root, data):
    (root / RESET_STATE_FILENAME).parent.mkdir(parents=True, exist_ok=True)
    with (root / RESET_STATE_FILENAME).open('w', encoding='utf-8') as reset_file:
        json.dump(data, reset_file, ensure_ascii=False, indent=2)
        reset_file.write('\n')


def is_ignored_reset_path(root, path):
    relative_path = dataset_relative_path(root, path)
    return relative_path in set(read_reset_state(root).get('ignored_paths', []))


def remove_ignored_reset_path(root, path):
    relative_path = dataset_relative_path(root, path)
    state = read_reset_state(root)
    state['ignored_paths'] = [
        ignored_path
        for ignored_path in state.get('ignored_paths', [])
        if ignored_path != relative_path
    ]
    write_reset_state(root, state)
