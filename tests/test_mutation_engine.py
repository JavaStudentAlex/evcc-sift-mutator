"""Unit Tests for Bandit, Mutation Operators, and SIFT Judge."""
from mutation.bandit import UCB1Bandit, MutationArm
from mutation.operators import (
    mutate_numerical_boundary,
    mutate_semantic_spoof,
    MutationCandidate,
)
from mutation.sift_judge import SIFTJudge
from protocols.iso15118_messages import CurrentDemandReq


def test_ucb1_bandit_selection_and_update():
    bandit = UCB1Bandit(exploration_c=1.0)
    # First 4 pulls should be initial exploration of all arms
    pulled_arms = set()
    for _ in range(4):
        arm = bandit.select_arm()
        pulled_arms.add(arm)
        bandit.update(arm, 1.0)

    assert len(pulled_arms) == 4

    # Reward one arm heavily
    bandit.update(MutationArm.ARM_NUMERICAL_BOUNDARY, 50.0)
    assert bandit.select_arm() == MutationArm.ARM_NUMERICAL_BOUNDARY


def test_sift_pairwise_tournament():
    judge = SIFTJudge()
    req = CurrentDemandReq()

    cand_a = mutate_numerical_boundary(req)
    cand_b = mutate_semantic_spoof(req)

    winner, score, meta = judge.rank_tournament([cand_a, cand_b])
    assert winner in [cand_a, cand_b]
    assert len(meta["theta"]) == 2
    assert meta["winner_idx"] in [0, 1]


def test_bradley_terry_convergence():
    judge = SIFTJudge()
    # 3 candidates: 0 beats 1, 1 beats 2, 0 beats 2
    comparisons = [
        (0, 1, "A"),
        (1, 2, "A"),
        (0, 2, "A"),
    ]
    theta = judge.fit_bradley_terry(3, comparisons)
    # theta[0] > theta[1] > theta[2]
    assert theta[0] > theta[1]
    assert theta[1] > theta[2]
