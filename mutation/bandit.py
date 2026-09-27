"""Multi-Armed Bandit (BAM / UCB1) Selection Engine for Protocol Mutation.

Implements Upper Confidence Bound (UCB1) reinforcement learning over mutation arms.
Dynamically favors mutation strategies that provoke abnormal charger behaviors,
safety interlock trips, sequence errors, or CAN bus distress frames.
"""
from __future__ import annotations

import json
import math
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("mutation_bandit")


class MutationArm:
    ARM_NUMERICAL_BOUNDARY = "ARM_NUMERICAL_BOUNDARY"
    ARM_SEQUENCE_INVERSION = "ARM_SEQUENCE_INVERSION"
    ARM_SEMANTIC_SPOOF = "ARM_SEMANTIC_SPOOF"
    ARM_SLAC_ATTENUATION = "ARM_SLAC_ATTENUATION"

    ALL_ARMS = [
        ARM_NUMERICAL_BOUNDARY,
        ARM_SEQUENCE_INVERSION,
        ARM_SEMANTIC_SPOOF,
        ARM_SLAC_ATTENUATION,
    ]


class UCB1Bandit:
    """UCB1 Multi-Armed Bandit for mutation operator selection."""

    def __init__(self, arms: Optional[List[str]] = None, exploration_c: float = 1.414):
        self.arms = arms or list(MutationArm.ALL_ARMS)
        self.exploration_c = exploration_c
        self.counts: Dict[str, int] = {arm: 0 for arm in self.arms}
        self.rewards: Dict[str, float] = {arm: 0.0 for arm in self.arms}
        self.total_pulls: int = 0

    def select_arm(self) -> str:
        """Selects the arm with the highest Upper Confidence Bound score."""
        self.total_pulls += 1
        # Initial exploration: try each arm once
        for arm in self.arms:
            if self.counts[arm] == 0:
                logger.debug(f"[Bandit] Initial exploration pull for arm: {arm}")
                return arm

        best_arm = self.arms[0]
        best_score = -float("inf")

        for arm in self.arms:
            mean_reward = self.rewards[arm] / self.counts[arm]
            bonus = self.exploration_c * math.sqrt(math.log(self.total_pulls) / self.counts[arm])
            score = mean_reward + bonus
            if score > best_score:
                best_score = score
                best_arm = arm

        logger.debug(f"[Bandit] Selected [{best_arm}] (score={best_score:.3f})")
        return best_arm

    def update(self, arm: str, reward: float) -> None:
        """Updates the empirical mean and pull count for the selected arm."""
        if arm not in self.counts:
            self.arms.append(arm)
            self.counts[arm] = 0
            self.rewards[arm] = 0.0

        self.counts[arm] += 1
        self.rewards[arm] += reward
        logger.info(
            f"[Bandit] Updated [{arm}]: reward={reward:.2f}, pulls={self.counts[arm]}, mean={(self.rewards[arm]/self.counts[arm]):.3f}"
        )

    def get_stats(self) -> Dict[str, Dict[str, float]]:
        """Returns empirical performance summary for all arms."""
        stats = {}
        for arm in self.arms:
            n = self.counts[arm]
            mean = (self.rewards[arm] / n) if n > 0 else 0.0
            stats[arm] = {
                "pulls": float(n),
                "total_reward": self.rewards[arm],
                "mean_reward": mean,
            }
        return stats

    def to_json(self) -> str:
        """Serializes bandit state to JSON."""
        return json.dumps({
            "exploration_c": self.exploration_c,
            "counts": self.counts,
            "rewards": self.rewards,
            "total_pulls": self.total_pulls,
        }, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> UCB1Bandit:
        """Restores bandit state from JSON."""
        data = json.loads(json_str)
        bandit = cls(arms=list(data["counts"].keys()), exploration_c=data["exploration_c"])
        bandit.counts = data["counts"]
        bandit.rewards = data["rewards"]
        bandit.total_pulls = data["total_pulls"]
        return bandit
