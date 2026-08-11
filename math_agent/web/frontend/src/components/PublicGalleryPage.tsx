import { useEffect, useState } from 'react';
import { listPublicCards } from '@/api/knowledgeCards';
import { TopBar } from '@/components/TopBar';
import type { KnowledgeCardSummary } from '@/types/knowledgeCards';

export function PublicGalleryPage() {
  const [cards, setCards] = useState<KnowledgeCardSummary[]>([]);
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listPublicCards({ q })
      .then((res) => setCards(res.cards))
      .finally(() => setLoading(false));
  }, [q]);

  return (
    <div className="flex h-[100dvh] w-full flex-col overflow-hidden">
      <TopBar />
      <main className="mx-auto w-full max-w-3xl flex-1 overflow-y-auto p-6">
        <h1 className="mb-4 text-xl font-bold">公开知识画廊</h1>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索卡片…"
          className="mb-4 rounded border px-3 py-2"
        />
        {loading ? <div>加载中…</div> : (
          <ul className="space-y-2">
            {cards.map((card) => (
              <li key={card.id} className="rounded-lg border p-3 hover:bg-accent">
                <a href={`/app/knowledge/card/${encodeURIComponent(card.id)}`} className="block">
                  {card.id} · ★ {card.star_count} · 引用 {card.citation_count}
                </a>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}
