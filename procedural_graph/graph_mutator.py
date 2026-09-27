"""Graph Mutator for Self-Improving Procedural Decision Graphs.

Adapts the procedural policy graph based on feedback from the physical testbed.
Optimizes exploration constants, reward coefficients, and candidate pool sizing
to maximize the discovery rate of critical edge cases.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, List, Tuple

from procedural_graph.graph_runtime import ProceduralFuzzingGraph

logger = logging.getLogger("graph_mutator")


class ProceduralGraphMutator:
    """Evolves the configuration and policy parameters of the procedural graph."""

    def __init__(self, target_graph: ProceduralFuzzingGraph):
        self.graph = target_graph

    def mutate_parameters(self) -> Dict[str, Any]:
        """Applies an evolutionary mutation step to the procedural graph configuration."""
        new_config = copy.deepcopy(self.graph.config)
        stats = self.graph.bandit.get_stats()

        # Analyze bandit exploitation vs exploration
        pulls = [s["pulls"] for s in stats.values()]
        max_pulls = max(pulls) if pulls else 0
        total_pulls = sum(pulls)

        # 1. If an arm is over-exploited (>70% of pulls), bump exploration constant
        if total_pulls > 10 and max_pulls / total_pulls > 0.70:
            new_config["exploration_c"] = round(new_config.get("exploration_c", 1.414) * 1.25, 3)
            logger.info(f"[GraphMutator] Increased exploration_c to {new_config['exploration_c']}")

        # 2. Dynamically expand candidate pool size if rewards are plateauing
        mean_rewards = [s["mean_reward"] for s in stats.values()]
        avg_mean = sum(mean_rewards) / len(mean_rewards) if mean_rewards else 0.0
        if avg_mean < 2.0 and new_config.get("candidate_pool_size", 3) < 5:
            new_config["candidate_pool_size"] = new_config.get("candidate_pool_size", 3) + 1
            logger.info(f"[GraphMutator] Expanded candidate_pool_size to {new_config['candidate_pool_size']}")

        # 3. Update active configuration
        self.graph.config = new_config
        self.graph.bandit.exploration_c = new_config["exploration_c"]
        return new_config

    def save_checkpoint(self, path: str) -> None:
        """Saves current evolved graph policy to disk."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "config": self.graph.config,
                "bandit_stats": self.graph.bandit.get_stats(),
                "total_cycles": len(self.graph.execution_history),
            }, f, indent=2)
        logger.info(f"Saved evolved graph policy checkpoint to {path}")
