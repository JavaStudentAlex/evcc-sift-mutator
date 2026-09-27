"""Procedural Decision Graph Runtime for Automated Protocol Security Testing.

Represents the complete fuzzing and penetration-testing lifecycle as an executable,
evolving graph of operational nodes. Ported from the kaggriculture procedural graph architecture.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from protocols.iso15118_messages import CurrentDemandReq, V2GMessage
from mutation.bandit import MutationArm, UCB1Bandit
from mutation.operators import (
    MutationCandidate,
    mutate_numerical_boundary,
    mutate_semantic_spoof,
)
from mutation.self_healing import SelfHealingEngine
from mutation.sift_judge import SIFTJudge
from simulator.state_machine import EvccState, Iso15118EvccStateMachine

logger = logging.getLogger("graph_runtime")


class GraphExecutionStep:
    """Records the execution of a single graph node."""

    def __init__(self, node_id: str, input_data: Any, output_data: Any):
        self.node_id = node_id
        self.input_data = input_data
        self.output_data = output_data


class ProceduralFuzzingGraph:
    """Executable decision graph for orchestrating adaptive protocol fuzzing."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self.get_default_config()
        self.bandit = UCB1Bandit(exploration_c=self.config.get("exploration_c", 1.414))
        self.self_healing = SelfHealingEngine(max_retries=self.config.get("max_retries", 3))
        self.judge = SIFTJudge()
        self.execution_history: List[List[GraphExecutionStep]] = []

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {
            "name": "Procedural_EVCC_SIFT_Graph_v1",
            "version": "1.0.0",
            "exploration_c": 1.414,
            "max_retries": 3,
            "candidate_pool_size": 3,
            "reward_weights": {
                "emergency_shutdown": 10.0,
                "sequence_error": 5.0,
                "wrong_parameter": 4.0,
                "abnormal_response": 2.0,
                "successful_repair": 1.0,
            },
            "enabled_arms": list(MutationArm.ALL_ARMS),
        }

    def execute_fuzzing_cycle(
        self,
        base_msg: V2GMessage,
        current_state: str,
        dispatch_fn: Any,
    ) -> Tuple[V2GMessage, float, Dict[str, Any]]:
        """Executes a complete procedural node-flow cycle for one message transmission."""
        steps: List[GraphExecutionStep] = []

        # Node 1: State Inspection
        state_info = {"current_state": current_state, "msg_type": type(base_msg).__name__}
        steps.append(GraphExecutionStep("Node_StateInspection", base_msg, state_info))

        # Node 2: Bandit Selection
        selected_arm = self.bandit.select_arm()
        steps.append(GraphExecutionStep("Node_BanditSelect", state_info, {"selected_arm": selected_arm}))

        # Node 3: Multi-Candidate Generation (Pool Size N)
        pool_size = self.config.get("candidate_pool_size", 3)
        raw_candidates: List[MutationCandidate] = []
        for _ in range(pool_size):
            if selected_arm == MutationArm.ARM_NUMERICAL_BOUNDARY:
                cand = mutate_numerical_boundary(base_msg)
            elif selected_arm == MutationArm.ARM_SEMANTIC_SPOOF:
                cand = mutate_semantic_spoof(base_msg)
            else:
                # Default boundary fallback
                cand = mutate_numerical_boundary(base_msg)
            raw_candidates.append(cand)

        steps.append(GraphExecutionStep("Node_CandidateGen", {"arm": selected_arm, "count": pool_size}, raw_candidates))

        # Node 4: Self-Healing Validation
        validated_candidates: List[MutationCandidate] = []
        repaired_flags: List[bool] = []
        for cand in raw_candidates:
            # Validate through self-healing loop
            val_res = self.self_healing.validate_payload(cand.mutated_payload)
            if val_res.is_valid:
                validated_candidates.append(cand)
                repaired_flags.append(False)
            else:
                # Attempt programmatic repair
                repaired_cand, was_repaired = self.self_healing.execute_with_self_healing(
                    base_msg,
                    lambda b, err: mutate_numerical_boundary(b),
                )
                validated_candidates.append(repaired_cand)
                repaired_flags.append(was_repaired)

        steps.append(GraphExecutionStep("Node_SelfHealing", raw_candidates, validated_candidates))

        # Node 5: SIFT Pairwise Tournament
        winner_cand, bt_score, tourney_meta = self.judge.rank_tournament(validated_candidates)
        steps.append(GraphExecutionStep("Node_SIFTJudge", validated_candidates, {"winner": winner_cand, "bt_score": bt_score}))

        # Node 6: Hardware / Mock Dispatch
        response = dispatch_fn(winner_cand.mutated_payload)
        steps.append(GraphExecutionStep("Node_HardwareDispatch", winner_cand.mutated_payload, response))

        # Node 7: Oracle Anomaly Evaluation & Reward Calculation
        reward, meta = self._compute_reward(response, repaired_flags)
        steps.append(GraphExecutionStep("Node_OracleEvaluate", response, {"reward": reward, "meta": meta}))

        # Node 8: Bandit Update
        self.bandit.update(selected_arm, reward)
        steps.append(GraphExecutionStep("Node_BanditUpdate", {"arm": selected_arm, "reward": reward}, self.bandit.get_stats()))

        self.execution_history.append(steps)
        return response, reward, {
            "selected_arm": selected_arm,
            "winner_description": winner_cand.description,
            "bt_score": bt_score,
            "reward": reward,
            "meta": meta,
        }

    def _compute_reward(self, response: Any, repaired_flags: List[bool]) -> Tuple[float, Dict[str, Any]]:
        """Calculates reinforcement reward based on charger behavioral distress."""
        weights = self.config.get("reward_weights", {})
        total_reward = 0.0
        meta = {}

        if response is None:
            total_reward += weights.get("emergency_shutdown", 10.0)
            meta["outcome"] = "Target Connection Reset / Drop"
            return total_reward, meta

        resp_code = getattr(response, "response_code", "")
        code_str = str(resp_code)

        if "SequenceError" in code_str:
            total_reward += weights.get("sequence_error", 5.0)
            meta["outcome"] = "SequenceError Triggered"
        elif "WrongChargeParameter" in code_str:
            total_reward += weights.get("wrong_parameter", 4.0)
            meta["outcome"] = "WrongChargeParameter Detected"
        elif "FAILED" in code_str:
            total_reward += weights.get("abnormal_response", 2.0)
            meta["outcome"] = "Generic Failure Code"
        else:
            total_reward += 0.5
            meta["outcome"] = "Accepted / OK"

        if any(repaired_flags):
            total_reward += weights.get("successful_repair", 1.0)
            meta["self_healed"] = True

        return total_reward, meta
