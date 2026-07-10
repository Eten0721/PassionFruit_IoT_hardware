"""In-memory WebRTC signaling state transitions.

Views own request parsing and HTTP responses; these helpers own the small
offer/answer/ICE state machine so it remains independent from capture state.
"""

from __future__ import annotations

from typing import Any, Callable, MutableMapping, Optional


def accept_offer(state: MutableMapping[str, Any], offer: dict[str, Any], now_string: Callable[[], str]) -> int:
    state['offer_id'] += 1
    state['offer'] = offer
    state['answer'] = None
    state['answer_id'] = 0
    state['dashboard_ice'] = []
    state['camera_ice'] = []
    state['updated_at'] = now_string()
    return state['offer_id']


def accept_answer(state: MutableMapping[str, Any], answer: dict[str, Any], now_string: Callable[[], str]) -> int:
    state['answer_id'] += 1
    state['answer'] = answer
    state['updated_at'] = now_string()
    return state['answer_id']


def add_ice_candidate(
    state: MutableMapping[str, Any],
    *,
    role: str,
    candidate: Any,
    now_string: Callable[[], str],
) -> None:
    key = 'dashboard_ice' if role == 'dashboard' else 'camera_ice'
    state[key].append(candidate)
    state['updated_at'] = now_string()


def build_state_payload(
    state: MutableMapping[str, Any],
    *,
    dashboard_from: Optional[int],
    camera_from: Optional[int],
    known_offer_id: Optional[int],
    known_answer_id: Optional[int],
) -> dict[str, Any]:
    dashboard_ice = state['dashboard_ice']
    camera_ice = state['camera_ice']
    incremental = dashboard_from is not None or camera_from is not None
    include_offer = known_offer_id is None or known_offer_id != state['offer_id']
    include_answer = known_answer_id is None or known_answer_id != state['answer_id']
    return {
        'offer': state['offer'] if include_offer else None,
        'offer_id': state['offer_id'],
        'answer': state['answer'] if include_answer else None,
        'answer_id': state['answer_id'],
        'dashboard_ice': dashboard_ice[dashboard_from or 0:] if incremental else dashboard_ice,
        'camera_ice': camera_ice[camera_from or 0:] if incremental else camera_ice,
        'dashboard_ice_total': len(dashboard_ice),
        'camera_ice_total': len(camera_ice),
        'offer_present': state['offer'] is not None,
        'answer_present': state['answer'] is not None,
        'updated_at': state['updated_at'],
    }
