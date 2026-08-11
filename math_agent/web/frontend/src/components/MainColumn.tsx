import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ChatStage } from './ChatStage';
import { Composer } from './Composer';
import { AnswerCard } from './AnswerCard';
import { CopyButton } from './CopyButton';
import { MathText } from './MathText';
import { finalAnswerFromEvents, useSolveSocket } from '@/hooks/useSolveSocket';
import { useProject, queryKeys } from '@/api/queries';
import { getSolveStatus, getSolveTrace } from '@/api/solve';
import { isQuotaExceededMessage } from '@/lib/publicError';
import { findPendingSolveTurn } from '@/lib/pendingSolve';
import { useUiStore } from '@/store/ui';
import { groupTurnsIntoConversations } from './ExplorerPanel';
import type { HumanDecision, SolveRequest, WsEvent, WsHumanInputRequiredEvent } from '@/types/websocket';
import type { FeedbackOutcome } from '@/types/feedback';

export interface Turn {
  role: 'user' | 'assistant';
  text: string;
}

function newConversationId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `conversation-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sanitizeText(text: string): string {
  return text.replace(/\u0000/g, '');
}

function withAssistantAnswer(turns: Turn[], answer: string): Turn[] {
  const text = sanitizeText(answer);
  if (!text) return turns;
  if (turns.length > 0 && turns[turns.length - 1].role === 'assistant') {
    return [...turns.slice(0, -1), { role: 'assistant', text }];
  }
  return [...turns, { role: 'assistant', text }];
}

function extractedProblemFromEvents(events: WsEvent[]): string {
  const event = [...events].reverse().find((item) => item.type === 'problem_extracted');
  if (!event || !('problem' in event) || typeof event.problem !== 'string') return '';
  return sanitizeText(event.problem).trim();
}

export function MainColumn() {
  const {
    events,
    connectionError,
    clear,
    sendProblem,
    submitHumanDecision,
    resumeCheckpoint,
    interrupt,
    markBackground,
    replayEvents,
    status,
    backgroundSessionId,
    sessionId,
  } = useSolveSocket();
  const [turns, setTurns] = useState<Turn[]>([]);
  const recordedDoneRef = useRef(false);
  const recordedTurnStartedRef = useRef<string | null>(null);
  /** Session re-attached via status polling (no local stream); cleared on finish. */
  const recoveredSessionRef = useRef<string | null>(null);
  const [backgroundStartedAt, setBackgroundStartedAt] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const {
    selectedProjectId,
    selectedOwnerUserId,
    selectedConversationId,
    chatResetKey,
    openUsageDialog,
  } = useUiStore();
  const { data: projectData } = useProject(selectedProjectId, selectedOwnerUserId);

  useEffect(() => {
    setTurns([]);
    recordedDoneRef.current = false;
    recordedTurnStartedRef.current = null;
    recoveredSessionRef.current = null;
    setBackgroundStartedAt(null);
    clear();
  }, [chatResetKey, clear]);

  useEffect(() => {
    if (isQuotaExceededMessage(connectionError)) {
      openUsageDialog('quota_exceeded');
    }
  }, [connectionError, openUsageDialog]);

  useEffect(() => {
    if (!selectedConversationId || !projectData?.turns || status !== 'idle') return;
    const conversation = groupTurnsIntoConversations(projectData.turns)
      .find((item) => item.id === selectedConversationId);
    if (!conversation) return;
    recordedDoneRef.current = true;
    setTurns(conversation.turns.flatMap((turn) => [
      { role: 'user' as const, text: sanitizeText(turn.problem) },
      ...(turn.answer
        ? [{ role: 'assistant' as const, text: sanitizeText(turn.answer) }]
        : []),
    ]));
  }, [selectedConversationId, projectData, status]);

  // Async-solve recovery: after a refresh or conversation switch, if the
  // latest turn of this conversation has no answer but a live session, enter
  // background-polling mode instead of showing a blank composer.
  useEffect(() => {
    if (status !== 'idle' || !selectedConversationId || !projectData?.turns) return undefined;
    const pending = findPendingSolveTurn(projectData.turns, selectedConversationId);
    if (!pending?.session_id) return undefined;
    const pendingSessionId = pending.session_id;
    let cancelled = false;
    getSolveStatus(pendingSessionId)
      .then((data) => {
        if (cancelled || (!data.active && !data.waiting_human)) return;
        recoveredSessionRef.current = pendingSessionId;
        setBackgroundStartedAt(pending.created_at ?? null);
        markBackground(pendingSessionId);
      })
      .catch(() => {
        // No live task or checkpoint for this session — nothing to recover.
      });
    return () => {
      cancelled = true;
    };
  }, [status, selectedConversationId, projectData, markBackground]);

  useEffect(() => {
    const started = [...events].reverse().find((event) => event.type === 'turn_started') as
      | { turn_id?: string }
      | undefined;
    const turnId = typeof started?.turn_id === 'string' ? started.turn_id : null;
    if (!turnId || recordedTurnStartedRef.current === turnId) return;
    recordedTurnStartedRef.current = turnId;
    void queryClient.invalidateQueries({ queryKey: queryKeys.project(selectedProjectId) });
  }, [events, queryClient, selectedProjectId]);

  useEffect(() => {
    if (status === 'done' && !recordedDoneRef.current) {
      recordedDoneRef.current = true;
      const answer = finalAnswerFromEvents(events);
      if (answer) {
        setTurns((prev) => withAssistantAnswer(prev, answer));
      }
      // Keep the live ChatStage as the answer surface so the thinking process
      // remains available as a collapsed section instead of disappearing.
      queryClient.invalidateQueries({ queryKey: queryKeys.project(selectedProjectId) });
    }
  }, [status, events, queryClient, selectedProjectId]);

  useEffect(() => {
    if (status !== 'background' || !backgroundSessionId) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await getSolveStatus(backgroundSessionId);
        if (cancelled) return;
        if (data.waiting_human) {
          resumeCheckpoint(backgroundSessionId);
          return;
        }
        if (!data.active) {
          await queryClient.invalidateQueries({
            queryKey: queryKeys.project(selectedProjectId),
          });
          if (cancelled) return;
          // A recovered (re-attached) session has no local stream to preserve:
          // drop back to idle so the reloaded history shows the new answer.
          if (recoveredSessionRef.current === backgroundSessionId) {
            recoveredSessionRef.current = null;
            setBackgroundStartedAt(null);
            clear();
          }
          return;
        }
        // Still running: replay the detached task's trace so the live process
        // stays visible after a page refresh / conversation switch.
        getSolveTrace(backgroundSessionId)
          .then((traceEvents) => {
            if (!cancelled) replayEvents(traceEvents);
          })
          .catch(() => {
            // Trace may be unavailable (404 or network error) — keep polling.
          });
      } catch {
        // Keep polling; transient network errors are expected after disconnect.
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [status, backgroundSessionId, queryClient, selectedProjectId, resumeCheckpoint, replayEvents, clear]);

  useEffect(() => {
    const extractedProblem = extractedProblemFromEvents(events);
    if (!extractedProblem) return;
    setTurns((previous) => {
      let latestUserIndex = -1;
      for (let index = previous.length - 1; index >= 0; index -= 1) {
        if (previous[index].role === 'user') {
          latestUserIndex = index;
          break;
        }
      }
      if (latestUserIndex < 0 || previous[latestUserIndex].text === extractedProblem) {
        return previous;
      }
      const updated = [...previous];
      updated[latestUserIndex] = { role: 'user', text: extractedProblem };
      return updated;
    });
  }, [events]);

  const handleSendProblem = (req: SolveRequest) => {
    const answer = finalAnswerFromEvents(events);
    const historyTurns = answer ? withAssistantAnswer(turns, answer) : turns;
    const conversationId = selectedConversationId || newConversationId();
    recordedDoneRef.current = false;
    recordedTurnStartedRef.current = null;
    recoveredSessionRef.current = null;
    setBackgroundStartedAt(null);
    if (!selectedConversationId) {
      // This is the live conversation, not a navigation event; do not reset it.
      useUiStore.setState({ selectedConversationId: conversationId });
    }
    sendProblem({
      ...req,
      conversation_id: conversationId,
      conversation_history: historyTurns.map((t) => ({ role: t.role, text: t.text })),
    });
    setTurns([...historyTurns, { role: 'user', text: req.problem }]);
  };

  const handleHumanDecision = (
    event: WsHumanInputRequiredEvent,
    decision: HumanDecision,
    feedback: string,
  ) => {
    submitHumanDecision(event.checkpoint_id, {
      request_id: event.request_id,
      decision,
      feedback,
    });
  };

  const handleRefreshBackground = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.project(selectedProjectId) });
    if (backgroundSessionId) {
      void getSolveStatus(backgroundSessionId).catch(() => {});
    }
  };

  const liveAnswer = status === 'done' || events.some((e) => e.type === 'done');
  // Current round's answer lives in ChatStage; omit the trailing assistant turn to avoid duplication.
  const conversationTurns =
    liveAnswer && turns.length > 0 && turns[turns.length - 1].role === 'assistant'
      ? turns.slice(0, -1)
      : turns;
  const showLiveStage = events.length > 0 || status !== 'idle';
  const showHistory = conversationTurns.length > 0;
  // Empty stage: no history, no live events — the composer moves up into the
  // centered hero instead of sitting in the footer.
  const isEmptyStage = !showHistory && !showLiveStage;
  const problemPreview =
    [...turns].reverse().find((turn) => turn.role === 'user')?.text || '';
  const feedbackOutcome: FeedbackOutcome | null =
    status === 'done'
      ? 'completed'
      : status === 'error' || status === 'interrupted'
        ? 'failed'
        : null;
  const answerFeedback =
    feedbackOutcome == null
      ? null
      : {
          outcome: feedbackOutcome,
          sessionId,
          problemPreview,
        };

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div
        data-testid="conversation-scroll"
        data-conversation-scroll=""
        className="flex flex-1 flex-col overflow-auto scrollbar-thin"
      >
        {showHistory && (
          <div
            data-testid="conversation-history"
            className="bg-card/40 px-4 py-3 sm:px-6"
          >
            <div className="mx-auto max-w-[860px]">
              {conversationTurns.map((turn, i) =>
                turn.role === 'user' ? (
                  <div key={i} className="group my-2 text-[15px] font-semibold text-foreground">
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="text-xs uppercase tracking-wide text-muted-foreground">
                        你的问题
                      </span>
                      {turn.text ? (
                        <CopyButton text={turn.text} label="复制问题" />
                      ) : null}
                    </div>
                    <div>
                      <MathText text={turn.text} />
                    </div>
                  </div>
                ) : (
                  <AnswerCard key={i} text={turn.text} />
                ),
              )}
            </div>
          </div>
        )}
        {(!showHistory || showLiveStage) && (
          <ChatStage
            events={events}
            connectionError={connectionError}
            onDismissError={clear}
            status={status}
            onHumanDecision={handleHumanDecision}
            onResumeCheckpoint={resumeCheckpoint}
            goalSummary={problemPreview}
            backgroundSessionId={backgroundSessionId}
            backgroundStartedAt={backgroundStartedAt}
            onRefreshBackground={handleRefreshBackground}
            sharedScroll
            feedback={answerFeedback}
            composerSlot={
              isEmptyStage ? (
                <Composer variant="hero" sendProblem={handleSendProblem} interrupt={interrupt} status={status} />
              ) : null
            }
          />
        )}
      </div>
      {isEmptyStage ? null : (
        <Composer sendProblem={handleSendProblem} interrupt={interrupt} status={status} />
      )}
    </div>
  );
}
