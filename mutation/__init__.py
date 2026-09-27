"""Mutation Package for Search-Based Protocol Testing."""
from .bandit import MutationArm, UCB1Bandit
from .operators import (
    MutationCandidate,
    mutate_numerical_boundary,
    mutate_sequence_inversion,
    mutate_semantic_spoof,
    mutate_slac_frame,
)
from .self_healing import SelfHealingEngine, SIFT_MUTATION_RETRIES, ValidationResult
from .sift_judge import SIFTJudge
from .prompts import (
    CONFORMANCE_SYSTEM_PROMPT,
    SELF_CORRECTION_PROMPT_TEMPLATE,
    PAIRWISE_EVALUATION_PROMPT_TEMPLATE,
)
from .llm_client import SafeLLMClient, sanitize_text

# Aliases for backward compatibility
MUTATION_SYSTEM_PROMPT = CONFORMANCE_SYSTEM_PROMPT
SELF_HEALING_PROMPT_TEMPLATE = SELF_CORRECTION_PROMPT_TEMPLATE
PAIRWISE_JUDGE_PROMPT_TEMPLATE = PAIRWISE_EVALUATION_PROMPT_TEMPLATE

__all__ = [
    "MutationArm",
    "UCB1Bandit",
    "MutationCandidate",
    "mutate_numerical_boundary",
    "mutate_sequence_inversion",
    "mutate_semantic_spoof",
    "mutate_slac_frame",
    "SelfHealingEngine",
    "SIFT_MUTATION_RETRIES",
    "ValidationResult",
    "SIFTJudge",
    "CONFORMANCE_SYSTEM_PROMPT",
    "SELF_CORRECTION_PROMPT_TEMPLATE",
    "PAIRWISE_EVALUATION_PROMPT_TEMPLATE",
    "MUTATION_SYSTEM_PROMPT",
    "SELF_HEALING_PROMPT_TEMPLATE",
    "PAIRWISE_JUDGE_PROMPT_TEMPLATE",
    "SafeLLMClient",
    "sanitize_text",
]
