import { useEffect, useState } from 'react';
import { UserPlus } from 'lucide-react';
import { TopBar } from '@/components/TopBar';
import { CloudSetupNotice } from '@/components/CloudSetupNotice';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { FriendsGalleryContent } from '@/components/FriendsGalleryPage';
import { KnowledgeHubContent } from '@/components/KnowledgeHubPage';
import { fetchAuthConfig } from '@/api/auth';
import {
  acceptFriendRequest,
  declineFriendRequest,
  getMyProfile,
  listFriendRequests,
  listFriends,
  requestFriend,
  unfriend,
  updateMyProfile,
  type FriendProfile,
  type FriendRequest,
} from '@/api/friends';
import { isCloudStorageRequiredMessage } from '@/lib/publicError';

export type FriendsTab = 'friends' | 'gallery' | 'share';

function FriendsManagerContent() {
  const [friends, setFriends] = useState<FriendProfile[]>([]);
  const [incoming, setIncoming] = useState<FriendRequest[]>([]);
  const [outgoing, setOutgoing] = useState<FriendRequest[]>([]);
  const [displayName, setDisplayName] = useState('');
  const [phone, setPhone] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [cloudMissing, setCloudMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const reload = async () => {
    setError(null);
    const config = await fetchAuthConfig();
    const needsCloud = !config.cloud_storage_configured;
    setCloudMissing(needsCloud);

    const profileRes = await getMyProfile();
    setDisplayName(profileRes.profile.display_name || '');

    if (needsCloud) {
      setFriends([]);
      setIncoming([]);
      setOutgoing([]);
      return;
    }

    const [friendsRes, requestsRes] = await Promise.all([
      listFriends(),
      listFriendRequests(),
    ]);
    setFriends(friendsRes.friends);
    setIncoming(requestsRes.incoming);
    setOutgoing(requestsRes.outgoing);
  };

  useEffect(() => {
    setLoading(true);
    reload()
      .catch((e) => {
        const message = e instanceof Error ? e.message : String(e);
        if (isCloudStorageRequiredMessage(message)) {
          setCloudMissing(true);
          setError(null);
        } else {
          setError(message);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  const retryLoad = async () => {
    setLoading(true);
    setError(null);
    try {
      await reload();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (isCloudStorageRequiredMessage(message)) {
        setCloudMissing(true);
        setError(null);
      } else {
        setError(message);
      }
    } finally {
      setLoading(false);
    }
  };

  const saveProfile = async () => {
    setBusy(true);
    setError(null);
    try {
      await updateMyProfile(displayName);
      await reload();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (isCloudStorageRequiredMessage(message)) {
        setCloudMissing(true);
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  const sendRequest = async () => {
    if (!phone.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await requestFriend({ phone: phone.trim() });
      setPhone('');
      await reload();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (isCloudStorageRequiredMessage(message)) {
        setCloudMissing(true);
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-bold">好友</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          添加好友后，可将知识卡片分享给好友导入，并邀请好友协作研究项目。
        </p>
      </div>

      {cloudMissing && <CloudSetupNotice title="好友功能尚未就绪" />}
      {error && !cloudMissing && (
        <div className="space-y-2">
          <p className="text-sm text-destructive">{error}</p>
          <button
            type="button"
            className="text-xs text-muted-foreground underline"
            disabled={loading || busy}
            onClick={() => {
              void retryLoad();
            }}
          >
            重试加载
          </button>
        </div>
      )}
      {loading && <p className="text-sm text-muted-foreground">加载中…</p>}

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">显示名称</h2>
        <div className="flex gap-2">
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="用于好友列表与导入来源标注"
            disabled={cloudMissing || busy}
            className="flex-1 rounded border px-3 py-2 text-sm disabled:opacity-50"
          />
          <button
            type="button"
            disabled={busy || cloudMissing}
            onClick={saveProfile}
            className="rounded bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <UserPlus size={14} aria-hidden />
          添加好友（手机号）
        </h2>
        <p className="text-xs text-muted-foreground">
          输入对方已注册的 11 位手机号，对方同意后即可成为好友。
        </p>
        <div className="flex gap-2">
          <input
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="11 位手机号"
            inputMode="numeric"
            autoComplete="tel"
            disabled={cloudMissing || busy}
            className="flex-1 rounded border px-3 py-2 text-sm disabled:opacity-50"
          />
          <button
            type="button"
            disabled={busy || cloudMissing || !phone.trim()}
            onClick={sendRequest}
            aria-label="发送好友请求"
            className="inline-flex items-center gap-1.5 rounded bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
          >
            <UserPlus size={14} aria-hidden />
            发送请求
          </button>
        </div>
      </section>

      {!cloudMissing && incoming.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">待处理请求</h2>
          <ul className="space-y-2">
            {incoming.map((req) => (
              <li key={req.id} className="flex items-center justify-between rounded border px-3 py-2 text-sm">
                <span>{req.other.label}</span>
                <span className="flex gap-2">
                  <button
                    type="button"
                    className="text-primary"
                    onClick={() => acceptFriendRequest(req.id).then(reload).catch((e) => setError(e.message))}
                  >
                    接受
                  </button>
                  <button
                    type="button"
                    className="text-muted-foreground"
                    onClick={() => declineFriendRequest(req.id).then(reload).catch((e) => setError(e.message))}
                  >
                    拒绝
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!cloudMissing && outgoing.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">已发送</h2>
          <ul className="space-y-1 text-sm text-muted-foreground">
            {outgoing.map((req) => (
              <li key={req.id}>{req.other.label} · 等待对方确认</li>
            ))}
          </ul>
        </section>
      )}

      {!cloudMissing && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">我的好友</h2>
          {friends.length === 0 ? (
            <p className="text-sm text-muted-foreground">还没有好友。</p>
          ) : (
            <ul className="space-y-2">
              {friends.map((friend) => (
                <li key={friend.user_id} className="flex items-center justify-between rounded border px-3 py-2 text-sm">
                  <div>
                    <div className="font-medium">{friend.label}</div>
                    <div className="font-mono text-[10px] text-muted-foreground">{friend.user_id}</div>
                  </div>
                  <button
                    type="button"
                    className="text-destructive"
                    onClick={() => unfriend(friend.user_id).then(reload).catch((e) => setError(e.message))}
                  >
                    解除
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

const FRIENDS_TABS: Array<{ value: FriendsTab; label: string }> = [
  { value: 'friends', label: '好友' },
  { value: 'gallery', label: '好友知识' },
  { value: 'share', label: '我的分享' },
];

export function FriendsPage({ initialTab = 'friends' }: { initialTab?: FriendsTab }) {
  const [tab, setTab] = useState<FriendsTab>(initialTab);

  return (
    <div className="flex h-[100dvh] w-full flex-col overflow-hidden">
      <TopBar />
      <main className="mx-auto w-full max-w-2xl flex-1 overflow-y-auto p-6">
        <Tabs value={tab} onValueChange={(value) => setTab(value as FriendsTab)}>
          <TabsList className="mb-6">
            {FRIENDS_TABS.map((t) => (
              <TabsTrigger
                key={t.value}
                value={t.value}
                className="px-3 py-2 text-sm font-medium normal-case tracking-normal"
              >
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="friends" className="overflow-visible py-0">
            <FriendsManagerContent />
          </TabsContent>
          <TabsContent value="gallery" className="overflow-visible py-0">
            <FriendsGalleryContent />
          </TabsContent>
          <TabsContent value="share" className="overflow-visible py-0">
            <KnowledgeHubContent />
          </TabsContent>
        </Tabs>
      </main>
    </div>
  );
}
