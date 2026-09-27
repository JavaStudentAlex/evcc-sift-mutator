"""Self-Healing Mutation Engine with Schema Validation Retry Loop.

Implements the multi-attempt self-healing retry loop (SIFT_MUTATION_RETRIES = 3)
derived from the SIFT architecture in kaggriculture.

Catches serialization, type, and schema exceptions locally, extracts the exact
traceback, and feeds error context back to the mutator/LLM for automated repair
before any packet touches the wire. Guarantees 0% syntax-level hallucination.
"""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import ValidationError
from protocols.iso15118_messages import V2GMessage
from mutation.operators import MutationCandidate

logger = logging.getLogger("self_healing")

SIFT_MUTATION_RETRIES = 3


class ValidationResult:
    def __init__(self, is_valid: bool, error_message: str = "", fixed_payload: Optional[Any] = None):
        self.is_valid = is_valid
        self.error_message = error_message
        self.fixed_payload = fixed_payload


class SelfHealingEngine:
    """Orchestrates local validation and self-healing repair attempts."""

    def __init__(self, max_retries: int = SIFT_MUTATION_RETRIES):
        self.max_retries = max_retries
        self.repair_history: List[Dict[str, Any]] = []

    def validate_payload(self, msg: Any) -> ValidationResult:
        """Locally tests serialization, schema conformity, and bitstream packaging."""
        if not isinstance(msg, V2GMessage):
            return ValidationResult(is_valid=False, error_message=f"Payload {type(msg)} does not inherit from V2GMessage")

        try:
            # 1. Pydantic validation
            msg_class = type(msg)
            msg_dict = msg.model_dump()
            validated_obj = msg_class.model_validate(msg_dict)

            # 2. Simulated EXI bitstream encoding test
            bitstream = validated_obj.to_exi_payload()
            if not bitstream or len(bitstream) == 0:
                return ValidationResult(is_valid=False, error_message="EXI serialization generated empty bitstream")

            # 3. Round-trip deserialization verification
            roundtrip = msg_class.from_exi_payload(bitstream)
            if not roundtrip:
                return ValidationResult(is_valid=False, error_message="Failed to round-trip deserialize EXI bitstream")

            return ValidationResult(is_valid=True)

        except ValidationError as e:
            err_desc = "; ".join([f"{err['loc']}: {err['msg']}" for err in e.errors()])
            return ValidationResult(is_valid=False, error_message=f"Pydantic Schema ValidationError: {err_desc}")
        except Exception as e:
            tb = traceback.format_exc()
            return ValidationResult(is_valid=False, error_message=f"Codec Exception: {str(e)}\n{tb}")

    def execute_with_self_healing(
        self,
        base_message: V2GMessage,
        mutator_fn: Callable[[V2GMessage, Optional[str]], MutationCandidate],
    ) -> Tuple[MutationCandidate, bool]:
        """Runs the mutator and self-heals up to max_retries if validation fails.

        Returns (winning_candidate, was_repaired).
        """
        last_error = ""
        was_repaired = False

        for attempt in range(1, self.max_retries + 1):
            try:
                candidate = mutator_fn(base_message, last_error if attempt > 1 else None)
                val_res = self.validate_payload(candidate.mutated_payload)

                if val_res.is_valid:
                    if attempt > 1:
                        logger.info(f"✓ [Self-Healing] Successfully repaired candidate on attempt {attempt}/{self.max_retries}")
                        was_repaired = True
                    return candidate, was_repaired

                last_error = val_res.error_message
                logger.warning(
                    f"✗ [Self-Healing] Attempt {attempt}/{self.max_retries} failed validation: {last_error[:120]}"
                )
                self.repair_history.append({
                    "attempt": attempt,
                    "error": last_error,
                    "candidate_desc": candidate.description,
                })

            except Exception as e:
                last_error = str(e)
                logger.error(f"✗ [Self-Healing] Mutator crash on attempt {attempt}: {last_error}")

        logger.error(f"[Self-Healing EXHAUSTED] All {self.max_retries} attempts failed. Returning unmutated base message.")
        fallback = MutationCandidate(
            arm="FALLBACK",
            description=f"Fallback unmutated due to failed repairs: {last_error[:80]}",
            mutated_payload=base_message,
            rationale="Safety fallback to preserve session execution.",
        )
        return fallback, False
