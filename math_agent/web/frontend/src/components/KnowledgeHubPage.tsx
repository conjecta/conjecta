import { useEffect, useState } from 'react';
import { listMyCards } from '@/api/knowledgeCards';
import { TopBar } from '@/components/TopBar';
import type { KnowledgeCardSummary } from '@/types/knowledgeCards';

export function KnowledgeHubContent() {
  const [cards, setCards] = useState<KnowledgeCardSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listMyCards()
      .then((res) => setCards(res.cards))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <h1 className="mb-4 text-xl font-bold">我的知识卡片</h1>
      {loading && <p className="text-sm text-muted-foreground">加载中…</p>}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {!loading && !error && cards.length === 0 && (
        <p className="text-muted-foreground">还没有知识卡片。在 Research 中证明引理后可以发布为卡片。</p>
      )}
      {!loading && !error && cards.length > 0 && (
        <ul className="space-y-2">
          {cards.map((card) => (
            <li key={card.id} className="rounded-lg border p-3 hover:bg-accent">
              <a href={`/app/knowledge/card/${encodeURIComponent(card.id)}`} className="block">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{card.id}</span>
                  <span className="text-xs text-muted-foreground">{card.visibility}</span>
                </div>
              </a>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function KnowledgeHubPage() {
  return (
    <div className="flex h-[100dvh] w-full flex-col overflow-hidden">
      <TopBar />
      <main className="mx-auto w-full max-w-3xl flex-1 overflow-y-auto p-6">
        <KnowledgeHubContent />
      </main>
    </div>
  );
}
