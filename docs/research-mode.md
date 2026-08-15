# Research mode（已移除）

> **2026-08-11**：research 已不再是一种模式。旧的 proof-DAG 编排器在
> commit `881b271` 被移除，随后 `24adc31` 把"研究"降级为普通 ReAct 路径上的
> **形式化升级（formal escalation）**策略。2026-08-11 又移除了前端的证明图
> 面板与研究指挥台页面。现在只有一条求解栈。历史设计见 git 历史。

## 现状：只有一种模式

`SolveRequest.mode` 只接受 `auto` / `react`（`scripts/evaluate_math_agent.py`
的 `--mode` 同样只有这两个 choice）。`research` 仅作为**历史 checkpoint 的
strategy 标记**残留在 `math_agent/web/solve_routes.py`，用于恢复旧会话。

原先 research 模式提供的能力，现在由形式化升级路径覆盖：当结论要求
`require_formal_verification` 而首轮未达 `verified` 时，supervisor 会提取该轮
Lean 工具（formalize / lean_check / prove_by_lemmas / tactic_search）的失败
诊断，注入上下文后在更大预算下重跑一轮。

配置项（`config.toml`，注意是 `escalation_*` 而非旧的 `research_*`）：

```toml
[agent]
escalation_max_react_steps = 24
escalation_max_tool_calls = 16
escalation_replan_rounds = 1

# 可选：把引理/tactic 生成路由到形式化专用模型
# [llm.prover]
# provider = "openai"
# model = "deepseek-prover-v2"
# base_url = "http://localhost:8000/v1"
```

仅剩两个 `research_*` 配置项仍在使用，因沿用旧名未改：
`research_refutation_enabled`（普通模式的命题审查反驳）与
`research_context_max_chars`（历史 trace 的上下文预算）。其余 `research_*`
键已删除；旧 config.toml 里的残留键会被静默忽略，不影响加载。

## 子目标级 HITL（节点操作）

证明图仍作为**内部状态**存在（`ReActTrace.proof_graph`），下面的 HTTP 接口
依然可用，但已没有 UI 入口——前端的证明图面板和研究指挥台页面均已移除，
需直接调用 API：

- **重试此引理**：`POST /api/solve/{session_id}/goals/{goal_id}/actions`，
  body `{"action": "retry", "guidance": "可选提示"}`。重置该目标并级联重置
  所有下游依赖（`ProofGraph.reset_goal(cascade=True)`），随后以 NDJSON 流
  恢复运行。
- **编辑声明**：`{"action": "edit", "statement": "...", "guidance": "..."}`
  改写目标陈述后级联重置并恢复。

约束：仅在 run 非活跃时可操作（活跃返回 409）；每个 checkpoint 的
goal action 只能认领一次（`ProjectStore.claim_goal_action`）。guidance 会
注入恢复运行的上下文，目标本身成为 resumed run 的 active goal。
