import { useRef, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ChevronRight, Loader2, Search } from 'lucide-react';
import type { HumanDecision, WsEvent, WsHumanInputRequiredEvent, WsInterruptedEvent } from '@/types/websocket';
import type { Status } from '@/hooks/useSolveSocket';
import { HumanInputCard } from './research/HumanInputCard';
import { ConnectionBanner } from './ConnectionBanner';
import { AnswerFeedback } from './AnswerFeedback';
import { BrandMark } from './BrandMark';
import { FloatingMathSymbols } from './FloatingMathSymbols';
import { PhaseStepper } from './PhaseStepper';
import { TraceItem } from './TraceItem';
import { formatElapsedSeconds } from '@/lib/time';
import { derivePhaseStepper, type PhaseStepperState } from '@/lib/phaseStepper';
import {
  buildTraceDisplay,
  humanizeAction,
  humanizeStage,
  lastRunningTool,
  latestTraceSummary,
  type TraceDisplayItem,
} from '@/lib/traceDisplay';
import { isSearchTool } from '@/lib/toolRender';

function EmptyState({ composerSlot }: { composerSlot?: ReactNode }) {
  return (
    <div className="relative mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-4 py-10 text-center duration-500 animate-in fade-in">
      <FloatingMathSymbols />
      <div className="relative flex w-full flex-col items-center">
        <div className="mb-7 flex h-16 w-16 items-center justify-center rounded-2xl border border-primary/15 bg-card/80 shadow-[0_18px_40px_-24px_hsl(var(--primary)/0.6)] backdrop-blur-sm">
          <BrandMark className="h-8 w-8" />
        </div>
        <p className="eyebrow mb-4">Proof Workbench</p>
        <h1 className="text-balance font-display text-[2.6rem] font-normal leading-[1.06] tracking-[-0.02em] sm:text-[3.4rem]">
          证明，或<span className="italic text-primary">证伪</span>。
        </h1>
        {composerSlot ? <div className="mt-10 w-full max-w-[720px] text-left">{composerSlot}</div> : null}
      </div>
    </div>
  );
}

/** Ticking elapsed-seconds counter; resets whenever `active` goes false. */
function useElapsedSeconds(active: boolean, startedAt?: string | null): number {
  const [seconds, setSeconds] = useState(0);
  const sinceRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active) {
      sinceRef.current = null;
      setSeconds(0);
      return undefined;
    }
    if (sinceRef.current == null) {
      const parsed = startedAt ? Date.parse(startedAt) : Number.NaN;
      sinceRef.current = Number.isNaN(parsed) ? Date.now() : parsed;
    }
    const tick = () => {
      setSeconds(Math.max(0, (Date.now() - (sinceRef.current ?? Date.now())) / 1000));
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAt]);

  return seconds;
}

function StatusBar({
  activeStage,
  isGenerating,
  isBusy,
  runningTool,
  elapsedSeconds,
  phaseStepper,
  goalSummary,
}: {
  activeStage: { stage: string; message?: string } | null;
  isGenerating: boolean;
  isBusy: boolean;
  runningTool: string | null;
  elapsedSeconds: number;
  phaseStepper: PhaseStepperState | null;
  goalSummary?: string;
}) {
  if (!isBusy && !activeStage && !phaseStepper) return null;

  const searching = runningTool !== null && isSearchTool(runningTool);
  const text = activeStage?.message
    || (runningTool
      ? (searching ? `正在搜索：${humanizeAction(runningTool)}…` : `正在调用 ${humanizeAction(runningTool)}`)
      : null)
    || (activeStage ? humanizeStage(activeStage.stage) : null)
    || (phaseStepper?.finished ? '求解完成' : null)
    || '正在思考…';

  return (
    <div className="sticky top-0 z-10 border-b border-border/60 bg-background/70 px-4 py-2.5 text-xs backdrop-blur-2xl">
      <div className="mx-auto max-w-[860px]">
      {goalSummary ? (
        <p className="mb-1.5 truncate text-[12px] font-medium text-foreground/80" title={goalSummary}>
          <span className="eyebrow mr-2 align-middle">目标</span>
          {goalSummary}
        </p>
      ) : null}
      <div className="flex items-center gap-2">
      {(isBusy || isGenerating) && (
        searching
          ? <Search size={14} className="animate-pulse text-muted-foreground" />
          : <Loader2 size={14} className="animate-spin text-muted-foreground" />
      )}
      <span className={`font-medium ${isBusy || isGenerating ? 'animate-pulse' : ''}`}>{text}</span>
      {phaseStepper ? (
        <span className="mx-auto hidden sm:block">
          <PhaseStepper state={phaseStepper} />
        </span>
      ) : null}
      {isBusy ? (
        <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
          {formatElapsedSeconds(elapsedSeconds)}
        </span>
      ) : null}
      </div>
      </div>
    </div>
  );
}

function ProcessTrace({
  items,
  collapsed,
  onToggle,
  summary,
}: {
  items: TraceDisplayItem[];
  collapsed: boolean;
  onToggle: () => void;
  /** One-line status shown while collapsed, so the log can stay closed. */
  summary?: string;
}) {
  if (items.length === 0) return null;

  return (
    <div className="surface mb-5 overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
        aria-expanded={!collapsed}
      >
        <ChevronRight
          size={14}
          className={`shrink-0 text-primary transition-transform duration-300 ${collapsed ? '' : 'rotate-90'}`}
        />
        <span className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.14em] text-foreground">
          推理与验证
        </span>
        {collapsed && summary ? (
          <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
            <span aria-hidden="true" className="mx-1.5 opacity-40">·</span>
            {summary}
          </span>
        ) : null}
        <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums opacity-55">
          {items.length} 步
        </span>
      </button>
      {!collapsed && (
        <div className="proof-spine border-t border-border/60 px-4 py-3 duration-200 animate-in fade-in slide-in-from-top-1">
          {items.map((item) => (
            <TraceItem key={item.id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

export function ChatStage({
  events,
  connectionError,
  onDismissError,
  status = 'idle',
  onHumanDecision,
  onResumeCheckpoint,
  goalSummary,
  backgroundSessionId,
  backgroundStartedAt,
  onRefreshBackground,
  sharedScroll = false,
  feedback = null,
  composerSlot = null,
}: {
  events: WsEvent[];
  connectionError: string | null;
  onDismissError: () => void;
  status?: Status;
  onHumanDecision?: (event: WsHumanInputRequiredEvent, decision: HumanDecision, feedback: string) => void;
  onResumeCheckpoint?: (checkpointId: string) => void;
  /** One-line current-goal summary pinned above the status row. */
  goalSummary?: string;
  backgroundSessionId?: string | null;
  /** ISO timestamp (turn.created_at) used for the background elapsed ticker. */
  backgroundStartedAt?: string | null;
  onRefreshBackground?: () => void;
  /** When true, parent owns the scrollport (full session history + live stage). */
  sharedScroll?: boolean;
  /** Optional post-answer feedback prompt (no modal). */
  feedback?: {
    outcome: 'completed' | 'failed';
    sessionId: string | null;
    problemPreview: string;
  } | null;
  /** Centered hero input, rendered only while the stage is empty. */
  composerSlot?: ReactNode;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const { processItems, answerItems, activeStage, isGenerating } = useMemo(
    () => buildTraceDisplay(events),
    [events],
  );
  const isBusy = status === 'connecting' || status === 'streaming' || isGenerating;
  const runningTool = useMemo(() => lastRunningTool(processItems), [processItems]);
  const processSummary = useMemo(() => latestTraceSummary(processItems), [processItems]);
  const phaseStepper = useMemo(() => derivePhaseStepper(events), [events]);
  const elapsedSeconds = useElapsedSeconds(isBusy);
  const backgroundElapsed = useElapsedSeconds(status === 'background', backgroundStartedAt);
  // The process log stays collapsed by default — during the run the sticky
  // status bar is the only live signal; details expand on demand.
  const [processOpen, setProcessOpen] = useState(false);

  // Re-pin to bottom when a fresh run begins (events reset to empty).
  useEffect(() => {
    if (events.length === 0) stickToBottomRef.current = true;
  }, [events.length]);

  // Track whether the user is pinned to the bottom; only auto-scroll if so,
  // so scrolling up to re-read mid-stream isn't fought by new content.
  const updateStickToBottom = (el: HTMLElement) => {
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distanceFromBottom < 80;
  };

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    updateStickToBottom(el);
  };

  useEffect(() => {
    if (!sharedScroll) return undefined;
    const el = bottomRef.current?.closest('[data-conversation-scroll]') as HTMLElement | null;
    if (!el) return undefined;
    const handleScroll = () => updateStickToBottom(el);
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, [sharedScroll, events.length]);

  useEffect(() => {
    if (!stickToBottomRef.current) return;
    if (typeof bottomRef.current?.scrollIntoView === 'function') {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [processItems, answerItems, isBusy, processOpen]);

  const showEmpty = processItems.length === 0 && answerItems.length === 0 && !isBusy && status !== 'background';
  const pendingInteraction = status === 'waiting_human'
    ? [...events].reverse().find((event): event is WsHumanInputRequiredEvent => event.type === 'human_input_required')
    : undefined;
  const interrupted = status === 'interrupted'
    ? [...events].reverse().find((event): event is WsInterruptedEvent => (
        event.type === 'interrupted'
        && typeof (event as WsInterruptedEvent).checkpoint_id === 'string'
      ))
    : undefined;
  // Research mode is gone: every solve streams the same flat process log.

  return (
    <main
      className={
        sharedScroll
          ? `flex flex-col bg-transparent ${showEmpty ? 'min-h-full flex-1' : ''}`
          : 'flex flex-1 flex-col overflow-hidden bg-transparent'
      }
    >
      {connectionError && <ConnectionBanner message={connectionError} onClose={onDismissError} />}
      <StatusBar
        activeStage={activeStage}
        isGenerating={isGenerating}
        isBusy={isBusy}
        runningTool={runningTool}
        elapsedSeconds={elapsedSeconds}
        phaseStepper={phaseStepper}
        goalSummary={goalSummary}
      />
      {status === 'background' ? (
        <div className="border-b border-amber-500/20 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-900 dark:text-amber-100">
          <div className="mx-auto flex max-w-[860px] flex-wrap items-center gap-2">
            <Loader2 size={14} className="animate-spin" />
            <span>
              后台求解中
              {backgroundSessionId ? `（会话 ${backgroundSessionId.slice(0, 8)}…）` : ''}
              {backgroundStartedAt ? ` · 已用时 ${formatElapsedSeconds(backgroundElapsed)}` : ''}
              。完成后结果会写入对话。
            </span>
            {onRefreshBackground ? (
              <button
                type="button"
                onClick={onRefreshBackground}
                className="ml-auto rounded-md border border-amber-500/30 bg-card px-2 py-1 text-[11px] font-semibold"
              >
                刷新状态
              </button>
            ) : null}
            {backgroundSessionId && onResumeCheckpoint ? (
              <button
                type="button"
                onClick={() => onResumeCheckpoint(backgroundSessionId)}
                className="rounded-md border border-amber-500/30 bg-card px-2 py-1 text-[11px] font-semibold"
              >
                尝试重新接入
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
      <div
        ref={sharedScroll ? undefined : scrollRef}
        onScroll={sharedScroll ? undefined : onScroll}
        className={
          sharedScroll
            ? 'px-4 py-5 sm:px-6'
            : 'flex-1 overflow-auto px-4 py-5 scrollbar-thin sm:px-6'
        }
      >
        <div className={`mx-auto max-w-[860px] ${showEmpty ? 'h-full' : ''}`}>
        {showEmpty && <EmptyState composerSlot={composerSlot} />}
        <ProcessTrace
          items={processItems}
          collapsed={!processOpen}
          onToggle={() => setProcessOpen((open) => !open)}
          summary={processSummary}
        />
        {answerItems.map((item) => (
          <TraceItem key={item.id} item={item} />
        ))}
        {pendingInteraction && onHumanDecision ? (
          <HumanInputCard event={pendingInteraction} onDecision={onHumanDecision} />
        ) : null}
        {interrupted && interrupted.type === 'interrupted' && interrupted.resumable && interrupted.checkpoint_id && onResumeCheckpoint ? (
          <button type="button" onClick={() => onResumeCheckpoint(interrupted.checkpoint_id!)} className="my-3 rounded-lg border bg-card px-3 py-2 text-xs font-semibold text-primary">
            从 checkpoint 继续
          </button>
        ) : null}
        {feedback ? (
          <AnswerFeedback
            key={feedback.sessionId || `${feedback.outcome}-${feedback.problemPreview.slice(0, 40)}`}
            outcome={feedback.outcome}
            sessionId={feedback.sessionId}
            problemPreview={feedback.problemPreview}
          />
        ) : null}
        <div ref={bottomRef} />
        </div>
      </div>
    </main>
  );
}
