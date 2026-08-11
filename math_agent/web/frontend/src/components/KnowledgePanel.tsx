import { useUiStore } from '@/store/ui';
import {
  useDeleteKnowledgeItem,
  useKnowledge,
  useKnowledgeGraph,
  useMaterials,
  useTranslateKnowledge,
  useUpdateKnowledgeItem,
} from '@/api/queries';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { MathText } from '@/components/MathText';
import { cn } from '@/lib/utils';
import { BadgeCheck, BookOpen, ExternalLink, Lightbulb, Pencil, Trash2, Wrench } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { ElementType } from 'react';

const TABS = [
  { value: 'knowledge', label: '知识' },
  { value: 'materials', label: '材料' },
] as const;

type KnowledgeKind = 'fact' | 'intuition' | 'technique' | 'material' | 'source';

type KnowledgeItem = {
  id: string;
  text?: string;
  statement?: string;
  why?: string;
  name?: string;
  label?: string;
  title?: string;
  body?: string;
  statement_zh?: string;
  why_zh?: string;
  title_zh?: string;
  body_zh?: string;
  description?: string;
  category?: string;
  kind?: string;
  source?: string;
  url?: string;
  created_at?: string;
  confidence?: number;
  humanApproved?: boolean;
  successCount?: number;
  failureCount?: number;
  metadata?: {
    provenance?: {
      source_owner_user_id?: string;
      source_owner_display_name?: string;
      source_owner_phone_masked?: string;
      source_project_id?: string;
      card_id?: string;
    };
    [key: string]: unknown;
  };
};

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const INTERNAL_SOURCE_LABELS = new Set([
  'memory_consolidation',
  'knowledge_evaluator',
  'agent_trace',
  'extracted',
  'project',
  'jsonl',
  'none',
  'consolidated',
  'manual',
  'user_prompt',
  'lean_verified',
  'pdf',
  'web',
]);

const KIND_STYLES: Record<
  KnowledgeKind,
  { label: string; icon: ElementType; accent: string; badge: string }
> = {
  fact: {
    label: 'Verified fact',
    icon: BadgeCheck,
    accent: 'border-l-blue-500',
    badge: 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300',
  },
  intuition: {
    label: 'Intuition',
    icon: Lightbulb,
    accent: 'border-l-amber-500',
    badge: 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  },
  technique: {
    label: 'Technique',
    icon: Wrench,
    accent: 'border-l-emerald-500',
    badge: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  },
  material: {
    label: 'Material',
    icon: BookOpen,
    accent: 'border-l-slate-500',
    badge: 'border-slate-500/30 bg-slate-500/10 text-slate-700 dark:text-slate-300',
  },
  source: {
    label: 'Source',
    icon: BookOpen,
    accent: 'border-l-sky-500',
    badge: 'border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  },
};

function normalizeText(text: string) {
  return text.replace(/\s+/g, ' ').trim();
}

function isDisplayableSource(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  const text = normalizeText(value);
  if (!text) return false;
  const lowered = text.toLowerCase();
  if (INTERNAL_SOURCE_LABELS.has(lowered)) return false;
  if (/^[a-z][a-z0-9_]{2,40}$/.test(lowered) && lowered.includes('_')) return false;
  return true;
}

function isReadable(value: unknown, itemId: string) {
  if (typeof value !== 'string') return false;
  const text = normalizeText(value);
  return Boolean(text) && text !== itemId && !UUID_PATTERN.test(text);
}

function compactText(text: string, max: number) {
  const normalized = normalizeText(text);
  return normalized.length > max ? `${normalized.slice(0, max - 1).trim()}...` : normalized;
}

function firstReadable(item: KnowledgeItem, fields: Array<keyof KnowledgeItem>) {
  for (const field of fields) {
    const value = item[field];
    if (isReadable(value, item.id)) return normalizeText(value as string);
  }
  return '';
}

function titleFromText(text: string, fallback: string) {
  if (!text) return fallback;
  const colonIndex = text.indexOf(':');
  if (colonIndex > 12 && colonIndex < 120) return compactText(text.slice(0, colonIndex), 96);

  const sentenceEnd = text.search(/[.!?]\s/);
  if (sentenceEnd > 24 && sentenceEnd < 120) return compactText(text.slice(0, sentenceEnd + 1), 96);

  return compactText(text, 96);
}

function removeTitlePrefix(text: string, title: string) {
  const normalized = normalizeText(text);
  if (!title || normalized === title) return '';
  if (normalized.startsWith(`${title}:`)) return normalizeText(normalized.slice(title.length + 1));
  return normalized;
}

function knowledgeSummary(item: KnowledgeItem, kind: KnowledgeKind) {
  const label = KIND_STYLES[kind].label;
  const explicitTitle = firstReadable(item, ['title', 'name', 'label']);
  const body = firstReadable(item, ['statement', 'text', 'body', 'description']);
  const title = explicitTitle || titleFromText(body, label);
  const excerpt = removeTitlePrefix(body, title);
  return {
    title,
    excerpt: compactText(excerpt, 260),
  };
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function metaChips(item: KnowledgeItem) {
  const chips: string[] = [];
  if (typeof item.confidence === 'number') chips.push(`${Math.round(item.confidence * 100)}%`);
  if (item.humanApproved) chips.push('reviewed');
  if (typeof item.successCount === 'number' && item.successCount > 0) chips.push(`${item.successCount} worked`);
  if (isDisplayableSource(item.source) && isReadable(item.source, item.id)) {
    chips.push(item.source as string);
  }
  if (isReadable(item.category, item.id)) chips.push(item.category as string);
  if (isReadable(item.kind, item.id)) chips.push(item.kind as string);
  if (item.created_at) {
    const date = formatDate(item.created_at);
    if (date) chips.push(date);
  }
  return chips.slice(0, 4);
}

function isUrlChip(chip: string) {
  return /^https?:\/\//i.test(chip);
}

function isPrimarilyEnglish(text: string) {
  const latin = (text.match(/[A-Za-z]/g) ?? []).length;
  const cjk = (text.match(/[\u3400-\u9fff]/g) ?? []).length;
  return latin >= 12 && latin > cjk * 2;
}

function translatedItem(item: KnowledgeItem, kind: KnowledgeKind): KnowledgeItem | null {
  if (kind === 'fact' && isReadable(item.statement_zh, item.id)) {
    return { ...item, statement: item.statement_zh, why: item.why_zh };
  }
  if ((kind === 'intuition' || kind === 'technique') && isReadable(item.title_zh, item.id)) {
    return { ...item, title: item.title_zh, body: item.body_zh };
  }
  return null;
}

/** Compact URL for narrow panels: host/…/filename.pdf */
function displayUrl(url: string) {
  try {
    const parsed = new URL(url);
    const segments = parsed.pathname.split('/').filter(Boolean);
    const file = segments[segments.length - 1] ?? '';
    if (file.includes('.')) return `${parsed.hostname}/…/${file}`;
    if (segments.length > 1) return `${parsed.hostname}/…/${segments[segments.length - 1]}`;
    return parsed.hostname + (parsed.pathname !== '/' ? parsed.pathname : '');
  } catch {
    return url;
  }
}

function KnowledgeArticle({
  item,
  kind,
  index,
  onSelect,
}: {
  item: KnowledgeItem;
  kind: KnowledgeKind;
  index: number;
  onSelect: (item: KnowledgeItem, kind: KnowledgeKind) => void;
}) {
  const style = KIND_STYLES[kind];
  const Icon = style.icon;
  const [language, setLanguage] = useState<'original' | 'zh'>('original');
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const selectedOwnerUserId = useUiStore((state) => state.selectedOwnerUserId);
  const translate = useTranslateKnowledge(selectedProjectId, selectedOwnerUserId);
  const localized = language === 'zh' ? translatedItem(item, kind) ?? item : item;
  const summary = knowledgeSummary(localized, kind);
  const chips = metaChips(item);
  const titleId = `knowledge-${kind}-${index}`;
  const isUrlTitle = isUrlChip(summary.title);
  const translatableKind = kind === 'fact' || kind === 'intuition' || kind === 'technique';
  const sourceText = firstReadable(item, ['statement', 'title', 'body', 'text', 'description']);
  const showLanguageChoice = translatableKind && (
    isPrimarilyEnglish(sourceText) || translatedItem(item, kind) !== null
  );
  const apiKind = kind === 'technique' ? 'trick' : kind;

  const chooseChinese = async () => {
    if (translatedItem(item, kind)) {
      setLanguage('zh');
      return;
    }
    try {
      await translate.mutateAsync({ itemId: item.id, kind: apiKind as 'fact' | 'intuition' | 'trick' });
      setLanguage('zh');
    } catch {
      // The inline mutation message keeps the original readable and offers a retry.
    }
  };

  return (
    <li>
      <article className={cn('kb-item', style.accent)}>
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className={cn('kb-kind-badge', style.badge)}>
            <Icon size={11} aria-hidden="true" />
            {style.label}
          </span>
          {item.metadata?.provenance?.source_owner_user_id ? (
            <span
              className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
              title={item.metadata.provenance.source_owner_user_id}
            >
              来自{' '}
              {item.metadata.provenance.source_owner_display_name ||
                item.metadata.provenance.source_owner_phone_masked ||
                item.metadata.provenance.source_owner_user_id}
            </span>
          ) : null}
          {showLanguageChoice ? (
            <span className="kb-language-choice" role="group" aria-label="显示语言">
              <button
                type="button"
                className={cn('kb-language-option', language === 'original' && 'is-active')}
                aria-pressed={language === 'original'}
                onClick={() => setLanguage('original')}
              >
                原文
              </button>
              <button
                type="button"
                className={cn('kb-language-option', language === 'zh' && 'is-active')}
                aria-pressed={language === 'zh'}
                disabled={translate.isPending}
                onClick={chooseChinese}
              >
                {translate.isPending ? '翻译中…' : '中文'}
              </button>
            </span>
          ) : null}
        </div>
        <button
          type="button"
          aria-labelledby={titleId}
          className="block w-full text-left"
          onClick={() => onSelect(localized, kind)}
        >
          <h3 id={titleId} className="kb-item-title">
            {isUrlTitle ? (
              <span
                className="break-all text-sky-700 dark:text-sky-300"
                title={summary.title}
              >
                {displayUrl(summary.title)}
              </span>
            ) : (
              <MathText text={summary.title} />
            )}
          </h3>
          {summary.excerpt ? (
            <p className="kb-item-excerpt">
              <MathText text={summary.excerpt} />
            </p>
          ) : null}
          {chips.length ? (
            <div className="kb-meta">
              {chips.map((chip) => (
                <span
                  key={chip}
                  className={cn('kb-meta-chip', isUrlChip(chip) && 'kb-url-chip')}
                  title={isUrlChip(chip) ? chip : undefined}
                >
                  {chip}
                </span>
              ))}
            </div>
          ) : null}
        </button>
        {translate.isError ? (
          <p className="mt-2 text-[10px] text-destructive">翻译失败，点击“中文”重试。</p>
        ) : null}
      </article>
    </li>
  );
}

function KnowledgeDetailDialog({
  item,
  kind,
  open,
  onOpenChange,
}: {
  item: KnowledgeItem | null;
  kind: KnowledgeKind | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const selectedOwnerUserId = useUiStore((state) => state.selectedOwnerUserId);
  const updateItem = useUpdateKnowledgeItem(selectedProjectId, selectedOwnerUserId);
  const deleteItem = useDeleteKnowledgeItem(selectedProjectId, selectedOwnerUserId);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const [draftBody, setDraftBody] = useState('');
  const [draftWhy, setDraftWhy] = useState('');

  const editable = kind === 'fact' || kind === 'intuition' || kind === 'technique';
  const apiKind = kind === 'technique' ? 'trick' : kind;

  useEffect(() => {
    if (!open || !item || !kind) return;
    setEditing(false);
    setConfirmDelete(false);
    setError(null);
    if (kind === 'fact') {
      setDraftTitle(item.statement || item.text || '');
      setDraftBody('');
      setDraftWhy(item.why || '');
    } else {
      setDraftTitle(item.title || item.name || item.label || '');
      setDraftBody(item.body || item.text || item.description || '');
      setDraftWhy('');
    }
  }, [open, item, kind]);

  if (!item || !kind) return null;
  const style = KIND_STYLES[kind];
  const Icon = style.icon;
  const fullText = firstReadable(item, ['statement', 'text', 'body', 'description']);
  const titleText = firstReadable(item, ['title', 'name', 'label']) || titleFromText(fullText, style.label);
  const isUrlTitle = isUrlChip(titleText);
  const busy = updateItem.isPending || deleteItem.isPending;

  const saveEdits = async () => {
    if (!editable || apiKind === 'material' || apiKind === 'source') return;
    setError(null);
    const fields: Record<string, string> =
      kind === 'fact'
        ? { statement: draftTitle.trim(), why: draftWhy.trim() }
        : { title: draftTitle.trim(), body: draftBody.trim() };
    if (kind === 'fact' && !fields.statement) {
      setError('事实内容不能为空');
      return;
    }
    if (kind !== 'fact' && !fields.title) {
      setError('标题不能为空');
      return;
    }
    try {
      await updateItem.mutateAsync({
        itemId: item.id,
        kind: apiKind as 'fact' | 'intuition' | 'trick',
        fields,
      });
      setEditing(false);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    }
  };

  const removeItem = async () => {
    if (!editable || apiKind === 'material' || apiKind === 'source') return;
    setError(null);
    try {
      await deleteItem.mutateAsync({
        itemId: item.id,
        kind: apiKind as 'fact' | 'intuition' | 'trick',
      });
      setConfirmDelete(false);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <div className="mb-2 flex items-center gap-2">
            <span className={cn('kb-kind-badge', style.badge)}>
              <Icon size={11} aria-hidden="true" />
              {style.label}
            </span>
          </div>
          <DialogTitle asChild>
            <h3 className="text-base leading-snug">
              {editing ? (
                kind === 'fact' ? '编辑事实' : '编辑条目'
              ) : isUrlTitle ? (
                <a
                  href={titleText}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 break-all text-sky-700 underline-offset-2 hover:underline dark:text-sky-300"
                >
                  {displayUrl(titleText)}
                  <ExternalLink size={12} />
                </a>
              ) : (
                <MathText text={titleText} />
              )}
            </h3>
          </DialogTitle>
          <DialogDescription asChild>
            <div className="sr-only">Knowledge detail</div>
          </DialogDescription>
        </DialogHeader>

        {editing ? (
          <div className="space-y-3 py-1">
            {kind === 'fact' ? (
              <>
                <div className="space-y-1">
                  <label className="text-[11px] text-muted-foreground" htmlFor="kb-edit-statement">
                    陈述
                  </label>
                  <Textarea
                    id="kb-edit-statement"
                    value={draftTitle}
                    onChange={(e) => setDraftTitle(e.target.value)}
                    rows={5}
                    disabled={busy}
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[11px] text-muted-foreground" htmlFor="kb-edit-why">
                    依据（可选）
                  </label>
                  <Textarea
                    id="kb-edit-why"
                    value={draftWhy}
                    onChange={(e) => setDraftWhy(e.target.value)}
                    rows={3}
                    disabled={busy}
                  />
                </div>
              </>
            ) : (
              <>
                <div className="space-y-1">
                  <label className="text-[11px] text-muted-foreground" htmlFor="kb-edit-title">
                    标题
                  </label>
                  <Input
                    id="kb-edit-title"
                    value={draftTitle}
                    onChange={(e) => setDraftTitle(e.target.value)}
                    disabled={busy}
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[11px] text-muted-foreground" htmlFor="kb-edit-body">
                    内容
                  </label>
                  <Textarea
                    id="kb-edit-body"
                    value={draftBody}
                    onChange={(e) => setDraftBody(e.target.value)}
                    rows={6}
                    disabled={busy}
                  />
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="max-h-[60vh] overflow-auto py-1">
            {fullText && !isUrlTitle && fullText !== titleText ? (
              <div className="prose prose-sm max-w-none text-xs leading-relaxed text-foreground">
                <MathText text={fullText} />
              </div>
            ) : null}
            {isUrlTitle && isReadable(item.body, item.id) ? (
              <div className="prose prose-sm max-w-none text-xs leading-relaxed text-foreground">
                <MathText text={item.body as string} />
              </div>
            ) : null}
            {kind === 'fact' && isReadable(item.why, item.id) ? (
              <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                依据：<MathText text={item.why as string} />
              </p>
            ) : null}
          </div>
        )}

        {error ? <p className="text-xs text-destructive">{error}</p> : null}

        {editable ? (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t pt-3">
            {editing ? (
              <>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setEditing(false);
                    setError(null);
                  }}
                  disabled={busy}
                >
                  取消
                </Button>
                <Button type="button" size="sm" onClick={() => void saveEdits()} disabled={busy}>
                  保存
                </Button>
              </>
            ) : confirmDelete ? (
              <>
                <span className="mr-auto text-xs text-destructive">删除后无法恢复</span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setConfirmDelete(false)}
                  disabled={busy}
                >
                  取消
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  onClick={() => void removeItem()}
                  disabled={busy}
                  aria-label="确认删除知识"
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
                  onClick={() => setEditing(true)}
                  disabled={busy}
                >
                  <Pencil size={12} />
                  <span className="ml-1">编辑</span>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setConfirmDelete(true)}
                  disabled={busy}
                  aria-label="删除知识"
                >
                  <Trash2 size={12} />
                  <span className="ml-1">删除</span>
                </Button>
              </>
            )}
          </div>
        ) : item.id ? (
          <div className="border-t pt-2 text-[10px] font-mono text-muted-foreground">ID: {item.id}</div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function KnowledgeList({
  items,
  empty,
  kind,
  onSelect,
}: {
  items: KnowledgeItem[];
  empty: string;
  kind: KnowledgeKind;
  onSelect: (item: KnowledgeItem, kind: KnowledgeKind) => void;
}) {
  return (
    <ul className="kb-list">
      {items.map((item, index) => (
        <KnowledgeArticle key={item.id} item={item} kind={kind} index={index} onSelect={onSelect} />
      ))}
      {items.length === 0 && empty ? <li className="kb-empty">{empty}</li> : null}
    </ul>
  );
}

export function KnowledgePanel() {
  const { selectedProjectId, selectedOwnerUserId, selectedKnowledgeTab, setSelectedKnowledgeTab } = useUiStore();
  const { data, isLoading, error } = useKnowledge(selectedProjectId, selectedOwnerUserId);
  const { data: materialsData, isLoading: materialsLoading } = useMaterials(selectedProjectId);
  const { data: graphData, isLoading: graphLoading } = useKnowledgeGraph(selectedProjectId);
  const [selected, setSelected] = useState<{ item: KnowledgeItem; kind: KnowledgeKind } | null>(null);
  const handleSelect = (item: KnowledgeItem, kind: KnowledgeKind) => setSelected({ item, kind });
  const materials = (materialsData?.materials ?? []).map((material) => ({
    ...material,
    title: material.label,
    body: material.text,
  }));
  const sources = (graphData?.nodes ?? [])
    .filter((node) => node.kind === 'source' && isDisplayableSource(node.label))
    .map((node) => ({
      id: node.id,
      title: node.label,
      body: node.body,
      source: node.source,
      kind: node.status,
      created_at: node.created_at,
    }));
  // Older builds stored finer-grained tab ids; collapse them onto the new tabs.
  const activeTab = selectedKnowledgeTab === 'materials' ? 'materials' : 'knowledge';

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="panel-header">
        <span>项目知识</span>
      </div>
      <Tabs
        value={activeTab}
        onValueChange={setSelectedKnowledgeTab}
        className="flex flex-1 flex-col overflow-hidden"
      >
        <TabsList className="grid w-full grid-cols-2">
          {TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value} className="px-1 text-center">
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="knowledge" className="scrollbar-thin">
          {isLoading && <div className="px-3 py-2 text-xs text-muted-foreground">加载中…</div>}
          {error && <div className="px-3 py-2 text-xs text-destructive">{error.message}</div>}
          {!isLoading && !error && (
            <>
              <KnowledgeList items={data?.facts ?? []} kind="fact" empty="还没有经过验证的事实。" onSelect={handleSelect} />
              <KnowledgeList items={data?.intuitions ?? []} kind="intuition" empty="" onSelect={handleSelect} />
              <KnowledgeList items={data?.tricks ?? []} kind="technique" empty="" onSelect={handleSelect} />
            </>
          )}
        </TabsContent>
        <TabsContent value="materials" className="scrollbar-thin">
          {materialsLoading ? (
            <div className="px-3 py-2 text-xs text-muted-foreground">加载中…</div>
          ) : (
            <KnowledgeList items={materials} kind="material" empty="还没有原始材料。" onSelect={handleSelect} />
          )}
          <div className="mb-1 mt-3 px-3 text-[10px] uppercase tracking-wide text-muted-foreground">来源</div>
          {graphLoading ? (
            <div className="px-3 py-2 text-xs text-muted-foreground">加载中…</div>
          ) : (
            <KnowledgeList items={sources} kind="source" empty="还没有来源记录。" onSelect={handleSelect} />
          )}
        </TabsContent>
      </Tabs>
      <KnowledgeDetailDialog
        item={selected?.item ?? null}
        kind={selected?.kind ?? null}
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      />
    </div>
  );
}
