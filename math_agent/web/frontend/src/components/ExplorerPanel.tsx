import { lazy, Suspense, useMemo, useState } from 'react';
import { useUiStore } from '@/store/ui';
import { useDeleteConversation, useProject, useProjects, useRenameProject } from '@/api/queries';
import { ChevronDown, ChevronRight, Pencil, Plus, Search, Trash2, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ProjectMembersPanel } from '@/components/ProjectMembersPanel';
import type { ProjectConversation, ProjectTurn } from '@/types/api';

// Lazy to avoid an import cycle: ResultsPanel reuses groupTurnsIntoConversations.
const ResultsPanel = lazy(() => import('./ResultsPanel').then((module) => ({
  default: module.ResultsPanel,
})));

function turnPreview(text: string, max = 72): string {
  const oneLine = text.replace(/\s+/g, ' ').trim();
  if (oneLine.length <= max) return oneLine;
  return `${oneLine.slice(0, max - 1)}…`;
}

export function groupTurnsIntoConversations(turns: ProjectTurn[]): ProjectConversation[] {
  const groups = new Map<string, ProjectConversation>();
  turns.forEach((turn) => {
    // Old records predate conversations. Keeping each one as its own conversation
    // preserves every historical answer without guessing at unrelated topics.
    const id = turn.conversation_id || turn.id;
    const existing = groups.get(id);
    if (existing) {
      existing.turns.push(turn);
      existing.updated_at = turn.created_at || existing.updated_at;
      return;
    }
    groups.set(id, {
      id,
      title: turnPreview(turn.problem, 96) || '未命名会话',
      turns: [turn],
      created_at: turn.created_at,
      updated_at: turn.created_at,
    });
  });
  return [...groups.values()];
}

function HistoryItem({
  conversation,
  active,
  confirming,
  busy,
  onSelect,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  conversation: ProjectConversation;
  active: boolean;
  confirming: boolean;
  busy: boolean;
  onSelect: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  return (
    <div
      aria-current={active ? 'true' : undefined}
      className={`group px-2 py-3 transition-colors ${
        active
          ? 'bg-muted/50'
          : 'hover:bg-muted/40'
      }`}
    >
      <button
        type="button"
        onClick={onSelect}
        className="w-full text-left"
      >
        <div className="line-clamp-2 text-sm font-medium leading-snug text-foreground">
          {conversation.title}
        </div>
      </button>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
          <span
            aria-hidden="true"
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
              active
                ? 'bg-primary motion-safe:animate-pulse'
                : conversation.turns.at(-1)?.answer
                  ? 'bg-success'
                  : 'bg-muted-foreground/30'
            }`}
          />
          <span className="truncate">
            {active ? '求解中' : conversation.turns.at(-1)?.answer ? '有结论' : '空'}
            <span className="mx-1 opacity-40">·</span>
            {conversation.turns.length} 轮
          </span>
        </span>
        {confirming ? (
          <div className="flex flex-wrap items-center justify-end gap-1">
            <span className="text-[10px] text-destructive">确认删除？</span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onCancelDelete}
              disabled={busy}
            >
              取消
            </Button>
            <Button
              type="button"
              size="sm"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={onConfirmDelete}
              disabled={busy}
              aria-label="确认删除对话"
            >
              删除
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
            onClick={onRequestDelete}
            disabled={busy}
            aria-label="删除对话"
            title="删除对话"
          >
            <Trash2 size={12} />
          </Button>
        )}
      </div>
    </div>
  );
}

export function ExplorerPanel() {
  const {
    selectedProjectId,
    selectedOwnerUserId,
    selectedConversationId,
    setSelectedConversationId,
    setSelectedProjectId,
    startNewChat,
  } = useUiStore();
  const { data: projectsData } = useProjects();
  const { data: projectData, isLoading } = useProject(selectedProjectId, selectedOwnerUserId);
  const deleteConversation = useDeleteConversation(selectedProjectId);
  const renameProject = useRenameProject();
  const [filter, setFilter] = useState('');
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [renameError, setRenameError] = useState<string | null>(null);
  const [resultsOpen, setResultsOpen] = useState(false);
  const [showMembers, setShowMembers] = useState(false);

  const turns = projectData?.turns ?? [];
  const projects = projectsData?.projects ?? [];
  const selectedProject = projects.find(
    (p) =>
      p.id === selectedProjectId
      && (p.owner_user_id || '') === (selectedOwnerUserId || ''),
  ) ?? projects.find((p) => p.id === selectedProjectId);
  const canRename =
    Boolean(selectedProjectId)
    && (!selectedProject?.role || selectedProject.role === 'lead');
  const currentName = selectedProject?.name || selectedProjectId;

  const conversations = useMemo(() => groupTurnsIntoConversations(turns), [turns]);
  const currentTurnCount = useMemo(() => {
    if (!selectedConversationId) return 0;
    return conversations.find((item) => item.id === selectedConversationId)?.turns.length ?? 0;
  }, [conversations, selectedConversationId]);
  const filteredConversations = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter(
      (conversation) => conversation.turns.some(
        (turn) => turn.problem.toLowerCase().includes(q)
          || (turn.answer || '').toLowerCase().includes(q),
      ),
    );
  }, [conversations, filter]);

  const handleConfirmDelete = async (conversationId: string) => {
    try {
      await deleteConversation.mutateAsync(conversationId);
      if (selectedConversationId === conversationId) {
        startNewChat();
      }
      setConfirmDeleteId(null);
    } catch {
      // Keep confirm UI open so the user can retry.
    }
  };

  const beginRename = () => {
    setDraftName(currentName);
    setRenameError(null);
    setRenaming(true);
  };

  const cancelRename = () => {
    setRenaming(false);
    setDraftName('');
    setRenameError(null);
  };

  const handleRename = async () => {
    const next = draftName.trim();
    if (!next || next === currentName) {
      cancelRename();
      return;
    }
    setRenameError(null);
    try {
      await renameProject.mutateAsync({
        projectId: selectedProjectId,
        name: next,
        ownerUserId: selectedOwnerUserId,
      });
      cancelRename();
    } catch (e) {
      setRenameError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 pb-2 pt-4">
        <h2 className="eyebrow">对话</h2>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setShowMembers(true)}
            aria-label="项目成员"
            title="项目成员"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <Users size={14} />
          </button>
          <button
            type="button"
            onClick={startNewChat}
            aria-label="新对话"
            title="新对话"
            className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <Plus size={14} />
          </button>
        </div>
      </div>

      {projects.length > 0 && (
        <div className="px-4 pb-2">
          {renaming ? (
            <div className="space-y-1.5">
              <input
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    void handleRename();
                  }
                  if (e.key === 'Escape') {
                    e.preventDefault();
                    cancelRename();
                  }
                }}
                aria-label="项目名称"
                autoFocus
                disabled={renameProject.isPending}
                className="w-full rounded-lg border bg-background px-2 py-1.5 text-xs outline-none focus:ring-2 focus:ring-ring/20"
              />
              <div className="flex items-center justify-end gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={cancelRename}
                  disabled={renameProject.isPending}
                >
                  取消
                </Button>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => {
                    void handleRename();
                  }}
                  disabled={renameProject.isPending || !draftName.trim()}
                  aria-label="保存项目名称"
                >
                  保存
                </Button>
              </div>
              {renameError ? (
                <p className="text-[10px] text-destructive">{renameError}</p>
              ) : null}
            </div>
          ) : (
            <div className="flex items-center gap-1">
              <select
                className="min-w-0 flex-1 rounded-lg border bg-background px-2 py-1.5 text-xs"
                value={`${selectedOwnerUserId || ''}|${selectedProjectId}`}
                onChange={(e) => {
                  const [owner, id] = e.target.value.split('|');
                  setSelectedProjectId(id, owner || null);
                  cancelRename();
                }}
                aria-label="选择项目"
              >
                {projects.map((p) => {
                  const owner = p.owner_user_id || '';
                  const role =
                    p.role === 'collaborator' ? '协作' : p.role === 'lead' ? '负责' : '';
                  return (
                    <option key={`${owner}|${p.id}`} value={`${owner}|${p.id}`}>
                      {p.name || p.id}
                      {role ? ` · ${role}` : ''}
                    </option>
                  );
                })}
              </select>
              {canRename ? (
                <button
                  type="button"
                  onClick={beginRename}
                  aria-label="重命名项目"
                  title="重命名项目"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <Pencil size={12} />
                </button>
              ) : null}
            </div>
          )}
        </div>
      )}

      <div className="px-4 py-2">
        <div className="relative">
          <Search
            size={12}
            className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="text"
            placeholder="搜索对话…"
            aria-label="搜索对话"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-9 w-full rounded-lg border bg-background pl-8 pr-3 text-xs outline-none transition-shadow focus:ring-2 focus:ring-ring/20"
          />
        </div>
      </div>

      <div className="border-t px-4 pt-2">
        <button
          type="button"
          aria-expanded={resultsOpen}
          onClick={() => setResultsOpen((v) => !v)}
          className="flex w-full items-center justify-between py-1 text-left"
        >
          <span className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {resultsOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            结论
          </span>
          <span className="text-[10px] text-muted-foreground">{currentTurnCount} 轮</span>
        </button>
        {resultsOpen && (
          <Suspense fallback={<div className="py-2 text-center text-xs text-muted-foreground">正在加载…</div>}>
            <ResultsPanel embedded />
          </Suspense>
        )}
      </div>

      <div className="mt-1 flex items-center justify-between border-t px-4 pb-2 pt-3">
        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          最近
        </span>
        <span className="text-[10px] text-muted-foreground">{filteredConversations.length} 个</span>
      </div>

      <div className="flex-1 overflow-auto px-3 pb-4 scrollbar-thin">
        {isLoading ? (
          <div className="py-4 text-center text-xs text-muted-foreground">加载中…</div>
        ) : filteredConversations.length === 0 ? (
          <div className="mx-1 border border-dashed px-4 py-8 text-center text-xs leading-relaxed text-muted-foreground">
            {conversations.length === 0
              ? '这里还没有对话。\n点击右上角 + 开始第一个问题。'
              : '没有匹配的对话。'}
          </div>
        ) : (
          <ul className="flex flex-col divide-y divide-border/60" role="list">
            {[...filteredConversations].reverse().map((conversation) => (
              <li key={conversation.id}>
                <HistoryItem
                  conversation={conversation}
                  active={conversation.id === selectedConversationId}
                  confirming={confirmDeleteId === conversation.id}
                  busy={deleteConversation.isPending}
                  onSelect={() => setSelectedConversationId(conversation.id)}
                  onRequestDelete={() => setConfirmDeleteId(conversation.id)}
                  onCancelDelete={() => setConfirmDeleteId(null)}
                  onConfirmDelete={() => {
                    void handleConfirmDelete(conversation.id);
                  }}
                />
              </li>
            ))}
          </ul>
        )}
        {deleteConversation.isError ? (
          <p className="mt-2 px-1 text-[10px] text-destructive">删除失败，请重试。</p>
        ) : null}
      </div>
      {showMembers && <ProjectMembersPanel onClose={() => setShowMembers(false)} />}
    </div>
  );
}
