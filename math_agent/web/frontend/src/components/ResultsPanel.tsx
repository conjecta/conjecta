import { useMemo } from 'react';
import { BadgeCheck, CircleDashed, CircleAlert, ShieldCheck } from 'lucide-react';
import { useProject } from '@/api/queries';
import { useUiStore } from '@/store/ui';
import { groupTurnsIntoConversations } from './ExplorerPanel';
import type { ProjectTurn } from '@/types/api';

function turnPreview(text: string, max: number): string {
  const oneLine = text.replace(/\s+/g, ' ').trim();
  if (oneLine.length <= max) return oneLine;
  return `${oneLine.slice(0, max - 1)}…`;
}

/** Strip LaTeX delimiters so long answers stay scannable as plain text. */
function stripMathDelimiters(text: string): string {
  return text
    .replace(/\$\$([\s\S]+?)\$\$/g, '$1')
    .replace(/\\\[([\s\S]+?)\\\]/g, '$1')
    .replace(/\$([^$\n]+?)\$/g, '$1')
    .replace(/\\\(([\s\S]+?)\\\)/g, '$1');
}

function verificationBadge(status?: string) {
  switch (status) {
    case 'verified':
      return {
        label: '已验证',
        icon: ShieldCheck,
        className: 'border-success/25 bg-success/10 text-success',
      };
    case 'reviewed':
      return {
        label: '已审阅',
        icon: BadgeCheck,
        className: 'border-primary/25 bg-primary/10 text-primary',
      };
    case 'blocked':
      return {
        label: '受阻',
        icon: CircleAlert,
        className: 'border-destructive/25 bg-destructive/10 text-destructive',
      };
    case 'unreviewed':
    case 'best_effort':
      return {
        label: '未验证',
        icon: CircleDashed,
        className: 'border-border bg-muted/50 text-muted-foreground',
      };
    default:
      return null;
  }
}

function TurnResultCard({ turn, index }: { turn: ProjectTurn; index: number }) {
  const openResultDrawer = useUiStore((state) => state.openResultDrawer);
  const badge = verificationBadge(turn.verification_status);
  const answerSummary = turn.answer ? turnPreview(stripMathDelimiters(turn.answer), 120) : '';

  return (
    <button
      type="button"
      onClick={() => openResultDrawer(turn.id)}
      className="w-full px-2 py-3 text-left transition-colors hover:bg-muted/40"
    >
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          轮 {index}
        </span>
        {badge ? (
          <span
            className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${badge.className}`}
          >
            <badge.icon size={11} />
            {badge.label}
          </span>
        ) : null}
        {turn.strategy ? (
          <span className="inline-flex items-center rounded-md border border-border bg-muted/50 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {turn.strategy === 'research' ? '研究' : '标准'}
          </span>
        ) : null}
      </div>
      <div className="mt-1.5 line-clamp-2 text-sm font-medium leading-snug text-foreground">
        {turnPreview(turn.problem, 40)}
      </div>
      {answerSummary ? (
        <div className="mt-1 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
          {answerSummary}
        </div>
      ) : null}
    </button>
  );
}

export function ResultsPanel({ embedded = false }: { embedded?: boolean }) {
  const { selectedProjectId, selectedOwnerUserId, selectedConversationId } = useUiStore();
  const { data: projectData, isLoading } = useProject(selectedProjectId, selectedOwnerUserId);

  const turns = useMemo(() => {
    if (!projectData?.turns) return [];
    const conversations = groupTurnsIntoConversations(projectData.turns);
    const current = selectedConversationId
      ? conversations.find((item) => item.id === selectedConversationId)
      : undefined;
    // Newest first for display; numbering stays chronological.
    return current ? [...current.turns].reverse() : [];
  }, [projectData, selectedConversationId]);

  return (
    <div className={embedded ? 'flex max-h-72 flex-col' : 'flex min-h-0 flex-1 flex-col'}>
      {!embedded && (
        <div className="flex items-center justify-between border-b px-4 pb-2 pt-3">
          <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            本轮结论
          </span>
          <span className="text-[10px] text-muted-foreground">{turns.length} 轮</span>
        </div>
      )}
      <div className="flex-1 overflow-auto px-3 pb-4 pt-2 scrollbar-thin">
        {isLoading ? (
          <div className="py-4 text-center text-xs text-muted-foreground">加载中…</div>
        ) : turns.length === 0 ? (
          <div className="mx-1 border border-dashed px-4 py-8 text-center text-xs leading-relaxed text-muted-foreground">
            完成一轮求解后，结论会沉淀在这里
          </div>
        ) : (
          <ul className="flex flex-col divide-y divide-border/60" role="list">
            {turns.map((turn, displayIndex) => (
              <li key={turn.id}>
                <TurnResultCard turn={turn} index={turns.length - displayIndex} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
