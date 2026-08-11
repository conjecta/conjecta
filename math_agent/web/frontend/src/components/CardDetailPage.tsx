import { useEffect, useState } from 'react';
import { exportCard, getCard } from '@/api/knowledgeCards';
import { TopBar } from '@/components/TopBar';
import type { KnowledgeCardDetail } from '@/types/knowledgeCards';

export function CardDetailPage({ cardId }: { cardId: string }) {
  const [detail, setDetail] = useState<KnowledgeCardDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCard(cardId)
      .then((cardRes) => {
        setDetail(cardRes.card);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [cardId]);

  const handleExport = async (format: string) => {
    try {
      const res = await exportCard(cardId, format);
      const blob = new Blob([res.content], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ext = format === 'markdown' ? 'md' : format === 'bibtex' ? 'bib' : format;
      a.download = `${cardId}.${ext}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const chrome = (children: React.ReactNode) => (
    <div className="flex h-[100dvh] w-full flex-col overflow-hidden">
      <TopBar />
      <main className="mx-auto w-full max-w-3xl flex-1 overflow-y-auto p-6">{children}</main>
    </div>
  );

  if (loading || error || !detail) {
    return chrome(
      loading ? (
        <div>加载中…</div>
      ) : (
        <div className="text-destructive">{error || 'Not found'}</div>
      ),
    );
  }

  const { card, revision } = detail;
  return chrome(
    <>
      <a href="/app/knowledge/gallery" className="mb-2 inline-block text-sm text-muted-foreground">
        ← 返回画廊
      </a>
      <h1 className="mb-2 text-xl font-bold">{revision.title}</h1>
      <div className="mb-4 text-sm text-muted-foreground">
        可见性: {card.visibility} · 状态: {card.status} · ★ {card.star_count}
      </div>
      <div className="rounded border p-4">
        <h2 className="font-semibold">Statement</h2>
        <p className="mb-4 whitespace-pre-wrap">{revision.statement}</p>
        <h2 className="font-semibold">Proof summary</h2>
        <p className="whitespace-pre-wrap">{revision.body}</p>
      </div>
      <div className="mt-4 flex gap-2">
        <button
          className="rounded border px-3 py-1 text-sm"
          onClick={() => handleExport('markdown')}
        >
          Markdown
        </button>
        <button
          className="rounded border px-3 py-1 text-sm"
          onClick={() => handleExport('latex')}
        >
          LaTeX
        </button>
        <button
          className="rounded border px-3 py-1 text-sm"
          onClick={() => handleExport('bibtex')}
        >
          BibTeX
        </button>
        <button
          className="rounded border px-3 py-1 text-sm"
          onClick={() => handleExport('lean')}
        >
          Lean
        </button>
      </div>
    </>,
  );
}
