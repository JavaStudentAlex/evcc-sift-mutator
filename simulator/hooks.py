"""Hooks Framework for Intercepting and Mutating EVCC / SECC Communication.

Directly matches the EnBW challenge integration requirements (`hooks.py`).
Provides pre_send and post_receive interception points for protocol fuzzing,
payload mutation, state machine perturbation, and telemetry extraction.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional
from protocols.iso15118_messages import V2GMessage

logger = logging.getLogger("evcc_hooks")

# Hook Type Signatures
PreSendHook = Callable[[V2GMessage, str], V2GMessage]
PostReceiveHook = Callable[[V2GMessage, str], None]
StateTransitionHook = Callable[[str, str], Optional[str]]


class HookRegistry:
    """Central registry for active mutation and interception hooks."""

    def __init__(self):
        self._pre_send_hooks: List[PreSendHook] = []
        self._post_receive_hooks: List[PostReceiveHook] = []
        self._state_transition_hooks: List[StateTransitionHook] = []

    def register_pre_send(self, hook: PreSendHook) -> None:
        """Registers a hook called right before a message is serialized and sent to SECC."""
        self._pre_send_hooks.append(hook)

    def register_post_receive(self, hook: PostReceiveHook) -> None:
        """Registers a hook called right after a response is received from SECC."""
        self._post_receive_hooks.append(hook)

    def register_state_transition(self, hook: StateTransitionHook) -> None:
        """Registers a hook that can override or skip state machine transitions."""
        self._state_transition_hooks.append(hook)

    def clear(self) -> None:
        """Clears all registered hooks."""
        self._pre_send_hooks.clear()
        self._post_receive_hooks.clear()
        self._state_transition_hooks.clear()

    def run_pre_send(self, message: V2GMessage, state_name: str) -> V2GMessage:
        """Executes all pre_send hooks sequentially, passing mutated message forward."""
        current_msg = message
        for hook in self._pre_send_hooks:
            try:
                current_msg = hook(current_msg, state_name)
            except Exception as e:
                logger.error(f"Error executing pre_send hook {hook.__name__}: {e}", exc_info=True)
        return current_msg

    def run_post_receive(self, response: V2GMessage, state_name: str) -> None:
        """Executes all post_receive hooks."""
        for hook in self._post_receive_hooks:
            try:
                hook(response, state_name)
            except Exception as e:
                logger.error(f"Error executing post_receive hook {hook.__name__}: {e}", exc_info=True)

    def run_state_transition(self, from_state: str, to_state: str) -> str:
        """Executes state transition hooks, returning the target state (or perturbed state)."""
        target = to_state
        for hook in self._state_transition_hooks:
            try:
                res = hook(from_state, target)
                if res is not None:
                    target = res
            except Exception as e:
                logger.error(f"Error executing state_transition hook {hook.__name__}: {e}", exc_info=True)
        return target


# Global default hook registry matching EnBW template
GLOBAL_HOOKS = HookRegistry()


def pre_send(message: V2GMessage, state_name: str) -> V2GMessage:
    """Default module-level pre_send hook entry point."""
    return GLOBAL_HOOKS.run_pre_send(message, state_name)


def post_receive(response: V2GMessage, state_name: str) -> None:
    """Default module-level post_receive hook entry point."""
    GLOBAL_HOOKS.run_post_receive(response, state_name)
