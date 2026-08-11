"""Shared solve session streaming for WebSocket and HTTP transports."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from math_agent.agent.react_state import ProjectContext
from math_agent.agent.react_state import HumanInputRequired
from math_agent.agent.human_interaction import interaction_event
from math_agent.config import load_config
from math_agent.log_config import close_session_logger, new_session_logger
from math_agent.web import agent_factory
from math_agent.web import hitl_auto_resolve
from math_agent.web.attachments import resolve_problem_text, to_image_parts
from math_agent.web.knowledge_selection import normalize_conversation_history
from math_agent.web.latex_sanitize import sanitize_latex_answer
from math_agent.web.project_access import resolve_project_access
from math_agent.web.solve_mode import resolve_solve_mode
from math_agent.web.active_solves import active_solve_tasks, solve_capacity
from math_agent.web.trace_store import TraceRecorder
from math_agent.web.public_errors import public_solve_error
from math_agent.web.operations import (
    finish_solve_run,
    reset_usage_context,
    set_usage_context,
    start_solve_run,
)
from fastapi import HTTPException

log = logging.getLogger("math_agent.web.solve_session")

# Bounds the events buffered for one client. Reached only when a client drains
# far slower than the agent emits; see on_event for the drop/backpressure rule.
SOLVE_EVENT_QUEUE_MAXSIZE = 1000


def _apply_goal_action(prior_trace: dict, goal_action: dict) -> dict:
    """Apply a claimed node-level goal action to a checkpoint trace.

    retry resets the goal (cascading to dependents); edit additionally rewrites
    its statement. The goal becomes the active target of the resumed run and
    any human guidance rides along in the context preamble.
    """
    from math_agent.agent.proof_graph import ProofGraph

    graph = ProofGraph.from_dict(prior_trace.get("proof_graph"))
    goal_id = str(goal_action.get("goal_id") or "").strip()
    action = str(goal_action.get("action") or "").strip().lower()
    statement = str(goal_action.get("statement") or "").strip()
    guidance = str(goal_action.get("guidance") or "").strip()
    try:
        if action == "edit" and statement:
            graph.edit_goal_statement(goal_id, statement)
        else:
            graph.reset_goal(goal_id, cascade=True)
        goal = graph.activate(goal_id)
    except (KeyError, ValueError) as exc:
        log.warning("Goal action on %s could not be applied: %s", goal_id, exc)
        return prior_trace
    prior_trace["proof_graph"] = graph.to_dict()
    prior_trace["current_goal"] = goal.statement
    if guidance:
        note = f"Human guidance for the resumed goal: {guidance}"
        preamble = str(prior_trace.get("context_preamble") or "")
        prior_trace["context_preamble"] = f"{preamble}\n\n{note}".strip()
    return prior_trace


async def stream_solve_events(
    msg: dict[str, Any], *, user_id: str | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Run one solve session and yield its NDJSON event stream."""
    problem = msg.get("problem", "")
    conversation_history = msg.get("conversation_history") or []
    history_turns = normalize_conversation_history(conversation_history)
    has_conversation_history = bool(history_turns)
    raw_files = msg.get("files") or []
    attachments, attach_notices = await asyncio.to_thread(to_image_parts, raw_files)
    config = load_config()
    try:
        model = agent_factory._resolve_platform_model(
            msg.get("model"), default_to_config=True
        )
    except HTTPException as exc:
        yield {"type": "error", "message": str(exc.detail)}
        return
    api_key = agent_factory._platform_api_key(model)
    requested_project_id = (msg.get("project_id") or "").strip()
    requested_owner_user_id = (msg.get("owner_user_id") or "").strip() or None
    conversation_id = str(msg.get("conversation_id") or "").strip()[:128]
    resume_checkpoint_id = (msg.get("checkpoint_id") or "").strip()
    # Resume requests may pin the original session id so a recovered run keeps
    # its identity (registry, checkpoints, run row) instead of forking a new
    # one. Only honored when the loaded checkpoint actually carries that id.
    requested_session_id = (
        (msg.get("session_id") or "").strip()[:128] if resume_checkpoint_id else ""
    )
    try:
        mode = resolve_solve_mode(msg)
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    prior_trace: dict | None = None
    if resume_checkpoint_id:
        try:
            # Tentatively load from actor store; may retry with lead store after access resolve.
            prior_trace = await asyncio.to_thread(
                agent_factory._project_store(user_id).get_checkpoint, resume_checkpoint_id
            )
        except Exception as exc:
            log.warning("Failed to load checkpoint %s: %s", resume_checkpoint_id, exc)
    human_decision = msg.get("human_decision")
    if not isinstance(human_decision, dict) and prior_trace and isinstance(
        prior_trace.get("submitted_human_decision"), dict
    ):
        human_decision = prior_trace["submitted_human_decision"]
    # Historical research checkpoints (from the decommissioned research engine)
    # resume as ordinary traces: there is one solve path now, and formal
    # escalation is driven by the problem's own verification requirement.
    effective_mode = mode
    goal_action = msg.get("goal_action")
    if (
        isinstance(goal_action, dict)
        and prior_trace
        and isinstance(prior_trace.get("proof_graph"), dict)
    ):
        prior_trace = _apply_goal_action(dict(prior_trace), goal_action)

    checkpoint_project_id = ""
    checkpoint_owner_user_id = ""
    if prior_trace:
        checkpoint_project_id = str(prior_trace.get("project_id") or "").strip()
        raw_project_context = prior_trace.get("project_context")
        if not checkpoint_project_id and isinstance(raw_project_context, dict):
            checkpoint_project_id = str(
                raw_project_context.get("project_id") or ""
            ).strip()
        if isinstance(raw_project_context, dict):
            checkpoint_owner_user_id = str(raw_project_context.get("user_id") or "").strip()
    project_id = requested_project_id or checkpoint_project_id or "default"
    owner_hint = requested_owner_user_id or checkpoint_owner_user_id or None

    knowledge_tenant_id = user_id
    if user_id:
        try:
            access = await asyncio.to_thread(
                resolve_project_access,
                user_id,
                project_id,
                owner_user_id=owner_hint,
                create_if_missing=True,
            )
            knowledge_tenant_id = access.knowledge_tenant_id
            owner_hint = access.owner_user_id
        except HTTPException as exc:
            yield {"type": "error", "message": str(exc.detail)}
            return
        except Exception as exc:
            log.warning("Project access resolve failed, using actor tenant: %s", exc)

    if resume_checkpoint_id and knowledge_tenant_id and knowledge_tenant_id != user_id:
        try:
            lead_trace = await asyncio.to_thread(
                agent_factory._project_store(knowledge_tenant_id).get_checkpoint, resume_checkpoint_id
            )
            if lead_trace:
                prior_trace = lead_trace
        except Exception as exc:
            log.warning("Failed to load checkpoint from lead store %s: %s", resume_checkpoint_id, exc)

    project_context = ProjectContext(project_id=project_id, user_id=knowledge_tenant_id)

    resolved = resolve_problem_text(problem, attachments)
    if resolved is None and prior_trace:
        checkpoint_problem = str(prior_trace.get("problem") or "").strip()
        resolved = checkpoint_problem or None
    if resolved is None:
        yield {"type": "error", "message": "No problem provided"}
        return
    problem = resolved

    if not solve_capacity.try_acquire():
        log.warning(
            "Solve rejected at capacity in_flight=%d user=%s",
            solve_capacity.in_flight,
            user_id or "-",
        )
        yield {
            "type": "error",
            "code": "SERVER_BUSY",
            "message": "服务器当前求解任务已满，请稍后重试。",
        }
        return
    capacity_held = True

    session_id, session_log = new_session_logger(problem, model=model)
    if (
        requested_session_id
        and prior_trace
        and str(prior_trace.get("session_id") or "") == requested_session_id
    ):
        session_id = requested_session_id
        if active_solve_tasks.contains(session_id, user_id=user_id):
            # A pinned resume must never double-run a session that is already
            # live (e.g. startup recovery racing a manual resume).
            close_session_logger(session_log)
            solve_capacity.release()
            capacity_held = False
            yield {"type": "error", "message": "Solve already running."}
            return
    session_log.info(
        "Solve stream started (mode=%s has_history=%s user_id=%s)",
        effective_mode,
        has_conversation_history,
        user_id or "-",
    )
    attachment_summary = []
    for idx, f in enumerate(raw_files or []):
        if isinstance(f, dict):
            du = f.get("data_url") or ""
            attachment_summary.append({
                "index": idx,
                "kind": f.get("kind"),
                "name": f.get("name"),
                "data_url_chars": len(du),
                "approx_decoded_bytes": (len(du) * 3 // 4) if du else 0,
            })
    session_log.info("Attachment summary: %s", attachment_summary)
    recorder = TraceRecorder(knowledge_tenant_id or "anonymous", session_id)
    session_event = {"type": "session", "session_id": session_id}
    recorder.record(session_event)
    yield session_event

    pending_turn_id = ""
    store = agent_factory._project_store(knowledge_tenant_id)
    if resume_checkpoint_id:
        existing_turns = await asyncio.to_thread(store.list_turns, project_id)
        for turn in reversed(existing_turns):
            if not isinstance(turn, dict):
                continue
            if str(turn.get("answer") or "").strip():
                continue
            turn_problem = str(turn.get("problem") or "").strip()
            if turn_problem and turn_problem != problem:
                continue
            turn_conversation = str(turn.get("conversation_id") or "")
            if conversation_id and turn_conversation and turn_conversation != conversation_id:
                continue
            pending_turn_id = str(turn.get("id") or "")
            if pending_turn_id:
                break
    if not pending_turn_id:
        try:
            pending = await asyncio.to_thread(
                agent_factory.persist_pending_turn,
                store,
                project_id,
                problem,
                raw_files,
                conversation_id=conversation_id,
            )
            pending_turn_id = str(pending.get("id") or "")
            turn_started = {
                "type": "turn_started",
                "turn_id": pending_turn_id,
                "conversation_id": conversation_id,
                "project_id": project_id,
                "problem": problem,
            }
            recorder.record(turn_started)
            yield turn_started
        except Exception as exc:
            session_log.warning("Failed to persist pending turn: %s", exc)
    else:
        turn_started = {
            "type": "turn_started",
            "turn_id": pending_turn_id,
            "conversation_id": conversation_id,
            "project_id": project_id,
            "problem": problem,
        }
        recorder.record(turn_started)
        yield turn_started

    for notice in attach_notices:
        session_log.info("Attachment notice: %s", notice)
        notice_event = {"type": "stage_status", "stage": "attachment", "message": notice}
        recorder.record(notice_event)
        yield notice_event

    usage_context_token = set_usage_context(
        user_id=user_id, session_id=session_id, operation="solve"
    )
    await start_solve_run(
        session_id=session_id,
        user_id=user_id,
        project_id=project_id,
        problem=problem,
        mode=effective_mode,
        model=model,
    )
    agent: Any | None = None
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SOLVE_EVENT_QUEUE_MAXSIZE)
    solve_task: asyncio.Task[Any] | None = None
    solve_persisted = False
    paused_for_human = False
    run_finished = False
    consumer_active = True

    async def finish_run(status: str, reason: str | None = None) -> None:
        nonlocal run_finished
        if run_finished:
            return
        run_finished = True
        await finish_solve_run(session_id=session_id, status=status, reason=reason)

    tool_evidence: list[dict[str, Any]] = []
    open_tool_calls: dict[Any, tuple[dict[str, Any], float]] = {}

    def record_tool_evidence(entry: dict[str, Any]) -> None:
        tool_evidence.append(entry)
        if len(tool_evidence) > 50:
            del tool_evidence[0]

    async def on_event(event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "done":
            session_log.warning("Ignored nested done event; solve session owns termination")
            return
        recorder.record(event)
        if event_type == "tool_start":
            tool_name = str(event.get("tool") or "")
            entry = {
                "tool": tool_name,
                "step_num": event.get("step_num"),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "args_preview": str(event.get("args_preview") or "")[
                    : 2000 if tool_name == "compute" else 200
                ],
            }
            open_tool_calls[event.get("step_num")] = (entry, time.monotonic())
            record_tool_evidence(entry)
        elif event_type == "tool_done":
            tool_name = str(event.get("tool") or "")
            started = open_tool_calls.pop(event.get("step_num"), None)
            if started is None:
                entry = {
                    "tool": tool_name,
                    "step_num": event.get("step_num"),
                }
                record_tool_evidence(entry)
            else:
                entry, started_monotonic = started
                entry["duration_seconds"] = round(
                    time.monotonic() - started_monotonic, 3
                )
            entry["success"] = bool(event.get("success"))
            entry["output_preview"] = str(event.get("output") or "")[
                : 2000 if tool_name == "compute" else 500
            ]
        if consumer_active:
            # After a detach nobody drains the queue; keep recording evidence
            # and trace events, but stop piling events into memory.
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A client too slow to drain must not grow this without bound.
                # Token deltas are cosmetic and safe to drop; anything else
                # waits, which backpressures the agent instead of losing state.
                if event.get("type") == "token":
                    return
                await queue.put(event)

    async def persist_solution_result(solution: Any) -> None:
        nonlocal solve_persisted
        if solve_persisted:
            return
        solved_problem = str(getattr(solution, "problem", "") or "").strip()
        try:
            await asyncio.to_thread(
                agent_factory.persist_turn,
                store,
                project_id,
                solved_problem or problem,
                getattr(solution, "final_answer", ""),
                raw_files,
                conversation_id=conversation_id,
                turn_id=pending_turn_id,
                verification_status=getattr(solution, "verification_status", "") or None,
                strategy=effective_mode,
                session_id=session_id,
                lean_proofs=getattr(solution, "lean_proofs", None) or None,
                verification_issues=getattr(solution, "verification_issues", None) or None,
                tool_evidence=list(tool_evidence) or None,
            )
        except Exception as exc:
            session_log.warning("Failed to persist turn: %s", exc)
        take_post_solve = getattr(agent, "take_post_solve", None)
        if callable(take_post_solve):
            post_solve = take_post_solve()
            if post_solve is not None:
                agent_factory.post_solve_tasks.create(post_solve)
        solve_persisted = True

    async def finish_detached_solve(task: asyncio.Task[Any]) -> None:
        """Let solve work survive a transport disconnect."""
        try:
            solution = await task
            await persist_solution_result(solution)
            await finish_run("completed")
            session_log.info("Detached solve finished and was persisted")
        except asyncio.CancelledError:
            await finish_run("cancelled", reason="server_shutdown")
            session_log.warning("Detached solve was cancelled")
            raise
        except HumanInputRequired as pause:
            await finish_run("waiting")
            hitl_auto_resolve.schedule_auto_resolve(
                session_id,
                str(pause.interaction.get("request_id") or ""),
                user_id=user_id,
            )
            session_log.info(
                "Detached solve paused for human input request=%s",
                pause.interaction.get("request_id"),
            )
        except Exception:
            await finish_run("failed")
            session_log.exception("Detached solve failed")
        finally:
            active_solve_tasks.discard(session_id, task)
            # The recorder, session logger, and capacity slot outlive the
            # transport for detached runs; the detached finisher owns all three.
            recorder.close()
            session_log.info("=== session end id=%s ===", session_id)
            close_session_logger(session_log)
            solve_capacity.release()

    try:
        agent = await agent_factory._build_agent(
            model_string=model,
            api_key=api_key,
            project_context=project_context,
            user_id=knowledge_tenant_id,
        )
        solve_task = asyncio.create_task(
            agent.solve(
                problem,
                on_event=on_event,
                session_log=session_log,
                prior_trace=prior_trace,
                session_id=session_id,
                mode=effective_mode,
                api_key=api_key,
                model=model,
                attachments=attachments,
                lean_executable=config.lean.lean_path if config.lean.enabled else None,
                has_conversation_history=has_conversation_history,
                conversation_history=history_turns,
                defer_post_solve=True,
                **(
                    {"human_decision": human_decision}
                    if isinstance(human_decision, dict)
                    else {}
                ),
            )
        )
        active_solve_tasks.register(
            session_id,
            user_id=user_id,
            task=solve_task,
            mode=effective_mode,
        )

        while True:
            if solve_task.done() and queue.empty():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.05)
                yield event
            except asyncio.TimeoutError:
                continue

        solution = await solve_task
        await persist_solution_result(solution)
        await finish_run("completed")

        final_answer = sanitize_latex_answer(getattr(solution, "final_answer", "") or "")
        done_event = {
            "type": "done",
            "summary": final_answer,
            "final_answer": final_answer,
            "lean_proofs": getattr(solution, "lean_proofs", []),
            "strategy": effective_mode,
            "verification_status": getattr(solution, "verification_status", "best_effort"),
            "verification_issues": list(getattr(solution, "verification_issues", [])),
            "tool_evidence": list(tool_evidence),
        }
        recorder.record(done_event)
        yield done_event
        session_log.info(
            "Solve stream completed verification_status=%s",
            getattr(solution, "verification_status", "best_effort"),
        )
    except HumanInputRequired as pause:
        paused_for_human = True
        await finish_run("waiting")
        hitl_auto_resolve.schedule_auto_resolve(
            session_id,
            str(pause.interaction.get("request_id") or ""),
            user_id=user_id,
        )
        interaction = interaction_event(pause.interaction, checkpoint_id=session_id)
        recorder.record(interaction)
        yield interaction
        session_log.info(
            "Solve paused for human input request=%s",
            pause.interaction.get("request_id"),
        )
    except asyncio.CancelledError:
        await finish_run("cancelled", reason="task_cancelled")
        session_log.warning("Solve stream cancelled")
        raise
    except Exception as exc:
        await finish_run("failed")
        session_log.exception("Solve stream failed")
        error_event = {"type": "error", "message": public_solve_error(exc)}
        recorder.record(error_event)
        yield error_event
    finally:
        detached = False
        if solve_task is not None:
            # Any unfinished solve (not just research) keeps running server-side
            # after a transport disconnect; `_cancel_research` opts out so an
            # explicit client abort still goes through cancel.
            detach_solve = (
                not bool(msg.get("_cancel_research"))
                and not solve_persisted
                and not paused_for_human
            )
            if detach_solve:
                detached = True
                # Nobody drains the queue from here on; on_event keeps writing
                # trace/evidence but stops buffering events in memory. The
                # detached finisher takes over closing the recorder.
                consumer_active = False
                agent_factory.post_solve_tasks.create(finish_detached_solve(solve_task))
            else:
                if not solve_task.done():
                    solve_task.cancel()
                await asyncio.gather(solve_task, return_exceptions=True)
                active_solve_tasks.discard(session_id, solve_task)
        if not detached:
            recorder.close()
        if not detached and not run_finished:
            # The transport closed before any terminal branch ran (GeneratorExit):
            # do not leave the operations record stuck at "running".
            await finish_run("cancelled", reason="client_disconnected")
            session_log.warning(
                "Solve stream ended by client disconnect session=%s mode=%s",
                session_id,
                effective_mode,
            )
        if session_log and not detached:
            session_log.info("=== session end id=%s ===", session_id)
            close_session_logger(session_log)
        if capacity_held and not detached:
            solve_capacity.release()
            capacity_held = False
        try:
            reset_usage_context(usage_context_token)
        except ValueError:
            # The NDJSON transport drives this generator inside a child task
            # (heartbeat), so set/reset can land in different contexts. The
            # token's context is discarded with that task — nothing leaks.
            pass


def encode_ndjson_event(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"
