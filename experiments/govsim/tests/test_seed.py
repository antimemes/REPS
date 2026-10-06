"""Seed propagation and contention checks against the pinned upstream code.

Run in the experiment's Nix test environment.
No model calls or embedding downloads are needed.
"""

import numpy as np
import pytest

from govsim_adapter.main import EXPERIMENTS, Params, _compose, run


def parameters(experiment="fish_baseline_concurrent"):
    return Params(
        experiment=experiment, max_rounds=1, model="mock/model", embedder="hash",
        temperature=None, top_p=None, max_tokens=64,
    )


@pytest.mark.parametrize("experiment", EXPERIMENTS)
def test_run_passes_effective_seed_to_environment(
    tmp_path, monkeypatch, event_capture, experiment,
):
    from simulation.scenarios.common.environment.concurrent_env import ConcurrentEnv

    class ReachedReset(Exception):
        pass

    received = []
    original_reset = ConcurrentEnv.reset

    def observe_reset(self, seed=None, options=None):
        received.append(seed)
        original_reset(self, seed=seed, options=options)
        # Stop before the first persona action, keeping the real adapter and
        # upstream construction path without making any model calls.
        raise ReachedReset

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPS_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("REPS_SEED", "37")
    monkeypatch.setattr(ConcurrentEnv, "reset", observe_reset)
    with pytest.raises(ReachedReset):
        run(parameters(experiment))

    events = event_capture.read()
    assert not any(event["type"] == "llm.call" for event in events)
    # Stopping before a checkpoint records its absence without replacing the
    # simulation's exception with FileNotFoundError during final ingestion.
    assert any(event.get("kind") == "govsim.unparsed_log"
               and "log_env.json" in event["data"]["error"] for event in events)
    assert received == [37], "The simulation environment must receive the run seed"


@pytest.mark.parametrize("allocation", ["_assign_stochastic", "_assign_proportional"])
def test_explicit_seed_repeats_contended_allocations(tmp_path, allocation):
    from simulation.scenarios.fishing.environment import FishingConcurrentEnv

    cfg = _compose(parameters(), seed=37)
    names = {f"persona_{i}": f"Agent {i}" for i in range(5)}

    def sequence(global_seed):
        # The environment's explicitly seeded RNG should be independent of
        # unrelated draws from NumPy's global RNG.
        global_state = np.random.get_state()
        try:
            np.random.seed(global_seed)
            env = FishingConcurrentEnv(cfg.experiment.env, str(tmp_path), names)
            env.reset(seed=37)
            results = []
            for _ in range(20):
                # Demand 25 from a pool of 7. Proportional allocation also
                # needs random tie-breaking because 7 is not divisible by 5.
                env.internal_global_state["resource_in_pool"] = 7
                env.internal_global_state["wanted_resource"] = {
                    agent: 5 for agent in env.agents
                }
                result = getattr(env, allocation)()
                assert sum(result.values()) == 7
                assert all(0 <= amount <= 5 for amount in result.values())
                results.append(result)
            return results
        finally:
            np.random.set_state(global_state)

    assert sequence(123) == sequence(456)
