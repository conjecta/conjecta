import { useState } from 'react';
import { CheckCircle2, ChevronRight, CircleAlert, Loader2, Search, Wrench } from 'lucide-react';
import { AnswerCard } from './AnswerCard';
import type { WsEvent } from '@/types/websocket';
import { MathText } from './MathText';
import { ToolResultBody } from './ToolResultView';
import { formatDurationMs } from '@/lib/time';
import { isSearchTool } from '@/lib/toolRender';
import {
  humanizeAction,
  humanizeStage,
  type ToolDisplay,
  type TraceDisplayItem,
} from '@/lib/traceDisplay';

/** Compact tool-call card (Codex exec-block style): collapsed by default,
 * header shows status icon + name + one-line args + duration; expands to a
 * per-tool rich body (compute code+output, search hit list, or plain text). */
export function ToolTraceCard({ data }: { data: ToolDisplay }) {
  const [open, setOpen] = useState(false);
  const searchTool = isSearchTool(data.tool);

  return (
    <div className="relative my-2 overflow-hidden rounded-lg border bg-card/80 text-xs shadow-sm before:absolute before:-left-[1.58rem] before:top-3.5 before:h-2 before:w-2 before:rounded-full before:bg-muted-foreground/50">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/40"
      >
        <ChevronRight
          size={12}
          className={`shrink-0 text-muted-foreground transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
        />
        {data.running ? (
          <Loader2 size={12} className="shrink-0 animate-spin text-muted-foreground" />
        ) : data.success === false ? (
          <CircleAlert size={12} className="shrink-0 text-destructive" />
        ) : (
          <CheckCircle2 size={12} className="shrink-0 text-success" />
        )}
        {searchTool ? (
          <Search size={12} className="shrink-0 text-muted-foreground/70" />
        ) : (
          <Wrench size={12} className="shrink-0 text-muted-foreground/70" />
        )}
        <span className="shrink-0 font-mono font-medium text-foreground">{humanizeAction(data.tool)}</span>
        {data.running && data.progressMessage ? (
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{data.progressMessage}</span>
        ) : data.argsPreview ? (
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{data.argsPreview}</span>
        ) : (
          <span className="flex-1" />
        )}
        {data.running ? (
          <span className="shrink-0 tabular-nums text-muted-foreground">
            {searchTool
              ? '正在搜索…'
              : data.elapsedSeconds !== undefined
                ? `运行中 · ${formatDurationMs(data.elapsedSeconds * 1000)}`
                : '运行中…'}
          </span>
        ) : data.durationMs !== undefined ? (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
            {formatDurationMs(data.durationMs)}
          </span>
        ) : null}
      </button>
      {open ? (
        <div className="border-t bg-muted/30 px-3 py-2 duration-200 animate-in fade-in slide-in-from-top-1">
          <ToolResultBody
            tool={data.tool}
            argsPreview={data.argsPreview}
            argsRaw={data.argsRaw}
            output={data.output}
            success={data.success}
            running={data.running}
          />
        </div>
      ) : null}
    </div>
  );
}

export function EventBlock({ event }: { event: WsEvent }) {
  const data = event as Record<string, unknown>;
  const text = (value: unknown) => (typeof value === 'string' ? value : '');
  const num = (value: unknown) => (typeof value === 'number' ? value : null);
  const message = text(data.message);
  switch (event.type) {
    case 'llm_start':
    case 'token':
    case 'stage_status':
    case 'step_start':
    case 'step':
    case 'tool_start':
    case 'tool_done':
    case 'turn_started':
    case 'session':
      return null;
    case 'lean':
      return (
        <div className="my-2 border border-border bg-card p-2 text-xs">
          Lean {text(data.status) || 'event'}: {text(data.statement) || text(data.details) || text(data.output)}
        </div>
      );
    case 'lean_verification':
      return (
        <div className={`my-2 border p-2 text-xs ${data.status === 'verified' ? 'border-success text-success' : 'border-destructive text-destructive'}`}>
          Lean {text(data.status)}: {text(data.statement) || text(data.details)}
        </div>
      );
    case 'signal':
      return (
        <div className="my-2 rounded-sm border bg-card px-3 py-2 text-xs">
          <div className="font-medium">{text(data.signal)}</div>
          {text(data.stage) ? <div className="text-muted-foreground">{text(data.stage)}</div> : null}
          {message ? <div className="mt-1 text-muted-foreground">{message}</div> : null}
        </div>
      );
    case 'goal_evaluation':
      return (
        <div className="my-2 rounded-sm border border-blue-500/30 bg-blue-500/5 px-3 py-2 text-xs">
          <div className="font-medium">Goal evaluation{num(data.score) !== null ? ` · ${Math.round((num(data.score) ?? 0) * 100)}%` : ''}</div>
          {text(data.reasoning) ? <div className="mt-1 text-muted-foreground">{text(data.reasoning)}</div> : null}
        </div>
      );
    case 'capability_health': {
      const caps = data.capabilities && typeof data.capabilities === 'object'
        ? data.capabilities as Record<string, { status?: string; error?: string; tool_count?: number }>
        : {};
      const entries = Object.entries(caps);
      if (entries.length === 0) {
        return (
          <div className="my-2 rounded-lg border bg-card/80 px-3 py-2 text-xs text-muted-foreground">
            研究能力探测：当前无外部 MCP 工具
          </div>
        );
      }
      return (
        <div className="my-2 rounded-lg border bg-card/80 px-3 py-2 text-xs" role="region" aria-label="研究能力状态">
          <div className="mb-1.5 font-semibold text-foreground">研究能力</div>
          <div className="flex flex-wrap gap-1.5">
            {entries.map(([name, info]) => {
              const status = String(info?.status || 'unknown');
              const ok = status === 'connected';
              return (
                <span
                  key={name}
                  title={info?.error || status}
                  className={`rounded-md px-2 py-1 font-mono text-[10px] ${
                    ok
                      ? 'bg-success/10 text-success'
                      : 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
                  }`}
                >
                  {name}
                  {typeof info?.tool_count === 'number' ? ` · ${info.tool_count}` : ''}
                  {' · '}
                  {ok ? '可用' : status === 'unavailable' ? '不可用' : status}
                </span>
              );
            })}
          </div>
        </div>
      );
    }
    case 'proof_graph':
      return null;
    case 'checkpoint':
      return (
        <div className="my-2 text-xs text-muted-foreground">
          Checkpoint saved{data.resumable ? '' : ' (not resumable)'}{text(data.reason) ? ` · ${text(data.reason)}` : ''}
        </div>
      );
    case 'human_input_required':
    case 'problem_extracted':
      return null;
    case 'error':
      return <div className="my-2 text-sm text-destructive">Error: {message}</div>;
    case 'interrupted':
      return (
        <div className="my-2 text-xs text-muted-foreground">
          {message}{data.resumable ? ' Resume is available.' : ''}
        </div>
      );
    case 'done': {
      const answerText = text(data.final_answer) || text(data.summary);
      const strategy = text(data.strategy);
      const verification = text(data.verification_status);
      const modeLabel = strategy === 'research' ? '深度研究' : '普通求解';
      const verificationLabels: Record<string, string> = {
        verified: '形式化验证',
        reviewed: '审阅通过',
        unreviewed: '审阅跳过',
        best_effort: '尚未闭合',
        blocked: '验证受阻',
      };
      const isPartial =
        verification === 'best_effort'
        || verification === 'blocked'
        || verification === 'unreviewed';
      const issues = Array.isArray(data.verification_issues)
        ? data.verification_issues.filter((issue): issue is string => typeof issue === 'string')
        : [];
      const displayIssues = Array.from(new Set(issues.map((issue) => {
        if (
          /reviewer\s+\w+\s+was unavailable/i.test(issue)
          || /automated review was temporarily unavailable/i.test(issue)
          || /PermissionDeniedError/i.test(issue)
        ) {
          return '部分自动审阅暂不可用，当前答案未完成全部复核。';
        }
        return issue;
      })));
      return (
        <AnswerCard
          text={answerText}
          title={isPartial ? '阶段性研究结果' : '最终答案'}
          titleClassName={isPartial ? 'text-amber-700 dark:text-amber-300' : 'text-success'}
          className={`duration-300 animate-in fade-in slide-in-from-bottom-1 ${isPartial ? 'border-amber-500/30' : 'border-success/25'}`}
          badges={(
            <>
              <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{modeLabel}</span>
              {verificationLabels[verification] ? (
                <span className={`rounded-md px-1.5 py-0.5 text-[10px] ${isPartial ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300' : 'bg-success/10 text-success'}`}>
                  {verificationLabels[verification]}
                </span>
              ) : null}
            </>
          )}
          footer={
            displayIssues.length > 0 && verification !== 'verified' && verification !== 'reviewed' ? (
              <div className="mt-3 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-muted-foreground">
                <div className="mb-1 font-semibold text-foreground">验证说明</div>
                {displayIssues.slice(0, 3).map((issue) => <div key={issue}>· {issue}</div>)}
              </div>
            ) : null
          }
        />
      );
    }
    default:
      return (
        <div className="my-2 rounded-sm border border-dashed bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
          <div className="font-mono">{event.type}</div>
          {message ? <div className="mt-1">{message}</div> : null}
        </div>
      );
  }
}

export function TraceItem({ item }: { item: TraceDisplayItem }) {
  switch (item.kind) {
    case 'stage':
      return (
    <div className="relative my-3 text-xs text-muted-foreground before:absolute before:-left-[1.72rem] before:top-1 before:h-3 before:w-3 before:rounded-full before:border-2 before:border-background before:bg-primary before:shadow-[0_0_0_1px_hsl(var(--primary)/0.35)]">
          <div className="uppercase tracking-wide">{humanizeStage(item.stage)}</div>
          {item.message ? <div className="mt-0.5 normal-case tracking-normal">{item.message}</div> : null}
        </div>
      );
    case 'generating':
      return (
        <div className="relative my-3 rounded-xl border border-dashed border-primary/30 bg-primary/5 px-4 py-3 text-xs before:absolute before:-left-[1.72rem] before:top-4 before:h-3 before:w-3 before:rounded-full before:border-2 before:border-background before:bg-primary">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 size={12} className="animate-spin" />
            <span className="font-medium">正在规划下一步</span>
            {item.charCount > 0 ? (
              <span
                aria-hidden="true"
                title={`已接收 ${item.charCount} 字符`}
                className="ml-auto h-1 w-16 overflow-hidden rounded-full bg-primary/15"
              >
                <span className="block h-full w-1/3 rounded-full bg-primary/60 motion-safe:animate-pulse" />
              </span>
            ) : null}
          </div>
          {item.thoughtPreview ? (
            <div className="mt-2 text-[13px] text-muted-foreground">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">思考</div>
              <MathText text={item.thoughtPreview} />
            </div>
          ) : (
            <div className="mt-1 text-muted-foreground">正在接收模型输出…</div>
          )}
        </div>
      );
    case 'step':
      return (
        <div className="relative my-3 rounded-xl border bg-card px-4 py-3 text-xs shadow-sm duration-200 before:absolute before:-left-[1.72rem] before:top-4 before:h-3 before:w-3 before:rounded-full before:border-2 before:border-background before:bg-muted-foreground/70 animate-in fade-in slide-in-from-bottom-1">
          <div className="flex items-center gap-2 font-medium">
            <span>步骤 {item.data.stepNum}</span>
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] tracking-wide text-muted-foreground">
              {humanizeAction(item.data.action)}
            </span>
            {item.data.verified === true ? (
              <span className="text-success">已验证</span>
            ) : item.data.verified === false ? (
              <span className="text-destructive">需复核</span>
            ) : null}
          </div>
          {item.data.thought ? (
            <div className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">思考</div>
              <MathText text={item.data.thought} />
            </div>
          ) : null}
          {item.data.observation ? (
            <div className="mt-2 text-[13px] leading-relaxed text-muted-foreground/80">
              <div className="mb-1 text-[10px] uppercase tracking-wide">观察</div>
              <MathText text={item.data.observation} />
            </div>
          ) : null}
          {item.data.verified === false && item.data.reviewIssues?.length ? (
            <div className="mt-2 text-xs text-destructive">
              <div className="mb-1 text-[10px] uppercase tracking-wide">复核原因</div>
              <ul className="list-disc space-y-0.5 pl-4">
                {item.data.reviewIssues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      );
    case 'tool':
      return <ToolTraceCard data={item.data} />;
    case 'memory_retrieval':
      return (
        <div className="my-2 rounded-sm border border-dashed bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
          检索到 {item.count} 条相关知识
        </div>
      );
    case 'event':
      return <EventBlock event={item.event} />;
    default:
      return null;
  }
}
