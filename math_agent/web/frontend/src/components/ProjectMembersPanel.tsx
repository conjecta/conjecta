import { useCallback, useEffect, useState } from 'react';
import { X } from 'lucide-react';
import {
  addProjectMember,
  listFriends,
  listProjectMembers,
  removeProjectMember,
  type FriendProfile,
} from '@/api/friends';
import { useUiStore } from '@/store/ui';
import { useAuthStore } from '@/store/auth';

interface Props {
  onClose: () => void;
}

interface Member {
  user_id: string;
  role: string;
  label: string;
}

const ROLE_LABELS: Record<string, string> = {
  lead: '负责人',
  collaborator: '协作者',
};

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

export function ProjectMembersPanel({ onClose }: Props) {
  const { selectedProjectId, selectedOwnerUserId } = useUiStore();
  const user = useAuthStore((s) => s.user);
  const [members, setMembers] = useState<Member[]>([]);
  const [friends, setFriends] = useState<FriendProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState('');
  const [inviting, setInviting] = useState(false);
  const ownerId = selectedOwnerUserId || user?.id || null;
  const isLead = !selectedOwnerUserId || selectedOwnerUserId === user?.id;

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    // Members are the primary content; friends only feed the "add" section, so
    // a friends failure must not blank the member list.
    const [membersRes, friendsRes] = await Promise.all([
      listProjectMembers(selectedProjectId, ownerId),
      listFriends().catch(() => ({ ok: true as const, friends: [] })),
    ]);
    setMembers(membersRes.members);
    setFriends(friendsRes.friends);
    setLoading(false);
  }, [selectedProjectId, ownerId]);

  useEffect(() => {
    reload().catch((e) => {
      setError(e.message);
      setLoading(false);
    });
  }, [reload]);

  // Escape closes the drawer, like every other overlay in the app.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const handleRemove = async (m: Member) => {
    if (!window.confirm(`确定将「${m.label}」移出项目吗？`)) return;
    try {
      await removeProjectMember(selectedProjectId, m.user_id, ownerId);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleAddFriend = async (f: FriendProfile) => {
    try {
      await addProjectMember(selectedProjectId, { user_id: f.user_id }, ownerId);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleInvite = async () => {
    const value = invite.trim();
    if (!value) return;
    setInviting(true);
    setError(null);
    try {
      // 11 位数字按手机号处理，否则按用户 ID。
      const payload = /^\d{11}$/.test(value) ? { phone: value } : { user_id: value };
      await addProjectMember(selectedProjectId, payload, ownerId);
      setInvite('');
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setInviting(false);
    }
  };

  const addableFriends = friends.filter((f) => !members.some((m) => m.user_id === f.user_id));

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/30"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="flex h-full w-full max-w-md flex-col overflow-y-auto bg-background p-6 shadow-xl"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-label="项目协作成员"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold">项目协作成员</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X size={16} />
          </button>
        </div>
        <p className="mb-4 text-xs text-muted-foreground">
          项目知识库由成员共享。负责人可添加/移除协作者；协作者可编辑知识库。
        </p>
        {error && <p className="mb-2 text-sm text-destructive">{error}</p>}
        {loading ? (
          <p className="mb-6 text-sm text-muted-foreground">加载中…</p>
        ) : (
          <ul className="mb-6 space-y-2">
            {members.map((m) => (
              <li key={m.user_id} className="flex items-center justify-between rounded border px-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{m.label}</div>
                  <div className="text-[10px] text-muted-foreground">{roleLabel(m.role)}</div>
                </div>
                {isLead && m.role !== 'lead' && (
                  <button type="button" className="text-destructive" onClick={() => handleRemove(m)}>
                    移除
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        {isLead && !loading && (
          <>
            <section className="mb-6">
              <h3 className="mb-2 text-sm font-semibold">邀请成员</h3>
              <div className="flex gap-2">
                <input
                  type="text"
                  className="flex-1 rounded border bg-background px-3 py-2 text-sm"
                  placeholder="手机号或用户 ID"
                  value={invite}
                  onChange={(e) => setInvite(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleInvite();
                  }}
                />
                <button
                  type="button"
                  className="rounded bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
                  disabled={inviting || !invite.trim()}
                  onClick={handleInvite}
                >
                  {inviting ? '邀请中…' : '邀请'}
                </button>
              </div>
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold">从好友中添加</h3>
              <ul className="space-y-2">
                {addableFriends.map((f) => (
                  <li key={f.user_id} className="flex items-center justify-between rounded border px-3 py-2 text-sm">
                    <span>{f.label}</span>
                    <button type="button" className="text-primary" onClick={() => handleAddFriend(f)}>
                      添加
                    </button>
                  </li>
                ))}
              </ul>
              {friends.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  还没有好友。先到「好友」页添加。
                </p>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
