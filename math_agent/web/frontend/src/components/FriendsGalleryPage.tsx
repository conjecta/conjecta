import { useEffect, useState } from 'react';
import { listFriendCards } from '@/api/knowledgeCards';
import { fetchAuthConfig } from '@/api/auth';
import { TopBar } from '@/components/TopBar';
import { CloudSetupNotice } from '@/components/CloudSetupNotice';
import { isCloudStorageRequiredMessage } from '@/lib/publicError';
import type { KnowledgeCardDetail } from '@/types/knowledgeCards';

export function FriendsGalleryContent() {
  const [cards, setCards] = useState<KnowledgeCardDetail[]>([]);
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cloudMissing, setCloudMissing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const config = await fetchAuthConfig();
        if (cancelled) return;
        if (!config.cloud_storage_configured) {
          setCloudMissing(true);
          setCards([]);
          return;
        }
        setCloudMissing(false);
        const res = await listFriendCards({ q });
        if (!cancelled) setCards(res.cards as KnowledgeCardDetail[]);
      } catch (e) {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : String(e);
        if (isCloudStorageRequiredMessage(message)) {
          setCloudMissing(true);
          setError(null);
        } else {
          setError(message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [q]);

  return (
    <>
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">好友知识</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            好友设为「好友可见」的卡片，可导入到你的项目知识库。
          </p>
        </div>
        <a href="/app/knowledge/gallery" className="text-xs text-muted-foreground hover:underline">
          公开画廊
        </a>
      </div>
      {cloudMissing && <div className="mb-4"><CloudSetupNotice title="好友知识需要云端存储" /></div>}
      {!cloudMissing && (
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索卡片…"
          className="mb-4 w-full rounded border px-3 py-2"
        />
      )}
      {error && !cloudMissing && <p className="text-sm text-destructive">{error}</p>}
      {loading ? (
        <div>加载中…</div>
      ) : cloudMissing ? null : (
        <ul className="space-y-2">
          {cards.map((entry) => {
            const card = entry.card ?? (entry as unknown as { id: string });
            const revision = entry.revision;
            const id = 'id' in card ? card.id : '';
            return (
              <li key={id} className="rounded-lg border p-3 hover:bg-accent">
                <a href={`/app/knowledge/card/${encodeURIComponent(id)}`} className="block">
                  <div className="font-medium">
                    {revision?.title || id}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground line-clamp-2">
                    {revision?.statement || ''}
                  </div>
                </a>
              </li>
            );
          })}
          {!loading && cards.length === 0 && (
            <p className="text-sm text-muted-foreground">暂无好友分享的卡片。</p>
          )}
        </ul>
      )}
    </>
  );
}

export function FriendsGalleryPage() {
  return (
    <div className="flex h-[100dvh] w-full flex-col overflow-hidden">
      <TopBar />
      <main className="mx-auto w-full max-w-3xl flex-1 overflow-y-auto p-6">
        <FriendsGalleryContent />
      </main>
    </div>
  );
}
