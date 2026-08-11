import { useState } from 'react';
import {
  ChevronDown,
  CircleAlert,
  GitBranch,
  PencilLine,
  Play,
  X,
} from 'lucide-react';
import type { HumanDecision, WsHumanInputRequiredEvent } from '@/types/websocket';
import { MathText } from '@/components/MathText';

type PlanGoal = {
  id?: string;
  statement?: string;
  depends_on?: string[];
  status?: string;
};

function ResearchPlanPreview({ goals, rootId }: { goals: PlanGoal[]; rootId?: string }) {
  const [expanded, setExpanded] = useState(false);
  const visibleGoals = expanded ? goals : goals.slice(0, 4);
  const goalLabels = new Map(
    goals.map((goal, index) => [
      goal.id,
      goal.id === rootId ? '主结论' : `L${String(index + 1).padStart(2, '0')}`,
    ]),
  );
  const dependencyCount = goals.reduce(
    (count, goal) => count + (Array.isArray(goal.depends_on) ? goal.depends_on.length : 0),
    0,
  );

  return (
    <div className="border-y border-primary/10 bg-background/70 px-4 py-3.5 sm:px-5">
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <GitBranch size={14} className="text-primary" />
          <span className="text-xs font-semibold">证明路线</span>
        </div>
        <span className="font-mono text-[10px] text-muted-foreground">
          {goals.length} 个目标 · {dependencyCount} 条依赖
        </span>
      </div>
      <ol className="grid gap-2 sm:grid-cols-2">
        {visibleGoals.map((goal, index) => {
          const label = goalLabels.get(goal.id) ?? `L${String(index + 1).padStart(2, '0')}`;
          const dependencies = Array.isArray(goal.depends_on) ? goal.depends_on : [];
          return (
            <li
              key={goal.id || `${goal.statement}-${index}`}
              className="min-w-0 rounded-xl border border-border/80 bg-card px-3 py-2.5 shadow-[0_1px_2px_hsl(var(--foreground)/0.025)]"
            >
              <div className="mb-1.5 flex min-w-0 items-center gap-2">
                <span className="shrink-0 rounded-md bg-primary/10 px-1.5 py-0.5 font-mono text-[9px] font-semibold tracking-wide text-primary">
                  {label}
                </span>
                {dependencies.length > 0 ? (
                  <div className="flex min-w-0 items-center gap-1 overflow-hidden text-[9px] text-muted-foreground">
                    <span className="shrink-0">依赖</span>
                    {dependencies.slice(0, 2).map((dependency) => (
                      <span
                        key={dependency}
                        className="truncate rounded border bg-background px-1 font-mono"
                      >
                        {goalLabels.get(dependency) ?? dependency}
                      </span>
                    ))}
                    {dependencies.length > 2 ? <span>+{dependencies.length - 2}</span> : null}
                  </div>
                ) : (
                  <span className="text-[9px] text-muted-foreground">起始目标</span>
                )}
              </div>
              <div className="text-[11px] leading-[1.55] text-foreground [overflow-wrap:anywhere]">
                <MathText text={goal.statement || '未命名目标'} />
              </div>
            </li>
          );
        })}
      </ol>
      {goals.length > 4 ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-2.5 inline-flex items-center gap-1 rounded-md px-1 py-1 text-[10px] font-semibold text-primary transition-colors hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-expanded={expanded}
        >
          <ChevronDown
            size={12}
            className={`transition-transform ${expanded ? 'rotate-180' : ''}`}
          />
          {expanded ? '收起完整路线' : `展开其余 ${goals.length - 4} 个目标`}
        </button>
      ) : null}
    </div>
  );
}
export function HumanInputCard({
  event,
  onDecision,
}: {
  event: WsHumanInputRequiredEvent;
  onDecision: (event: WsHumanInputRequiredEvent, decision: HumanDecision, feedback: string) => void;
}) {
  const [feedback, setFeedback] = useState('');
  const [feedbackMode, setFeedbackMode] = useState<'edit' | 'respond' | null>(null);
  const details = event.details || {};
  const summary = typeof details.summary === 'string' ? details.summary : '';
  const revised = typeof details.revised_statement === 'string' ? details.revised_statement : '';
  const action =
    details.action && typeof details.action === 'object'
      ? (details.action as { name?: string; args?: unknown })
      : null;
  const graph =
    details.proof_graph && typeof details.proof_graph === 'object'
      ? (details.proof_graph as { root_id?: string; goals?: PlanGoal[] })
      : null;
  const goals = Array.isArray(graph?.goals) ? graph.goals.filter((goal) => goal?.statement) : [];
  const decide = (decision: HumanDecision) => onDecision(event, decision, feedback.trim());
  const isPlanReview = event.kind === 'plan_review';
  const isResearchDecision =
    isPlanReview ||
    event.kind === 'budget_extend' ||
    event.kind === 'reviewer_block' ||
    goals.length > 0;
  const approveLabel =
    event.kind === 'reviewer_block'
      ? '接受当前结果'
      : event.kind === 'budget_extend'
        ? '延长预算继续'
        : isPlanReview
          ? '按此方案开始研究'
          : event.kind === 'tool_approval'
            ? '批准执行'
            : '批准继续';
  const rejectLabel =
    event.kind === 'budget_extend' ? '结束并收工' : isPlanReview ? '停止本次研究' : '拒绝并停止';

  const openFeedback = (mode: 'edit' | 'respond') => {
    setFeedback('');
    setFeedbackMode(mode);
  };

  return (
    <section
      className="relative my-4 overflow-hidden rounded-2xl border border-primary/25 bg-card shadow-[0_16px_38px_-28px_hsl(var(--primary)/0.55)]"
      aria-label="需要你的决定"
    >
      <div className="absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r from-primary via-primary/70 to-transparent" />
      <div className="flex items-start gap-3 px-4 pb-3.5 pt-4 sm:px-5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary/15 bg-primary/10 text-primary">
          {isResearchDecision ? <GitBranch size={17} /> : <CircleAlert size={17} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <div className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-primary">
              {isResearchDecision ? '研究检查点' : '需要你的决定'}
            </div>
            {isPlanReview ? (
              <span className="rounded-full border border-primary/15 bg-primary/5 px-2 py-0.5 text-[9px] font-medium text-primary">
                规划完成
              </span>
            ) : null}
            {goals.length > 0 ? (
              <span className="rounded-full border bg-background px-2 py-0.5 font-mono text-[9px] text-muted-foreground">
                {goals.length} 个目标
              </span>
            ) : null}
          </div>
          <div className="mt-1.5 text-sm font-semibold leading-relaxed">{event.question}</div>
          {action?.name ? (
            <div className="mt-2 rounded-lg border bg-background px-3 py-2 text-xs">
              待执行工具：
              <span className="font-mono font-semibold">{action.name}</span>
            </div>
          ) : null}
          {summary ? <div className="mt-2 text-xs text-muted-foreground">{summary}</div> : null}
          {typeof details.blocked_reason === 'string' && details.blocked_reason ? (
            <div className="mt-2 text-xs text-muted-foreground">卡点：{details.blocked_reason}</div>
          ) : null}
          {event.kind === 'budget_extend' ? (
            <div className="mt-2 rounded-lg border bg-background px-3 py-2 text-xs text-muted-foreground">
              已延长 {Number(details.extensions_used) || 0}/{Number(details.extensions_cap) || 0} 次
              {typeof details.next_wall_seconds === 'number'
                ? ` · 下一段约 ${Math.round(details.next_wall_seconds)}s / ${Number(details.next_iterations) || 0} 轮`
                : null}
              {typeof details.proved_count === 'number'
                ? ` · 已证引理 ${details.proved_count}`
                : null}
            </div>
          ) : null}
          {revised ? (
            <div className="mt-2 rounded-lg border bg-background px-3 py-2 text-xs">
              建议修订：{revised}
            </div>
          ) : null}
        </div>
      </div>
      {goals.length > 0 ? <ResearchPlanPreview goals={goals} rootId={graph?.root_id} /> : null}
      <div className="px-4 py-3.5 sm:px-5">
        {feedbackMode ? (
          <div className="mb-3 rounded-xl border border-primary/15 bg-primary/[0.035] p-3">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div className="flex items-center gap-1.5 text-[10px] font-semibold text-foreground">
                <PencilLine size={12} className="text-primary" />
                {feedbackMode === 'edit' ? '告诉 Conjecta 如何调整方案' : '补充研究信息'}
              </div>
              <button
                type="button"
                onClick={() => setFeedbackMode(null)}
                className="rounded-md p-1 text-muted-foreground hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label="关闭修改意见"
              >
                <X size={13} />
              </button>
            </div>
            <textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder={
                feedbackMode === 'edit'
                  ? '例如：先尝试组合证明；把形式化验证留到最后两个引理…'
                  : '写下希望研究过程额外考虑的条件或线索…'
              }
              rows={3}
              autoFocus
              className="w-full resize-y rounded-lg border bg-background px-3 py-2 text-xs leading-relaxed outline-none focus:ring-2 focus:ring-ring"
            />
            <button
              type="button"
              onClick={() => decide(feedbackMode)}
              disabled={!feedback.trim()}
              className="mt-2 inline-flex items-center rounded-lg bg-primary px-3 py-2 text-[10px] font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {feedbackMode === 'edit' ? '提交修改方案' : '补充后继续'}
            </button>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          {event.allowed_decisions.includes('approve') ? (
            <button
              type="button"
              onClick={() => decide('approve')}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-[10px] font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <Play size={12} fill="currentColor" /> {approveLabel}
            </button>
          ) : null}
          {event.kind !== 'tool_approval' &&
          event.kind !== 'budget_extend' &&
          event.allowed_decisions.includes('edit') &&
          feedbackMode !== 'edit' ? (
            <button
              type="button"
              onClick={() => openFeedback('edit')}
              className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-2 text-[10px] font-semibold transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <PencilLine size={12} /> 调整方案
            </button>
          ) : null}
          {event.kind !== 'budget_extend' &&
          event.allowed_decisions.includes('respond') &&
          feedbackMode !== 'respond' ? (
            <button
              type="button"
              onClick={() => openFeedback('respond')}
              className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-2 text-[10px] font-semibold transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              补充线索
            </button>
          ) : null}
          {event.allowed_decisions.includes('reject') ? (
            <button
              type="button"
              onClick={() => decide('reject')}
              className="ml-auto rounded-lg px-2.5 py-2 text-[10px] font-semibold text-muted-foreground transition-colors hover:bg-destructive/5 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {rejectLabel}
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
