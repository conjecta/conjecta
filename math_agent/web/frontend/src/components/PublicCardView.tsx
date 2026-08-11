import { useEffect, useState } from 'react';
import { getCard } from '@/api/knowledgeCards';
import type { KnowledgeCardDetail } from '@/types/knowledgeCards';

export function PublicCardView({ cardId }: { cardId: string }) {
  const [detail, setDetail] = useState<KnowledgeCardDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCard(cardId)
      .then((res) => setDetail(res.card))
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [cardId]);

  if (loading) return <div className="p-6">加载中…</div>;
  if (!detail) return <div className="p-6">卡片不存在或未公开。</div>;

  const { card, revision } = detail;
  return (
    <div className="min-h-screen bg-background p-6 text-foreground">
      <h1 className="mb-2 text-2xl font-bold">{revision.title}</h1>
      <div className="mb-4 text-sm text-muted-foreground">
        Conjecta · {card.visibility} · ★ {card.star_count}
      </div>
      <div className="rounded border p-4">
        <p className="whitespace-pre-wrap">{revision.statement}</p>
        <p className="mt-4 whitespace-pre-wrap">{revision.body}</p>
      </div>
    </div>
  );
}
