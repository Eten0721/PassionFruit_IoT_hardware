"""Dataset storage primitives used by the capture HTTP façade.

Only the storage primitive lives here: validation and state-machine decisions
remain in the capture service, so a failed atomic write can safely re-open the
same camera request without advancing a gate.
"""

from __future__ import annotations

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
