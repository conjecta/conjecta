import { useEffect, useState, useRef, type ClipboardEvent } from 'react';
import type { SolveAttachment, SolveRequest } from '@/types/websocket';
import type { Status } from '@/hooks/useSolveSocket';
import { useUiStore } from '@/store/ui';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { Send, Square, Paperclip, X, FileText, Image as ImageIcon } from 'lucide-react';

interface ComposerProps {
  sendProblem: (req: SolveRequest) => void;
  interrupt: () => void;
  status: Status;
  /** 'footer' pins to the bottom of the chat column; 'hero' sits centered in
   * the empty-state hero and autofocuses on mount. */
  variant?: 'footer' | 'hero';
}

export const MAX_COMPOSER_FILES = 8;
export const MAX_COMPOSER_ATTACHMENT_BYTES = 11 * 1024 * 1024;
const SUPPORTED_IMAGE_MIME_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
]);

/** Platform solve model — fixed; no user picker. */
export const PLATFORM_MODEL = 'openai/gpt-5.6-sol';
export const PLATFORM_MODEL_LABEL = 'GPT-5.6 Sol';

/** Official OpenAI blossom mark (viewBox 0 0 24 24). */
function OpenAILogo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className={className}
      fill="currentColor"
    >
      <path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0806 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.141.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.141-.0852-4.783-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0519V6.0652a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z" />
    </svg>
  );
}

export function fileKind(mime: string): 'image' | 'pdf' | null {
  if (SUPPORTED_IMAGE_MIME_TYPES.has(mime.toLowerCase())) return 'image';
  if (mime === 'application/pdf') return 'pdf';
  return null;
}

export function pastedImageName(mime: string, index: number): string {
  const subtype = mime.split('/')[1]?.toLowerCase() || 'png';
  const ext =
    subtype === 'jpeg' ? 'jpg' : subtype.replace(/[^a-z0-9]+/g, '') || 'png';
  return index === 1 ? `pasted-image.${ext}` : `pasted-image-${index}.${ext}`;
}

type LocalAttachment = Omit<SolveAttachment, 'data_url'> & {
  id: number;
  size: number;
  data_url: string | null;
};

type AttachmentReservation = {
  size: number;
  generation: number;
  reader: FileReader | null;
};

export function Composer({ sendProblem, interrupt, status, variant = 'footer' }: ComposerProps) {
  const [text, setText] = useState('');
  const [files, setFiles] = useState<LocalAttachment[]>([]);
  const [attachmentNotice, setAttachmentNotice] = useState<string | null>(null);
  const { selectedProjectId, selectedOwnerUserId, draftPrompt, draftPromptKey } = useUiStore();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const nextFileId = useRef(0);
  const pasteCountRef = useRef(0);
  const attachmentGenerationRef = useRef(0);
  const reservationsRef = useRef(new Map<number, AttachmentReservation>());

  const busy = status === 'connecting' || status === 'streaming' || status === 'background';
  const waitingForHuman = status === 'waiting_human';
  const hasPendingFiles = files.some((file) => !file.data_url);
  const readyFiles = files.filter(
    (file): file is LocalAttachment & { data_url: string } => Boolean(file.data_url),
  );
  const canSubmit = !waitingForHuman && !hasPendingFiles && Boolean(text.trim() || readyFiles.length > 0);

  // Populate from an example prompt / draft chosen elsewhere (e.g. the empty state).
  useEffect(() => {
    if (draftPromptKey === 0 || busy) return;
    setText(draftPrompt);
    const el = textareaRef.current;
    if (el) {
      el.focus();
      const end = draftPrompt.length;
      requestAnimationFrame(() => el.setSelectionRange(end, end));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftPromptKey]);

  // Hero variant greets the user with a focused input.
  useEffect(() => {
    if (variant === 'hero') textareaRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => {
    attachmentGenerationRef.current += 1;
    const reservations = Array.from(reservationsRef.current.values());
    reservationsRef.current.clear();
    for (const reservation of reservations) reservation.reader?.abort();
  }, []);

  const resetAttachments = () => {
    attachmentGenerationRef.current += 1;
    const reservations = Array.from(reservationsRef.current.values());
    reservationsRef.current.clear();
    for (const reservation of reservations) reservation.reader?.abort();
    setFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const attachFiles = (list: ArrayLike<File>) => {
    let reservedCount = reservationsRef.current.size;
    let reservedBytes = Array.from(reservationsRef.current.values()).reduce(
      (total, reservation) => total + reservation.size,
      0,
    );
    let nextNotice: string | null = null;

    Array.from(list).forEach((file) => {
      const kind = fileKind(file.type);
      if (!kind) {
        nextNotice ??= '仅支持 PNG、JPEG、WebP、GIF 或 PDF。';
        return;
      }
      if (reservedCount >= MAX_COMPOSER_FILES) {
        nextNotice ??= `最多可添加 ${MAX_COMPOSER_FILES} 个附件。`;
        return;
      }
      if (file.size > MAX_COMPOSER_ATTACHMENT_BYTES - reservedBytes) {
        nextNotice ??= '附件总大小不能超过 11 MB。';
        return;
      }

      const id = nextFileId.current++;
      const generation = attachmentGenerationRef.current;
      const reader = new FileReader();
      reservationsRef.current.set(id, { size: file.size, generation, reader });
      reservedCount += 1;
      reservedBytes += file.size;
      setFiles((prev) => [
        ...prev,
        { id, kind, data_url: null, name: file.name, size: file.size },
      ]);

      const discardPending = (message: string) => {
        const reservation = reservationsRef.current.get(id);
        if (!reservation || reservation.generation !== generation) return;
        reservationsRef.current.delete(id);
        setFiles((prev) => prev.filter((attachment) => attachment.id !== id));
        nextNotice ??= message;
        setAttachmentNotice(message);
      };
      reader.onload = () => {
        const reservation = reservationsRef.current.get(id);
        if (!reservation || reservation.generation !== generation) return;
        if (typeof reader.result !== 'string') {
          discardPending('无法读取附件，请重试。');
          return;
        }
        reservation.reader = null;
        setFiles((prev) => prev.map((attachment) => (
          attachment.id === id
            ? { ...attachment, data_url: reader.result as string }
            : attachment
        )));
      };
      reader.onerror = () => discardPending('无法读取附件，请重试。');
      reader.onabort = () => discardPending('附件读取已取消。');
      try {
        reader.readAsDataURL(file);
      } catch {
        discardPending('无法读取附件，请重试。');
      }
    });
    setAttachmentNotice(nextNotice);
  };

  const onPick = (list: FileList | null) => {
    attachFiles(list ?? []);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    if (busy) return;
    const items = Array.from(e.clipboardData?.items ?? []);
    const imageFiles: File[] = [];
    for (const item of items) {
      if (!item.type.startsWith('image/')) continue;
      const blob = item.getAsFile();
      if (!blob) continue;
      pasteCountRef.current += 1;
      const name = pastedImageName(item.type, pasteCountRef.current);
      imageFiles.push(new File([blob], name, { type: item.type }));
    }
    if (imageFiles.length === 0) return;
    e.preventDefault();
    attachFiles(imageFiles);
  };

  const removeFile = (id: number) => {
    const reservation = reservationsRef.current.get(id);
    reservationsRef.current.delete(id);
    reservation?.reader?.abort();
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const doSend = () => {
    const problem =
      text.trim() || (readyFiles.length > 0 ? '请根据附件中的题目进行求解。' : '');
    if (!problem) return;
    sendProblem({
      problem,
      project_id: selectedProjectId,
      ...(selectedOwnerUserId ? { owner_user_id: selectedOwnerUserId } : {}),
      mode: 'auto',
      model: PLATFORM_MODEL,
      files: readyFiles.length
        ? readyFiles.map(({ kind, data_url, name }) => ({ kind, data_url, name }))
        : undefined,
    });
    setText('');
    resetAttachments();
    setAttachmentNotice(null);
  };

  const submit = () => {
    if (!canSubmit || busy || waitingForHuman) return;
    const problem =
      text.trim() || (readyFiles.length > 0 ? '请根据附件中的题目进行求解。' : '');
    if (!problem) return;
    doSend();
  };

  return (
    <footer
      className={
        variant === 'hero'
          ? 'w-full'
          : 'relative shrink-0 bg-gradient-to-t from-background via-background to-transparent px-3 pb-3 pt-2 sm:px-5 sm:pb-5'
      }
    >
      <div className={variant === 'hero' ? 'w-full' : 'mx-auto max-w-[900px]'}>
      {attachmentNotice && (
        <div role="alert" className="mb-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {attachmentNotice}
        </div>
      )}
      {files.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {files.map((f) => (
            <span
              key={f.id}
              className="flex items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-xs shadow-sm"
            >
              {f.kind === 'image' ? <ImageIcon size={12} /> : <FileText size={12} />}
              {f.name}
              {!f.data_url && <span className="text-muted-foreground">读取中…</span>}
              <button
                type="button"
                aria-label={`remove ${f.name}`}
                onClick={() => removeFile(f.id)}
                className="ml-1 text-muted-foreground hover:text-foreground"
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="overflow-hidden rounded-[20px] border border-border/80 bg-card/95 shadow-[0_1px_2px_hsl(var(--foreground)/0.04),0_22px_50px_-28px_hsl(var(--foreground)/0.4)] backdrop-blur-xl transition-all duration-300 focus-within:border-primary/40 focus-within:shadow-[0_1px_2px_hsl(var(--foreground)/0.04),0_26px_60px_-26px_hsl(var(--primary)/0.45)] focus-within:ring-4 focus-within:ring-ring/10">
        <Textarea
          ref={textareaRef}
          placeholder="写下要证明、计算或研究的问题… 按Shift+Enter换行"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onPaste={onPaste}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          disabled={busy || waitingForHuman}
          className="min-h-[84px] resize-none border-0 bg-transparent px-5 py-4 text-[15px] leading-[1.7] placeholder:text-muted-foreground/60 focus:ring-0"
        />
        <div className="flex items-center justify-between border-t border-border/50 bg-muted/20 px-2.5 py-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,application/pdf"
            multiple
            className="hidden"
            onChange={(e) => onPick(e.target.files)}
          />
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || waitingForHuman}
              aria-label="Attach file"
              title="添加图片或 PDF"
            >
              <Paperclip size={12} />
            </Button>
            <span
              title="由 OpenAI GPT-5.6 Sol 求解"
              aria-label="模型 OpenAI GPT-5.6 Sol"
              className="ml-0.5 inline-flex h-8 items-center gap-1.5 rounded-lg bg-muted/80 px-2.5 text-[11px] font-semibold tracking-wide text-foreground ring-1 ring-border/60"
            >
              <OpenAILogo className="h-3.5 w-3.5 shrink-0" />
              {PLATFORM_MODEL_LABEL}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {busy && (
              <span className="text-xs text-muted-foreground animate-pulse">
                {status === 'connecting'
                  ? '连接中…'
                  : status === 'background'
                    ? '后台研究中…'
                    : '推理中…'}
              </span>
            )}
            {waitingForHuman && (
              <span className="text-xs font-medium text-primary">请在上方卡片中作出决定</span>
            )}
            {busy ? (
              <Button variant="outline" size="sm" onClick={interrupt}>
                <Square size={12} className="mr-1" />
                停止
              </Button>
            ) : (
              <Button size="sm" onClick={submit} disabled={!canSubmit}>
                <Send size={13} className="mr-1" />
                求解
              </Button>
            )}
          </div>
        </div>
      </div>
      </div>
    </footer>
  );
}
