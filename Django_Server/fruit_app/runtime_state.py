"""Single-process runtime state and dataset-operation coordination.

The hardware controller intentionally runs as one Django process.  This
wrapper keeps that deployment assumption explicit while providing one lock
and generation token for operations that temporarily release the lock during
slow filesystem work.
"""

from __future__ import annotations

import threading
from typing import Any


class RuntimeState(dict[str, Any]):
    def __init__(self, initial: dict[str, Any]):
        super().__init__(initial)
        self.lock = threading.RLock()
        self._operation_sequence = 0

    def begin_dataset_operation(self, kind: str, fruit_id: str | None) -> int | None:
        if self.get('dataset_operation'):
            return None
        self._operation_sequence += 1
        self['dataset_operation'] = {
            'token': self._operation_sequence,
            'kind': kind,
            'fruit_id': fruit_id,
        }
        return self._operation_sequence

    def operation_matches(self, token: int) -> bool:
        operation = self.get('dataset_operation') or {}
        return operation.get('token') == token

    def finish_dataset_operation(self, token: int) -> bool:
        if not self.operation_matches(token):
            return False
        self['dataset_operation'] = None
        return True

    def dataset_operation_payload(self) -> dict[str, Any] | None:
        operation = self.get('dataset_operation')
        if not operation:
            return None
        return {
            'kind': operation.get('kind'),
            'fruit_id': operation.get('fruit_id'),
        }
