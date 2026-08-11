import { useEffect, useMemo, useState } from 'react';
import { BrainCircuit, Check, Clock3, Pause, Play, ShieldCheck, Trash2 } from 'lucide-react';
import {
  clearUserProfile,
  deleteUserMemory,
  fetchUserMemories,
  updateUserMemory,
  type UserMemory,
  type UserMemoryList,
  type UserMemoryStatus,
} from '@/api/memories';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

const KIND_LABELS: Record<UserMemory['kind'], string> = {
  preference: '偏好',
  technique: '习惯',
  correction: '纠正',
  context: '背景',
};

const STATUS_LABELS: Record<UserMemoryStatus, string> = {
  candidate: '待确认',
  active: '使用中',
  snoozed: '已暂停',
};

function scopeLabel(scope: string) {
  return scope === 'global' ? '所有项目' : `项目 ${scope.replace(/^project:/, '')}`;
}

function nextAction(status: UserMemoryStatus): { label: string; status: UserMemoryStatus } {
  if (status === 'active') return { label: '暂停', status: 'snoozed' };
  return { label: status === 'candidate' ? '启用' : '恢复', status: 'active' };
}

export function MemoryDialog({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<UserMemoryList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [confirmClearProfile, setConfirmClearProfile] = useState(false);

  useEffect(() => {
    let mounted = true;
    fetchUserMemories()
      .then((result) => mounted && setData(result))
      .catch((err) => {
        if (mounted) setError(err instanceof Error ? err.message : '记忆加载失败');
      });
    return () => {
      mounted = false;
    };
  }, []);

  const counts = useMemo(() => {
    const result = { active: 0, candidate: 0, snoozed: 0 };
    data?.memories.forEach((memory) => {
      result[memory.status] += 1;
    });
    return result;
  }, [data]);

  const changeStatus = async (memory: UserMemory, status: UserMemoryStatus) => {
    setBusyId(memory.id);
    setError(null);
    try {
      const updated = await updateUserMemory(memory.id, { status });
      setData((current) =>
        current
          ? {
              ...current,
              memories: current.memories.map((item) => (item.id === updated.id ? updated : item)),
            }
          : current,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : '记忆状态更新失败');
    } finally {
      setBusyId(null);
    }
  };

  const removeMemory = async (memoryId: string) => {
    setBusyId(memoryId);
    setError(null);
    try {
      await deleteUserMemory(memoryId);
      setData((current) =>
        current
          ? { ...current, memories: current.memories.filter((item) => item.id !== memoryId) }
          : current,
      );
      setConfirmDeleteId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '记忆删除失败');
    } finally {
      setBusyId(null);
    }
  };

  const clearProfile = async () => {
    setBusyId('profile');
    setError(null);
    try {
      await clearUserProfile();
      setData((current) => (current ? { ...current, profile: null } : current));
      setConfirmClearProfile(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : '整体理解清除失败');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl overflow-hidden p-0">
        <DialogHeader className="border-b bg-card px-6 pb-5 pt-6 text-left">
          <div className="mb-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-primary">
            <BrainCircuit size={14} />
            Personal context
          </div>
          <DialogTitle>记忆管理</DialogTitle>
          <DialogDescription>
            查看 Conjecta 从对话中学到的长期偏好。上方整体理解与“使用中”的条目会影响后续回答。
          </DialogDescription>
        </DialogHeader>

        <div className="scrollbar-thin max-h-[70vh] space-y-5 overflow-y-auto px-6 py-5">
          {error && (
            <div
              role="alert"
              className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              {error}
            </div>
          )}

          {data?.profile && (
            <section className="relative overflow-hidden border bg-primary/[0.045] px-4 py-4">
              <div className="absolute inset-y-0 left-0 w-1 bg-primary" />
              <div className="mb-2 flex items-center justify-between gap-3">
                <p className="flex items-center gap-1.5 text-xs font-semibold">
                  <ShieldCheck size={14} className="text-primary" />
                  对你的整体理解
                </p>
                <span className="font-mono text-[10px] text-muted-foreground">
                  v{data.profile.version}
                </span>
              </div>
              <p className="text-sm leading-6 text-foreground/85">{data.profile.summary}</p>
              <div className="mt-3 flex flex-wrap items-center justify-end gap-2 border-t border-primary/15 pt-2.5">
                {confirmClearProfile ? (
                  <>
                    <span className="w-full text-xs leading-5 text-destructive sm:mr-auto sm:w-auto">
                      清除后将停止使用这份整体理解
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => setConfirmClearProfile(false)}
                      disabled={busyId === 'profile'}
                    >
                      取消
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      onClick={clearProfile}
                      disabled={busyId === 'profile'}
                    >
                      确认清除
                    </Button>
                  </>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="text-muted-foreground hover:text-destructive"
                    onClick={() => setConfirmClearProfile(true)}
                  >
                    <Trash2 size={12} />
                    <span className="ml-1">清除整体理解</span>
                  </Button>
                )}
              </div>
            </section>
          )}

          {data && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 border-b pb-3 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
              <span>{counts.active} 使用中</span>
              <span>{counts.candidate} 待确认</span>
              <span>{counts.snoozed} 已暂停</span>
            </div>
          )}

          {!data && !error && (
            <div className="flex items-center gap-2 py-12 text-sm text-muted-foreground">
              <Clock3 size={15} className="animate-pulse" /> 正在整理记忆…
            </div>
          )}

          {data && data.memories.length === 0 && (
            <div className="border border-dashed px-4 py-10 text-center">
              <BrainCircuit className="mx-auto mb-3 text-muted-foreground" size={22} />
              <p className="text-sm font-medium">还没有长期记忆</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                当你反复表达语言、格式或解题偏好时，相关内容会出现在这里。
              </p>
            </div>
          )}

          {data?.memories.map((memory) => {
            const action = nextAction(memory.status);
            const deleting = confirmDeleteId === memory.id;
            const busy = busyId === memory.id;
            return (
              <article
                key={memory.id}
                className="border border-l-2 border-l-primary/60 bg-card px-4 py-3.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="border bg-secondary px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-secondary-foreground">
                    {KIND_LABELS[memory.kind]}
                  </span>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {scopeLabel(memory.scope)}
                  </span>
                  <span className="ml-auto flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                    {memory.status === 'active' && <Check size={11} className="text-success" />}
                    {STATUS_LABELS[memory.status]}
                  </span>
                </div>
                <p className="mt-2.5 text-sm font-medium leading-6">{memory.content}</p>
                {memory.why && (
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">依据：{memory.why}</p>
                )}
                <div className="mt-3 flex flex-wrap items-center justify-end gap-2 border-t pt-2.5">
                  {deleting ? (
                    <>
                      <span className="w-full text-xs leading-5 text-destructive sm:mr-auto sm:w-auto">
                        删除后不会自动重新添加相同内容
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setConfirmDeleteId(null)}
                        disabled={busy}
                      >
                        取消
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        onClick={() => removeMemory(memory.id)}
                        disabled={busy}
                      >
                        确认删除
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => changeStatus(memory, action.status)}
                        disabled={busy}
                      >
                        {memory.status === 'active' ? <Pause size={12} /> : <Play size={12} />}
                        <span className="ml-1">{action.label}</span>
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="text-destructive hover:bg-destructive/10"
                        onClick={() => setConfirmDeleteId(memory.id)}
                        disabled={busy}
                        aria-label={`删除记忆：${memory.content}`}
                      >
                        <Trash2 size={12} />
                      </Button>
                    </>
                  )}
                </div>
              </article>
            );
          })}

          <p className="pb-1 text-[11px] leading-5 text-muted-foreground">
            删除会保留一条拒绝记录，防止系统再次学习完全相同的内容；不会删除原始对话。
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
