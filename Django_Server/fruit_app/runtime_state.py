"""Single-process runtime state and dataset-operation coordination.

The hardware controller intentionally runs as one Django process.  This
wrapper keeps that deployment assumption explicit while providing one lock
and generation token for operations that temporarily release the lock during
slow filesystem work.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, MutableMapping
from typing import Any


class RuntimeState(MutableMapping[str, Any]):
    def __init__(self, initial: dict[str, Any]):
        self._data = dict(initial)
        self.lock = threading.RLock()
        self._operation_sequence = 0

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def replace(self, values: dict[str, Any]) -> None:
        self._data.clear()
        self._data.update(values)

    def begin_dataset_operation(self, kind: str, fruit_id: str | None) -> int | None:
        if self._data.get('dataset_operation'):
            return None
        self._operation_sequence += 1
        self._data['dataset_operation'] = {
            'token': self._operation_sequence,
            'kind': kind,
            'fruit_id': fruit_id,
        }
        return self._operation_sequence

    def operation_matches(self, token: int) -> bool:
        operation = self._data.get('dataset_operation') or {}
        return operation.get('token') == token

    def finish_dataset_operation(self, token: int) -> bool:
        if not self.operation_matches(token):
            return False
        self._data['dataset_operation'] = None
        return True

    def dataset_operation_payload(self) -> dict[str, Any] | None:
        operation = self._data.get('dataset_operation')
        if not operation:
            return None
        return {
            'kind': operation.get('kind'),
            'fruit_id': operation.get('fruit_id'),
        }
