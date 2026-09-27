"""Unit Tests for Procedural Fuzzing Graph and Graph Mutator."""
import tempfile
from pathlib import Path
from procedural_graph.graph_runtime import ProceduralFuzzingGraph
from procedural_graph.graph_mutator import ProceduralGraphMutator
from protocols.iso15118_messages import CurrentDemandReq, CurrentDemandRes, ResponseCode


def test_procedural_graph_cycle_execution():
    graph = ProceduralFuzzingGraph()
    req = CurrentDemandReq(ev_target_voltage=400.0, ev_target_current=150.0)

    def mock_dispatch(msg):
        return CurrentDemandRes(
            response_code=ResponseCode.OK,
            evse_present_voltage=400.0,
            evse_present_current=150.0,
        )

    res, reward, meta = graph.execute_fuzzing_cycle(req, "CURRENT_DEMAND", mock_dispatch)

    assert res is not None
    assert reward >= 0.5
    assert "selected_arm" in meta
    assert len(graph.execution_history) == 1
    # 8 operational nodes recorded in the trace
    assert len(graph.execution_history[0]) == 8


def test_graph_mutator_evolution_and_checkpoint():
    graph = ProceduralFuzzingGraph()
    mutator = ProceduralGraphMutator(graph)

    # Simulate heavy pulls on one arm
    graph.bandit.counts["ARM_NUMERICAL_BOUNDARY"] = 25
    graph.bandit.counts["ARM_SEMANTIC_SPOOF"] = 2
    graph.bandit.total_pulls = 30

    old_c = graph.config["exploration_c"]
    new_cfg = mutator.mutate_parameters()
    # Over-exploitation should have boosted exploration constant
    assert new_cfg["exploration_c"] > old_c

    with tempfile.TemporaryDirectory() as tmpdir:
        ckpt_path = Path(tmpdir) / "test_policy.json"
        mutator.save_checkpoint(str(ckpt_path))
        assert ckpt_path.exists()
        assert "exploration_c" in ckpt_path.read_text()
