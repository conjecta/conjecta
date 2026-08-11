import sys
from pathlib import Path
from unittest.mock import AsyncMock, DEFAULT, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import evaluate_math_agent as eval_script
from math_agent.config import AgentConfig


def _make_summary(false_verified_count=0, accuracy=1.0):
    summary = MagicMock()
    summary.false_verified_count = false_verified_count
    summary.accuracy = accuracy
    summary.to_dict = dict
    return summary


def test_research_is_not_a_transport_mode():
    # Research mode was removed along with its orchestrator; formal escalation
    # is driven by the problem's verification requirement, not a mode switch.
    with pytest.raises(SystemExit):
        eval_script._parse_args(["--mode", "research"])

    # Its goal-parallelism ablation flag went with it.
    with pytest.raises(SystemExit):
        eval_script._parse_args(["--research-max-parallel-goals", "3"])


@pytest.mark.asyncio
async def test_supervisor_path_is_default():
    """By default the evaluator should route through SupervisorAgent."""
    with patch.multiple(
        eval_script,
        load_config=DEFAULT,
        create_backend=DEFAULT,
        LeanRunner=DEFAULT,
        LeanCodegen=DEFAULT,
        ToolRegistry=DEFAULT,
        PlanMemory=DEFAULT,
        SupervisorAgent=DEFAULT,
        ReActAgent=DEFAULT,
        run_evaluation=DEFAULT,
        write_results=DEFAULT,
        load_cases=DEFAULT,
    ) as mocks:
        cfg = MagicMock()
        cfg.lean.enabled = True
        cfg.agent = AgentConfig(tools=[])
        mocks["load_config"].return_value = cfg
        mocks["create_backend"].return_value = MagicMock()
        mocks["LeanRunner"].return_value = MagicMock()
        mocks["SupervisorAgent"].return_value.solve = AsyncMock()
        mocks["ReActAgent"].return_value.solve = AsyncMock()

        fake_case = MagicMock()
        fake_case.id = "case-1"
        fake_case.problem = "test problem"
        fake_case.require_formal_verification = False
        mocks["load_cases"].return_value = [fake_case]

        async def _fake_run(cases, solve, trials=1):
            for case in cases:
                for _ in range(trials):
                    await solve(case)
            return [], _make_summary(false_verified_count=0, accuracy=1.0)

        mocks["run_evaluation"].side_effect = _fake_run

        args = eval_script._parse_args(["--dataset", "data/eval_smoke.jsonl"])
        assert args.direct_react is False

        # Exercise the full _main path so the default agent selection is verified.
        await eval_script._main(["--dataset", "data/eval_smoke.jsonl"])
        mocks["SupervisorAgent"].assert_called_once()
        mocks["ReActAgent"].assert_not_called()


@pytest.mark.asyncio
async def test_direct_react_uses_react_agent():
    """The --direct-react flag should bypass SupervisorAgent."""
    with patch.multiple(
        eval_script,
        load_config=DEFAULT,
        create_backend=DEFAULT,
        LeanRunner=DEFAULT,
        LeanCodegen=DEFAULT,
        ToolRegistry=DEFAULT,
        PlanMemory=DEFAULT,
        SupervisorAgent=DEFAULT,
        ReActAgent=DEFAULT,
        run_evaluation=DEFAULT,
        write_results=DEFAULT,
        load_cases=DEFAULT,
    ) as mocks:
        cfg = MagicMock()
        cfg.lean.enabled = True
        cfg.agent = AgentConfig(tools=[])
        mocks["load_config"].return_value = cfg
        mocks["create_backend"].return_value = MagicMock()
        mocks["LeanRunner"].return_value = MagicMock()
        mocks["SupervisorAgent"].return_value.solve = AsyncMock()
        mocks["ReActAgent"].return_value.solve = AsyncMock()

        fake_case = MagicMock()
        fake_case.id = "case-1"
        fake_case.problem = "test problem"
        fake_case.require_formal_verification = False
        mocks["load_cases"].return_value = [fake_case]

        async def _fake_run(cases, solve, trials=1):
            for case in cases:
                for _ in range(trials):
                    await solve(case)
            return [], _make_summary(false_verified_count=0, accuracy=1.0)

        mocks["run_evaluation"].side_effect = _fake_run

        args = eval_script._parse_args(["--direct-react"])
        assert args.direct_react is True

        await eval_script._main(["--direct-react"])
        mocks["ReActAgent"].assert_called_once()
        mocks["SupervisorAgent"].assert_not_called()


@pytest.mark.asyncio
async def test_main_exits_nonzero_on_zero_accuracy():
    """The evaluator must fail when no cases are answered correctly."""
    with patch.multiple(
        eval_script,
        load_config=DEFAULT,
        create_backend=DEFAULT,
        LeanRunner=DEFAULT,
        LeanCodegen=DEFAULT,
        ToolRegistry=DEFAULT,
        PlanMemory=DEFAULT,
        SupervisorAgent=DEFAULT,
        ReActAgent=DEFAULT,
        run_evaluation=DEFAULT,
        write_results=DEFAULT,
        load_cases=DEFAULT,
    ) as mocks:
        cfg = MagicMock()
        cfg.lean.enabled = True
        cfg.agent = AgentConfig(tools=[])
        mocks["load_config"].return_value = cfg
        mocks["create_backend"].return_value = MagicMock()
        mocks["LeanRunner"].return_value = MagicMock()
        mocks["SupervisorAgent"].return_value.solve = AsyncMock()

        fake_case = MagicMock()
        fake_case.id = "case-1"
        fake_case.problem = "test problem"
        fake_case.require_formal_verification = False
        mocks["load_cases"].return_value = [fake_case]
        mocks["run_evaluation"].return_value = (
            [],
            _make_summary(false_verified_count=0, accuracy=0.0),
        )

        exit_code = await eval_script._main(["--dataset", "data/eval_smoke.jsonl"])
        assert exit_code == 1


@pytest.mark.asyncio
async def test_main_exits_nonzero_on_false_verified():
    """The evaluator must fail when any formally-verified answer is incorrect."""
    with patch.multiple(
        eval_script,
        load_config=DEFAULT,
        create_backend=DEFAULT,
        LeanRunner=DEFAULT,
        LeanCodegen=DEFAULT,
        ToolRegistry=DEFAULT,
        PlanMemory=DEFAULT,
        SupervisorAgent=DEFAULT,
        ReActAgent=DEFAULT,
        run_evaluation=DEFAULT,
        write_results=DEFAULT,
        load_cases=DEFAULT,
    ) as mocks:
        cfg = MagicMock()
        cfg.lean.enabled = True
        cfg.agent = AgentConfig(tools=[])
        mocks["load_config"].return_value = cfg
        mocks["create_backend"].return_value = MagicMock()
        mocks["LeanRunner"].return_value = MagicMock()
        mocks["SupervisorAgent"].return_value.solve = AsyncMock()

        fake_case = MagicMock()
        fake_case.id = "case-1"
        fake_case.problem = "test problem"
        fake_case.require_formal_verification = False
        mocks["load_cases"].return_value = [fake_case]
        mocks["run_evaluation"].return_value = (
            [],
            _make_summary(false_verified_count=1, accuracy=0.5),
        )

        exit_code = await eval_script._main(["--dataset", "data/eval_smoke.jsonl"])
        assert exit_code == 1


@pytest.mark.asyncio
async def test_supervisor_auto_approves_hitl_pause_and_resumes():
    """An unattended eval run must auto-approve HITL pauses and resume."""
    from math_agent.agent.react_state import HumanInputRequired

    with patch.multiple(
        eval_script,
        load_config=DEFAULT,
        create_backend=DEFAULT,
        LeanRunner=DEFAULT,
        LeanCodegen=DEFAULT,
        ToolRegistry=DEFAULT,
        PlanMemory=DEFAULT,
        SupervisorAgent=DEFAULT,
        ReActAgent=DEFAULT,
        run_evaluation=DEFAULT,
        write_results=DEFAULT,
        load_cases=DEFAULT,
    ) as mocks:
        cfg = MagicMock()
        cfg.lean.enabled = True
        cfg.agent = AgentConfig(tools=[])
        mocks["load_config"].return_value = cfg
        mocks["create_backend"].return_value = MagicMock()
        mocks["LeanRunner"].return_value = MagicMock()

        interaction = {
            "request_id": "hitl-1",
            "kind": "reviewer_block",
            "question": "Accept best-effort answer?",
            "allowed_decisions": ["approve", "reject", "edit", "respond"],
        }
        solution = MagicMock()
        solution.metadata = {}
        calls: list[dict] = []

        async def _solve(problem, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                # Mimic the agent checkpointing the pending pause, then raising.
                store = mocks["SupervisorAgent"].call_args.kwargs["project_store"]
                store.write_checkpoint(
                    {
                        "session_id": kwargs["session_id"],
                        "problem": problem,
                        "pending_interaction": dict(interaction),
                    }
                )
                raise HumanInputRequired(interaction)
            return solution

        mocks["SupervisorAgent"].return_value.solve = AsyncMock(side_effect=_solve)

        fake_case = MagicMock()
        fake_case.id = "case-1"
        fake_case.problem = "test problem"
        fake_case.require_formal_verification = False
        mocks["load_cases"].return_value = [fake_case]

        async def _fake_run(cases, solve, trials=1):
            for case in cases:
                for _ in range(trials):
                    await solve(case)
            return [], _make_summary(false_verified_count=0, accuracy=1.0)

        mocks["run_evaluation"].side_effect = _fake_run

        exit_code = await eval_script._main(["--dataset", "data/eval_smoke.jsonl"])

        assert exit_code == 0
        assert len(calls) == 2
        assert calls[0]["prior_trace"] is None
        assert calls[0]["human_decision"] is None
        # The resume passes the checkpoint and an auto-approve decision.
        assert calls[1]["session_id"] == calls[0]["session_id"]
        assert calls[1]["prior_trace"]["pending_interaction"]["request_id"] == "hitl-1"
        assert calls[1]["human_decision"]["request_id"] == "hitl-1"
        assert calls[1]["human_decision"]["decision"] == "approve"
        assert "auto-resolved" in calls[1]["human_decision"]["feedback"]


@pytest.mark.asyncio
async def test_supervisor_hitl_pause_without_checkpoint_reraises():
    """A pause with no usable checkpoint must still surface as an error."""
    from math_agent.agent.react_state import HumanInputRequired

    with patch.multiple(
        eval_script,
        load_config=DEFAULT,
        create_backend=DEFAULT,
        LeanRunner=DEFAULT,
        LeanCodegen=DEFAULT,
        ToolRegistry=DEFAULT,
        PlanMemory=DEFAULT,
        SupervisorAgent=DEFAULT,
        ReActAgent=DEFAULT,
        run_evaluation=DEFAULT,
        write_results=DEFAULT,
        load_cases=DEFAULT,
    ) as mocks:
        cfg = MagicMock()
        cfg.lean.enabled = True
        cfg.agent = AgentConfig(tools=[])
        mocks["load_config"].return_value = cfg
        mocks["create_backend"].return_value = MagicMock()
        mocks["LeanRunner"].return_value = MagicMock()

        interaction = {
            "request_id": "hitl-1",
            "kind": "reviewer_block",
            "question": "?",
            "allowed_decisions": ["approve"],
        }
        mocks["SupervisorAgent"].return_value.solve = AsyncMock(
            side_effect=HumanInputRequired(interaction)
        )

        fake_case = MagicMock()
        fake_case.id = "case-1"
        fake_case.problem = "test problem"
        fake_case.require_formal_verification = False
        mocks["load_cases"].return_value = [fake_case]

        raised: list[BaseException] = []

        async def _fake_run(cases, solve, trials=1):
            for case in cases:
                try:
                    await solve(case)
                except HumanInputRequired as exc:
                    raised.append(exc)
            return [], _make_summary(false_verified_count=0, accuracy=1.0)

        mocks["run_evaluation"].side_effect = _fake_run

        await eval_script._main(["--dataset", "data/eval_smoke.jsonl"])

        assert len(raised) == 1


@pytest.mark.asyncio
async def test_supervisor_uses_isolated_plan_memory_per_case():
    """Each evaluation case must receive its own PlanMemory instance."""
    with patch.multiple(
        eval_script,
        load_config=DEFAULT,
        create_backend=DEFAULT,
        LeanRunner=DEFAULT,
        LeanCodegen=DEFAULT,
        ToolRegistry=DEFAULT,
        PlanMemory=DEFAULT,
        SupervisorAgent=DEFAULT,
        ReActAgent=DEFAULT,
        run_evaluation=DEFAULT,
        write_results=DEFAULT,
        load_cases=DEFAULT,
    ) as mocks:
        cfg = MagicMock()
        cfg.lean.enabled = True
        cfg.agent = AgentConfig(tools=[])
        mocks["load_config"].return_value = cfg
        mocks["create_backend"].return_value = MagicMock()
        mocks["LeanRunner"].return_value = MagicMock()
        mocks["SupervisorAgent"].return_value.solve = AsyncMock()

        case_a = MagicMock()
        case_a.id = "case-a"
        case_a.problem = "problem a"
        case_a.require_formal_verification = False
        case_b = MagicMock()
        case_b.id = "case-b"
        case_b.problem = "problem b"
        case_b.require_formal_verification = False
        mocks["load_cases"].return_value = [case_a, case_b]

        plan_memory_instances = []

        def _make_plan_memory(*, path, seed_path):
            instance = MagicMock()
            instance.path = path
            instance.seed_path = seed_path
            plan_memory_instances.append(instance)
            return instance

        mocks["PlanMemory"].side_effect = _make_plan_memory

        async def _fake_run(cases, solve, trials=2):
            for case in cases:
                for _ in range(trials):
                    await solve(case)
            return [], _make_summary(false_verified_count=0, accuracy=1.0)

        mocks["run_evaluation"].side_effect = _fake_run

        await eval_script._main(["--dataset", "data/eval_smoke.jsonl", "--trials", "2"])

        assert len(plan_memory_instances) == 2
        assert plan_memory_instances[0].seed_path is None
        assert plan_memory_instances[1].seed_path is None
        assert plan_memory_instances[0].path != plan_memory_instances[1].path
