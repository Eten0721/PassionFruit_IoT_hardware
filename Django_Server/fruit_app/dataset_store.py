"""Dataset storage primitives used by the capture HTTP façade.

Only the storage primitive lives here: validation and state-machine decisions
remain in the capture service, so a failed atomic write can safely re-open the
same camera request without advancing a gate.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterable


class DatasetRuntimeCache:
    """Small single-process cache for immutable bootstrap work and hot reads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialised_roots: set[str] = set()
        self._counter_values: dict[str, int] = {}
        self._image_manifests: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._maintenance_last: dict[str, float] = {}

    @staticmethod
    def root_key(root: Path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(root)))

    def ensure_once(self, root: Path, initialise: Callable[[], None]) -> bool:
        key = self.root_key(root)
        with self._lock:
            if key in self._initialised_roots:
                return False
            initialise()
            self._initialised_roots.add(key)
            return True

    def read_counter(self, root: Path, loader: Callable[[], int]) -> int:
        key = self.root_key(root)
        with self._lock:
            if key not in self._counter_values:
                self._counter_values[key] = int(loader())
            return self._counter_values[key]

    def write_counter(self, root: Path, value: int) -> None:
        with self._lock:
            self._counter_values[self.root_key(root)] = int(value)

    def image_manifest(
        self,
        root: Path,
        fruit_id: str,
        loader: Callable[[], list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        key = (self.root_key(root), fruit_id)
        with self._lock:
            if key not in self._image_manifests:
                self._image_manifests[key] = loader()
            return [dict(item) for item in self._image_manifests[key]]

    def invalidate_images(self, root: Path, fruit_id: str | None = None) -> None:
        root_key = self.root_key(root)
        with self._lock:
            if fruit_id is not None:
                self._image_manifests.pop((root_key, fruit_id), None)
                return
            self._image_manifests = {
                key: value
                for key, value in self._image_manifests.items()
                if key[0] != root_key
            }

    def claim_maintenance(self, root: Path, now: float, interval_seconds: float) -> bool:
        key = self.root_key(root)
        with self._lock:
            last_attempt = self._maintenance_last.get(key)
            if last_attempt is not None and now - last_attempt < interval_seconds:
                return False
            self._maintenance_last[key] = now
            return True

    def reset(self, root: Path | None = None) -> None:
        with self._lock:
            if root is None:
                self._initialised_roots.clear()
                self._counter_values.clear()
                self._image_manifests.clear()
                self._maintenance_last.clear()
                return
            root_key = self.root_key(root)
            self._initialised_roots.discard(root_key)
            self._counter_values.pop(root_key, None)
            self._maintenance_last.pop(root_key, None)
            self.invalidate_images(root)


RUNTIME_CACHE = DatasetRuntimeCache()


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
