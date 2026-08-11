import { useState } from 'react';
import { publishKnowledgeCard, publishTurnCard } from '@/api/knowledgeCards';

interface Props {
  projectId: string;
  itemId: string;
  kind: string;
  defaultTitle: string;
  defaultStatement: string;
  /** When set, publish from the conversation turn endpoint instead of a knowledge item. */
  sourceTurn?: { turnId: string; body?: string };
  onClose: () => void;
  onPublished?: () => void;
}

export function PublishCardDrawer({ projectId, itemId, kind, defaultTitle, defaultStatement, sourceTurn, onClose, onPublished }: Props) {
  const [title, setTitle] = useState(defaultTitle);
  const [visibility, setVisibility] = useState<'private' | 'friends' | 'public'>('private');
  const [tags, setTags] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      if (sourceTurn) {
        await publishTurnCard(projectId, sourceTurn.turnId, {
          title,
          statement: defaultStatement,
          body: sourceTurn.body,
          visibility,
          tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        });
      } else {
        await publishKnowledgeCard(projectId, kind, itemId, {
          title,
          statement: defaultStatement,
          visibility,
          tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        });
      }
      onPublished?.();
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30">
      <div className="h-full w-full max-w-md bg-background p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold">发布为知识卡片</h2>
          <button onClick={onClose}>×</button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium">标题</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} className="w-full rounded border px-3 py-2" />
          </div>
          <div>
            <label className="block text-sm font-medium">可见性</label>
            <select value={visibility} onChange={(e) => setVisibility(e.target.value as 'private' | 'friends' | 'public')} className="w-full rounded border px-3 py-2">
              <option value="private">私有</option>
              <option value="friends">好友可见</option>
              <option value="public">公开</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium">标签（逗号分隔）</label>
            <input value={tags} onChange={(e) => setTags(e.target.value)} className="w-full rounded border px-3 py-2" />
          </div>
          <button type="submit" disabled={saving} className="w-full rounded bg-primary px-4 py-2 text-primary-foreground">
            {saving ? '发布中…' : '发布'}
          </button>
        </form>
      </div>
    </div>
  );
}
