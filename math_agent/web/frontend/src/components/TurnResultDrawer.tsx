import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import { queryKeys, useProject } from '@/api/queries';
import { useUiStore } from '@/store/ui';
import { summarizeArgsPreview } from '@/lib/toolRender';
import type { ToolDisplay } from '@/lib/traceDisplay';
import type { ToolEvidence } from '@/types/websocket';
import { MathText } from './MathText';
import { PublishCardDrawer } from './PublishCardDrawer';
import { ToolTraceCard } from './TraceItem';

/** Adapt a persisted tool_evidence entry to the live-trace ToolDisplay shape
 * (name → tool, output_preview → output, duration_seconds → durationMs). */
function toToolDisplay(item: ToolEvidence, index: number): ToolDisplay {
  return {
    stepNum: index,
    tool: item.name,
    running: false,
    argsPreview: item.args_preview ? summarizeArgsPreview(item.args_preview) : undefined,
    argsRaw: item.args_preview,
    output: item.output_preview,
    success: item.success,
    durationMs: typeof item.duration_seconds === 'number'
      ? item.duration_seconds * 1000
      : undefined,
  };
}

function ToolEvidenceList({ evidence }: { evidence: ToolEvidence[] }) {
  if (evidence.length === 0) return null;
  return (
    <div className="rounded-xl border bg-card/70 px-4 py-3 shadow-sm">
      <div className="mb-2 text-xs font-semibold text-foreground">验证证据</div>
      <div className="space-y-1.5">
        {evidence.map((item, index) => (
          <ToolTraceCard key={index} data={toToolDisplay(item, index)} />
        ))}
      </div>
    </div>
  );
}

function LeanProofs({ proofs }: { proofs: string[] }) {
  const [collapsed, setCollapsed] = useState(true);
  if (proofs.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-xl border bg-card/70 shadow-sm">
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center gap-1.5 px-4 py-3 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
        aria-expanded={!collapsed}
      >
        <ChevronRight
          size={14}
          className={`shrink-0 transition-transform duration-200 ${collapsed ? '' : 'rotate-90'}`}
        />
        <span className="font-semibold text-foreground">Lean 证明</span>
        <span className="opacity-70">({proofs.length})</span>
        <span className="ml-auto opacity-70">{collapsed ? '已折叠 · 点击展开' : '点击收起'}</span>
      </button>
      {!collapsed && (
        <div className="space-y-2 border-t px-4 py-3 duration-200 animate-in fade-in slide-in-from-top-1">
          {proofs.map((proof, index) => (
            <pre
              key={index}
              className="overflow-x-auto rounded-lg bg-muted/50 p-3 text-xs leading-relaxed"
            >
              <code>{proof}</code>
            </pre>
          ))}
        </div>
      )}
    </div>
  );
}

export function TurnResultDrawer() {
  const {
    resultDrawerTurnId,
    closeResultDrawer,
    selectedProjectId,
    selectedOwnerUserId,
  } = useUiStore();
  const { data: projectData } = useProject(selectedProjectId, selectedOwnerUserId);
  const queryClient = useQueryClient();
  const [publishing, setPublishing] = useState(false);

  const turn = resultDrawerTurnId
    ? projectData?.turns?.find((item) => item.id === resultDrawerTurnId)
    : undefined;
  if (!turn) return null;

  const attachments = (turn.attachments ?? []).filter((item) => item.name);
  const issues = turn.verification_issues ?? [];

  return (
    <>
      <div className="fixed inset-0 z-50 flex justify-end bg-black/30">
        <div className="h-full w-full max-w-lg overflow-y-auto bg-background p-6 shadow-xl">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-bold">轮次结论</h2>
            <button type="button" aria-label="关闭" onClick={closeResultDrawer}>×</button>
          </div>
          <div className="space-y-4">
            <div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                问题
              </div>
              <div className="text-sm leading-relaxed">
                <MathText text={turn.problem} />
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                结论
              </div>
              <div className="text-sm leading-relaxed">
                <MathText text={turn.answer} />
              </div>
            </div>
            <LeanProofs proofs={turn.lean_proofs ?? []} />
            <ToolEvidenceList evidence={turn.tool_evidence ?? []} />
            {issues.length > 0 ? (
              <div className="rounded-xl border border-destructive/25 bg-destructive/5 px-4 py-3">
                <div className="mb-1 text-xs font-semibold text-destructive">验证问题</div>
                <ul className="list-inside list-disc space-y-1 text-xs leading-relaxed text-muted-foreground">
                  {issues.map((issue, index) => (
                    <li key={index}>{issue}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {attachments.length > 0 ? (
              <div>
                <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  附件
                </div>
                <ul className="space-y-1 text-xs text-muted-foreground">
                  {attachments.map((item, index) => (
                    <li key={index} className="rounded-md border bg-card px-2 py-1.5">
                      {item.name}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <button
              type="button"
              onClick={() => setPublishing(true)}
              className="w-full rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground"
            >
              发布为知识卡片
            </button>
          </div>
        </div>
      </div>
      {publishing ? (
        <PublishCardDrawer
          projectId={selectedProjectId}
          itemId={turn.id}
          kind="turn"
          defaultTitle={turn.problem.replace(/\s+/g, ' ').trim().slice(0, 80)}
          defaultStatement={turn.problem}
          sourceTurn={{ turnId: turn.id, body: turn.answer }}
          onClose={() => setPublishing(false)}
          onPublished={() => {
            void queryClient.invalidateQueries({
              queryKey: queryKeys.knowledge(selectedProjectId, selectedOwnerUserId),
            });
            void queryClient.invalidateQueries({
              queryKey: queryKeys.knowledgeGraph(selectedProjectId),
            });
          }}
        />
      ) : null}
    </>
  );
}
