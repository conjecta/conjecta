from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace
from typing import Any, Coroutine
from unittest.mock import AsyncMock, MagicMock

import pytest

import math_agent.web.agent_factory as agent_factory
import math_agent.web.app as web_app
import math_agent.web.solve_routes as solve_routes
import math_agent.web.solve_session as solve_session
from math_agent.agent.context_augmentor import AugmentationResult
from math_agent.agent.react_state import ReActSolution, ReActTrace
from math_agent.agent.supervisor import SupervisorAgent
from math_agent.agent.supervisor_intake import IntakeResult
from math_agent.config import AgentConfig


class RecordingPostSolveManager:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.tasks: list[asyncio.Task[Any]] = []

    def create(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        self.timeline.append("post_solve_scheduled")
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


async def _async_build_agent_result(agent: Any) -> Any:
    return agent


def _patch_solve_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent: Any,
    timeline: list[str],
    post_solve_manager: RecordingPostSolveManager | None = None,
) -> None:
    config = SimpleNamespace(lean=SimpleNamespace(enabled=False, lean_path=None))
    monkeypatch.setattr(solve_session, "load_config", lambda: config)
    monkeypatch.setattr(
        solve_session,
        "new_session_logger",
        lambda problem, model: ("session-test", logging.getLogger("test.solve-stream")),
    )
    monkeypatch.setattr(
        agent_factory, "_build_agent", lambda **kwargs: _async_build_agent_result(agent)
    )
    monkeypatch.setattr(agent_factory, "_maybe_knowledge_store", lambda user_id=None: None)

    class _EmptyStore:
        def list_turns(self, _project_id):
            return []

    monkeypatch.setattr(agent_factory, "_project_store", lambda user_id=None: _EmptyStore())
    monkeypatch.setattr(agent_factory, "default_model_string", lambda config: "openai/gpt-5.6-sol")
    monkeypatch.setattr(agent_factory, "prefix_history", lambda problem, history: problem)

    def persist_pending_turn(store, project_id, problem, files, *, conversation_id=""):
        timeline.append("persist_pending_turn")
        return {
            "id": "pending-turn",
            "problem": problem,
            "answer": "",
            "conversation_id": conversation_id,
        }

    def persist_turn(
        store,
        project_id,
        problem,
        final_answer,
        files,
        *,
        conversation_id="",
        turn_id="",
        verification_status=None,
        strategy=None,
        session_id=None,
        lean_proofs=None,
        verification_issues=None,
        tool_evidence=None,
    ):
        timeline.append("persist_turn")
        return {"problem": problem, "answer": final_answer, "id": turn_id or "final-turn"}

    monkeypatch.setattr(agent_factory, "persist_pending_turn", persist_pending_turn)
    monkeypatch.setattr(agent_factory, "persist_turn", persist_turn)
    if post_solve_manager is not None:
        monkeypatch.setattr(
            agent_factory,
            "post_solve_tasks",
            post_solve_manager,
            raising=False,
        )


@pytest.mark.asyncio
async def test_attachment_processing_is_offloaded_from_event_loop(monkeypatch):
    timeline: list[str] = []
    worker_threads: list[int] = []
    release_processing = threading.Event()
    released_by_event_loop: list[bool] = []

    def blocking_attachment_processing(files):
        worker_threads.append(threading.get_ident())
        released_by_event_loop.append(release_processing.wait(timeout=0.3))
        return [], []

    _patch_solve_dependencies(
        monkeypatch,
        agent=SimpleNamespace(),
        timeline=timeline,
    )
    monkeypatch.setattr(solve_session, "to_image_parts", blocking_attachment_processing)
    stream = solve_session.stream_solve_events({"problem": "P", "files": [{}]})
    asyncio.get_running_loop().call_later(0.01, release_processing.set)
    first_event = asyncio.create_task(stream.__anext__())

    event = await first_event
    await stream.aclose()

    assert released_by_event_loop == [True]
    assert event["type"] == "session"
    assert len(worker_threads) == 1
    assert worker_threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_stream_keeps_current_problem_separate_from_conversation_history(monkeypatch):
    timeline: list[str] = []
    captured: dict[str, Any] = {}

    class CapturingAgent:
        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            captured["problem"] = problem
            captured["conversation_history"] = kwargs["conversation_history"]
            return ReActSolution(problem=problem, turns=[], final_answer="continued")

    _patch_solve_dependencies(monkeypatch, agent=CapturingAgent(), timeline=timeline)
    events = [
        event
        async for event in solve_session.stream_solve_events(
            {
                "problem": "Prove the remaining case.",
                "conversation_history": [
                    {"role": "user", "text": "Prove the base case."},
                    {"role": "assistant", "text": "The base case is established."},
                ],
            }
        )
    ]

    assert captured["problem"] == "Prove the remaining case."
    assert captured["conversation_history"][-1] == {
        "role": "assistant",
        "text": "The base case is established.",
    }
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_stream_persists_the_problem_extracted_from_an_attachment(monkeypatch):
    timeline: list[str] = []
    persisted: dict[str, str] = {}

    class ExtractedProblemAgent:
        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            return ReActSolution(
                problem="Prove the theorem visible in the image.",
                turns=[],
                final_answer="Proof complete.",
            )

    _patch_solve_dependencies(
        monkeypatch,
        agent=ExtractedProblemAgent(),
        timeline=timeline,
    )

    def persist_turn(
        _store,
        _project_id,
        problem,
        final_answer,
        _files,
        *,
        conversation_id="",
        turn_id="",
        **_extra,
    ):
        persisted["problem"] = problem
        persisted["answer"] = final_answer
        persisted["conversation_id"] = conversation_id
        persisted["turn_id"] = turn_id

    monkeypatch.setattr(agent_factory, "persist_turn", persist_turn)

    events = [
        event
        async for event in solve_session.stream_solve_events(
            {
                "problem": "请根据附件中的题目进行求解。",
                "files": [],
                "conversation_id": "conversation-image",
            }
        )
    ]

    assert events[-1]["type"] == "done"
    assert persisted == {
        "problem": "Prove the theorem visible in the image.",
        "answer": "Proof complete.",
        "conversation_id": "conversation-image",
        "turn_id": "pending-turn",
    }


@pytest.mark.asyncio
async def test_stream_emits_one_done_after_persistence_with_verification(monkeypatch):
    timeline: list[str] = []
    post_solve_finished = asyncio.Event()

    class FakeAgent:
        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            assert kwargs["defer_post_solve"] is True
            on_event = kwargs["on_event"]
            await on_event({"type": "stage_status", "stage": "thinking"})
            await on_event(
                {
                    "type": "tool_start",
                    "step_num": 1,
                    "tool": "compute",
                    "args_preview": "print(2+2)",
                }
            )
            await on_event(
                {
                    "type": "tool_done",
                    "step_num": 1,
                    "tool": "compute",
                    "success": True,
                    "output": "4",
                }
            )
            # A nested solver must never be able to steal terminal-event ownership.
            await on_event({"type": "done", "summary": "inner terminal"})
            trace = ReActTrace(problem=problem)
            return ReActSolution(
                problem=problem,
                turns=[],
                final_answer="outer answer",
                lean_proofs=["theorem answer : True := by trivial"],
                verification_status="reviewed",
                verification_issues=["formal proof was not requested"],
                trace=trace,
            )

        def take_post_solve(self):
            async def finish_learning() -> None:
                timeline.append("post_solve_started")
                post_solve_finished.set()

            return finish_learning()

    manager = RecordingPostSolveManager(timeline)
    _patch_solve_dependencies(
        monkeypatch,
        agent=FakeAgent(),
        timeline=timeline,
        post_solve_manager=manager,
    )

    events = []
    async for event in solve_session.stream_solve_events(
        {
            "problem": "What is 2+2?",
            "project_id": "project-test",
            "mode": "react",
        }
    ):
        events.append(event)
        timeline.append(f"event:{event['type']}")

    done_events = [event for event in events if event["type"] == "done"]
    assert len(done_events) == 1
    assert timeline.index("persist_turn") < timeline.index("event:done")
    assert timeline.index("persist_turn") < timeline.index("post_solve_scheduled")
    done = done_events[0]
    assert done == {
        "type": "done",
        "summary": "outer answer",
        "final_answer": "outer answer",
        "lean_proofs": ["theorem answer : True := by trivial"],
        "strategy": "react",
        "verification_status": "reviewed",
        "verification_issues": ["formal proof was not requested"],
        "tool_evidence": done["tool_evidence"],
    }
    evidence = done["tool_evidence"]
    assert len(evidence) == 1
    entry = evidence[0]
    assert entry["tool"] == "compute"
    assert entry["step_num"] == 1
    assert entry["args_preview"] == "print(2+2)"
    assert entry["success"] is True
    assert entry["output_preview"] == "4"
    assert entry["duration_seconds"] >= 0
    assert entry["started_at"]

    await asyncio.wait_for(post_solve_finished.wait(), timeout=1)
    await asyncio.gather(*manager.tasks)


@pytest.mark.asyncio
async def test_closing_stream_with_cancel_flag_cancels_and_awaits_underlying_solve(
    monkeypatch,
):
    timeline: list[str] = []
    solve_started = asyncio.Event()
    solve_cancelled = asyncio.Event()

    class BlockingAgent:
        task: asyncio.Task[Any] | None = None

        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            self.task = asyncio.current_task()
            solve_started.set()
            await kwargs["on_event"]({"type": "stage_status", "stage": "thinking"})
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                solve_cancelled.set()
                raise

    agent = BlockingAgent()
    _patch_solve_dependencies(monkeypatch, agent=agent, timeline=timeline)
    stream = solve_session.stream_solve_events(
        {"problem": "Keep solving", "_cancel_research": True}
    )

    try:
        assert (await stream.__anext__())["type"] == "session"
        assert (await stream.__anext__())["type"] == "turn_started"
        assert (await stream.__anext__())["type"] == "stage_status"
        await solve_started.wait()

        await stream.aclose()
        await asyncio.sleep(0)

        assert solve_cancelled.is_set()
        assert agent.task is not None and agent.task.done()
    finally:
        if agent.task is not None and not agent.task.done():
            agent.task.cancel()
            await asyncio.gather(agent.task, return_exceptions=True)


@pytest.mark.asyncio
async def test_closing_normal_stream_detaches_and_persists_underlying_solve(monkeypatch):
    timeline: list[str] = []
    solve_started = asyncio.Event()
    release = asyncio.Event()
    solve_cancelled = asyncio.Event()
    persisted: dict[str, Any] = {}
    manager = RecordingPostSolveManager(timeline)

    class BlockingNormalAgent:
        task: asyncio.Task[Any] | None = None

        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            self.task = asyncio.current_task()
            solve_started.set()
            on_event = kwargs["on_event"]
            await on_event({"type": "stage_status", "stage": "thinking"})
            try:
                await release.wait()
            except asyncio.CancelledError:
                solve_cancelled.set()
                raise
            # Evidence emitted after the transport is gone must still persist.
            await on_event(
                {
                    "type": "tool_start",
                    "step_num": 1,
                    "tool": "compute",
                    "args_preview": "print(1)",
                }
            )
            await on_event(
                {
                    "type": "tool_done",
                    "step_num": 1,
                    "tool": "compute",
                    "success": True,
                    "output": "1",
                }
            )
            return ReActSolution(problem=problem, turns=[], final_answer="finished")

    def persist_turn(
        _store,
        _project_id,
        problem,
        final_answer,
        _files,
        *,
        conversation_id="",
        turn_id="",
        verification_status=None,
        strategy=None,
        session_id=None,
        lean_proofs=None,
        verification_issues=None,
        tool_evidence=None,
    ):
        timeline.append("persist_turn")
        persisted["answer"] = final_answer
        persisted["tool_evidence"] = tool_evidence
        return {"problem": problem, "answer": final_answer, "id": turn_id or "final-turn"}

    agent = BlockingNormalAgent()
    _patch_solve_dependencies(
        monkeypatch,
        agent=agent,
        timeline=timeline,
        post_solve_manager=manager,
    )
    monkeypatch.setattr(agent_factory, "persist_turn", persist_turn)
    stream = solve_session.stream_solve_events({"problem": "Keep solving"})

    assert (await stream.__anext__())["type"] == "session"
    assert (await stream.__anext__())["type"] == "turn_started"
    assert (await stream.__anext__())["type"] == "stage_status"
    await solve_started.wait()
    await stream.aclose()
    await asyncio.sleep(0)

    assert not solve_cancelled.is_set()
    assert agent.task is not None and not agent.task.done()

    release.set()
    await asyncio.gather(*manager.tasks)

    assert persisted["answer"] == "finished"
    assert agent.task.done()
    evidence = persisted["tool_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["tool"] == "compute"
    assert evidence[0]["success"] is True
    assert evidence[0]["output_preview"] == "1"
    assert "duration_seconds" in evidence[0]


@pytest.mark.asyncio
async def test_post_solve_manager_cancels_and_awaits_retained_tasks():
    from math_agent.web.post_solve import PostSolveTaskManager

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def post_solve_work() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    manager = PostSolveTaskManager()
    task = manager.create(post_solve_work())
    await started.wait()

    await manager.shutdown()

    assert cancelled.is_set()
    assert task.done()


@pytest.mark.asyncio
async def test_supervisor_exposes_post_solve_work_without_starting_it(monkeypatch):
    trace = ReActTrace(problem="augmented problem")
    solution = ReActSolution(
        problem="augmented problem",
        turns=[],
        final_answer="answer",
        trace=trace,
    )

    async def fake_run_react(*args: Any, **kwargs: Any):
        return solution, trace

    post_solve_calls: list[dict[str, Any]] = []

    async def fake_run_post_solve(**kwargs: Any) -> None:
        post_solve_calls.append(kwargs)

    supervisor = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(memory_consolidation_enabled=True),
    )
    supervisor._intake = SimpleNamespace(
        analyze=AsyncMock(return_value=IntakeResult(strategy="react"))
    )
    supervisor._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(
                prompt="augmented problem",
                memories_used=[],
            )
        )
    )
    monkeypatch.setattr(supervisor, "_run_react", fake_run_react)
    monkeypatch.setattr(
        supervisor,
        "_run_post_solve",
        fake_run_post_solve,
        raising=False,
    )

    returned = await supervisor.solve("problem", defer_post_solve=True)

    assert returned is solution
    assert post_solve_calls == []
    deferred = supervisor.take_post_solve()
    assert deferred is not None
    await deferred
    assert len(post_solve_calls) == 1
    assert post_solve_calls[0]["solution"] is solution
    assert post_solve_calls[0]["trace"] is trace
    assert supervisor.take_post_solve() is None


class ClosableEventStream:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = iter(events)
        self.closed = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def aclose(self) -> None:
        self.closed.set()


class FakeSolveRequest:
    def __init__(
        self,
        *,
        disconnected: bool,
        body_chunks: list[bytes] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.disconnected = disconnected
        self.body_chunks = body_chunks or [b'{"problem":"test","mode":"react"}']
        self.headers = headers or {}
        self.stream_reads = 0

    async def json(self) -> dict[str, Any]:
        return __import__("json").loads(b"".join(self.body_chunks))

    async def stream(self):
        for chunk in self.body_chunks:
            self.stream_reads += 1
            yield chunk

    async def is_disconnected(self) -> bool:
        return self.disconnected


def _patch_http_stream(
    monkeypatch: pytest.MonkeyPatch,
    stream: ClosableEventStream,
) -> None:
    monkeypatch.setattr(solve_routes, "require_http_app_access", lambda request: None)
    monkeypatch.setattr(
        solve_routes,
        "require_auth_user",
        lambda request: SimpleNamespace(user_id="user-test"),
    )
    monkeypatch.setattr(
        solve_routes,
        "stream_solve_events",
        lambda msg, user_id=None: stream,
    )
    monkeypatch.setattr(
        solve_routes,
        "_check_solve_quota",
        AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_http_solve_rejects_declared_oversized_body_before_read(monkeypatch):
    stream = ClosableEventStream([])
    _patch_http_stream(monkeypatch, stream)
    monkeypatch.setattr(solve_routes, "MAX_SOLVE_REQUEST_BYTES", 32)
    request = FakeSolveRequest(
        disconnected=False,
        body_chunks=[b'{"problem":"too large"}'],
        headers={"content-length": "33"},
    )

    with pytest.raises(Exception) as exc:
        await solve_routes.solve_stream(request)  # type: ignore[arg-type]

    assert exc.value.status_code == 413
    assert request.stream_reads == 0


@pytest.mark.asyncio
async def test_http_solve_caps_chunked_body_without_content_length(monkeypatch):
    stream = ClosableEventStream([])
    _patch_http_stream(monkeypatch, stream)
    monkeypatch.setattr(solve_routes, "MAX_SOLVE_REQUEST_BYTES", 32)
    request = FakeSolveRequest(
        disconnected=False,
        body_chunks=[b'{"problem":"', b"x" * 64, b'"}'],
        headers={},
    )

    with pytest.raises(Exception) as exc:
        await solve_routes.solve_stream(request)  # type: ignore[arg-type]

    assert exc.value.status_code == 413
    assert request.stream_reads == 2


@pytest.mark.asyncio
async def test_http_stream_closes_shared_generator_on_disconnect(monkeypatch):
    stream = ClosableEventStream([{"type": "session", "session_id": "http"}])
    _patch_http_stream(monkeypatch, stream)

    response = await solve_routes.solve_stream(
        FakeSolveRequest(disconnected=True)  # type: ignore[arg-type]
    )
    chunks = [chunk async for chunk in response.body_iterator]

    assert chunks == []
    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_http_stream_closes_shared_generator_when_body_iterator_exits(monkeypatch):
    stream = ClosableEventStream(
        [
            {"type": "session", "session_id": "http"},
            {"type": "stage_status", "stage": "thinking"},
        ]
    )
    _patch_http_stream(monkeypatch, stream)

    response = await solve_routes.solve_stream(
        FakeSolveRequest(disconnected=False)  # type: ignore[arg-type]
    )
    body = response.body_iterator
    first = await body.__anext__()
    await body.aclose()

    assert '"type": "session"' in first
    assert stream.closed.is_set()


class IdleEventStream:
    def __init__(self) -> None:
        self.closed = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closed.set()


@pytest.mark.asyncio
async def test_http_stream_emits_heartbeat_ping_when_idle(monkeypatch):
    stream = IdleEventStream()
    _patch_http_stream(monkeypatch, stream)
    monkeypatch.setattr(solve_routes, "SOLVE_STREAM_HEARTBEAT_SECONDS", 0.01)

    response = await solve_routes.solve_stream(
        FakeSolveRequest(disconnected=False)  # type: ignore[arg-type]
    )
    body = response.body_iterator
    first = await body.__anext__()
    second = await body.__anext__()
    await body.aclose()

    assert first == '{"type": "ping"}\n'
    assert second == '{"type": "ping"}\n'
    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_lifespan_shuts_down_post_solve_manager(monkeypatch):
    async def no_prefetch() -> None:
        return None

    lean_manager = SimpleNamespace(shutdown=AsyncMock())
    post_solve_manager = SimpleNamespace(shutdown=AsyncMock())
    monkeypatch.setattr(agent_factory, "_prefetch_lean_workspace", no_prefetch)
    monkeypatch.setattr(agent_factory, "lean_jobs", lean_manager)
    monkeypatch.setattr(agent_factory, "post_solve_tasks", post_solve_manager)

    async with web_app.lifespan(web_app.app):
        pass

    lean_manager.shutdown.assert_awaited_once()
    post_solve_manager.shutdown.assert_awaited_once()


# --- Solve trace persistence, session-id passthrough, and trace routes ---

from fastapi.testclient import TestClient  # noqa: E402

from math_agent.web.project_store import ProjectStore  # noqa: E402
from math_agent.web.security import LOCAL_DEV_USER_ID  # noqa: E402
from math_agent.web.trace_store import TraceRecorder, read_trace  # noqa: E402


@pytest.mark.asyncio
async def test_solve_writes_trace_with_steps_and_done_but_no_tokens(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    timeline: list[str] = []

    class TracingAgent:
        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            on_event = kwargs["on_event"]
            await on_event({"type": "token", "text": "chunk"})
            await on_event({"type": "step", "step_num": 1, "content": "thinking"})
            await on_event(
                {
                    "type": "tool_start",
                    "step_num": 1,
                    "tool": "compute",
                    "args_preview": "print(2+2)",
                }
            )
            await on_event(
                {
                    "type": "tool_done",
                    "step_num": 1,
                    "tool": "compute",
                    "success": True,
                    "output": "4",
                }
            )
            return ReActSolution(problem=problem, turns=[], final_answer="4")

    _patch_solve_dependencies(monkeypatch, agent=TracingAgent(), timeline=timeline)

    events = [
        event
        async for event in solve_session.stream_solve_events({"problem": "What is 2+2?"})
    ]

    session_id = events[0]["session_id"]
    # Token events still reach the live stream; they must not reach the trace.
    assert "token" in [event["type"] for event in events]
    recorded = read_trace("anonymous", session_id)
    recorded_types = [event["type"] for event in recorded]
    assert "token" not in recorded_types
    assert "ping" not in recorded_types
    assert recorded_types[0] == "session"
    for expected in ("turn_started", "step", "tool_start", "tool_done", "done"):
        assert expected in recorded_types


def _make_checkpoint_store(monkeypatch, tmp_path, checkpoint: dict[str, Any]) -> None:
    store = ProjectStore(root=tmp_path / "checkpoint-store")
    store.write_checkpoint(checkpoint)
    monkeypatch.setattr(
        agent_factory, "_project_store", lambda user_id=None: store
    )


@pytest.mark.asyncio
async def test_resume_reuses_session_id_when_checkpoint_matches(monkeypatch, tmp_path):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    timeline: list[str] = []

    class EchoAgent:
        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            return ReActSolution(problem=problem, turns=[], final_answer="resumed")

    _patch_solve_dependencies(monkeypatch, agent=EchoAgent(), timeline=timeline)
    _make_checkpoint_store(
        monkeypatch,
        tmp_path,
        {"session_id": "pinned-id", "problem": "Prove it.", "project_id": "default"},
    )

    events = [
        event
        async for event in solve_session.stream_solve_events(
            {
                "problem": "Prove it.",
                "checkpoint_id": "pinned-id",
                "session_id": "pinned-id",
                "mode": "react",
            }
        )
    ]

    assert events[0] == {"type": "session", "session_id": "pinned-id"}
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_resume_generates_new_session_id_when_checkpoint_mismatches(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    timeline: list[str] = []

    class EchoAgent:
        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            return ReActSolution(problem=problem, turns=[], final_answer="resumed")

    _patch_solve_dependencies(monkeypatch, agent=EchoAgent(), timeline=timeline)
    _make_checkpoint_store(
        monkeypatch,
        tmp_path,
        {"session_id": "other-id", "problem": "Prove it.", "project_id": "default"},
    )

    events = [
        event
        async for event in solve_session.stream_solve_events(
            {
                "problem": "Prove it.",
                "checkpoint_id": "other-id",
                "session_id": "pinned-id",
                "mode": "react",
            }
        )
    ]

    assert events[0]["type"] == "session"
    assert events[0]["session_id"] != "pinned-id"


@pytest.mark.asyncio
async def test_pinned_resume_rejected_while_session_already_running(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    timeline: list[str] = []
    solve_started = False

    class NoRunAgent:
        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            nonlocal solve_started
            solve_started = True
            return ReActSolution(problem=problem, turns=[], final_answer="x")

    _patch_solve_dependencies(monkeypatch, agent=NoRunAgent(), timeline=timeline)
    _make_checkpoint_store(
        monkeypatch,
        tmp_path,
        {"session_id": "pinned-id", "problem": "Prove it.", "project_id": "default"},
    )
    dummy_task = asyncio.create_task(asyncio.Event().wait())
    solve_session.active_solve_tasks.register(
        "pinned-id", user_id=None, task=dummy_task, mode="react"
    )
    try:
        events = [
            event
            async for event in solve_session.stream_solve_events(
                {
                    "problem": "Prove it.",
                    "checkpoint_id": "pinned-id",
                    "session_id": "pinned-id",
                    "mode": "react",
                }
            )
        ]
    finally:
        solve_session.active_solve_tasks.discard("pinned-id", dummy_task)
        dummy_task.cancel()
        await asyncio.gather(dummy_task, return_exceptions=True)

    assert events == [{"type": "error", "message": "Solve already running."}]
    assert solve_started is False


trace_route_client = TestClient(web_app.app)


def _wire_trace_routes(monkeypatch, tmp_path) -> ProjectStore:
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_DISABLE_QUOTA", "1")
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    store = ProjectStore(root=tmp_path / "route-store")
    monkeypatch.setattr(solve_routes, "_project_store", lambda _uid: store)
    return store


def _write_trace(session_id: str, events: list[dict[str, Any]]) -> None:
    recorder = TraceRecorder(LOCAL_DEV_USER_ID, session_id)
    for event in events:
        recorder.record(event)
    recorder.close()


def test_trace_route_returns_persisted_events(monkeypatch, tmp_path):
    _wire_trace_routes(monkeypatch, tmp_path)
    _write_trace(
        "sess-trace",
        [
            {"type": "session", "session_id": "sess-trace"},
            {"type": "step", "step_num": 1},
            {"type": "done", "final_answer": "42"},
        ],
    )

    response = trace_route_client.get("/api/solve/sess-trace/trace")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["session_id"] == "sess-trace"
    assert [event["type"] for event in body["events"]] == ["session", "step", "done"]


def test_trace_route_404_without_trace(monkeypatch, tmp_path):
    _wire_trace_routes(monkeypatch, tmp_path)

    response = trace_route_client.get("/api/solve/sess-missing/trace")

    assert response.status_code == 404


def test_trace_route_400_on_invalid_session_id(monkeypatch, tmp_path):
    _wire_trace_routes(monkeypatch, tmp_path)

    response = trace_route_client.get("/api/solve/bad%20id/trace")

    assert response.status_code == 400


def test_status_route_reports_has_trace(monkeypatch, tmp_path):
    store = _wire_trace_routes(monkeypatch, tmp_path)
    store.write_checkpoint({"session_id": "sess-with-trace", "project_id": "default"})
    store.write_checkpoint({"session_id": "sess-no-trace", "project_id": "default"})
    _write_trace("sess-with-trace", [{"type": "done", "final_answer": "42"}])

    with_trace = trace_route_client.get("/api/solve/sess-with-trace/status")
    without_trace = trace_route_client.get("/api/solve/sess-no-trace/status")

    assert with_trace.status_code == 200
    assert with_trace.json()["has_trace"] is True
    assert without_trace.status_code == 200
    assert without_trace.json()["has_trace"] is False
