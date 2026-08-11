export const DEFAULT_PUBLIC_ERROR = '服务暂时遇到问题，请稍后重试。';

export const QUOTA_EXCEEDED_MESSAGE =
  '今日免费额度已用完，请在「用量与 API Key」中绑定自己的 API Key 后继续使用。';

export const CLOUD_STORAGE_REQUIRED_MESSAGE =
  '好友与协作功能需要云端存储。请在项目根目录 .env 中配置 SUPABASE_URL 与 SUPABASE_SERVICE_ROLE_KEY，执行 docs/supabase_social_collab_schema.sql 后重启服务。详见 docs/local-friends-setup.md。';

export const FRIENDS_UNAVAILABLE_MESSAGE = '好友服务暂时不可用，请稍后重试。';

export function publicErrorMessage(status?: number): string {
  if (status === 400 || status === 422) return '请求内容有误，请检查后重试。';
  if (status === 401 || status === 403) return '登录状态已失效，请重新登录后再试。';
  if (status === 404) return '没有找到请求的内容。';
  if (status === 413) return '这次提交的内容过大，请减少附件或缩短内容后重试。';
  if (status === 429) return '当前请求较多，请稍后重试。';
  return DEFAULT_PUBLIC_ERROR;
}

export function messageFromStatusAndDetail(status: number, detail?: unknown): string {
  if (status === 429 && detail === 'DAILY_QUOTA_EXCEEDED') {
    return QUOTA_EXCEEDED_MESSAGE;
  }
  if (status === 503 && detail === 'CLOUD_STORAGE_REQUIRED') {
    return CLOUD_STORAGE_REQUIRED_MESSAGE;
  }
  if (status === 502 && detail === FRIENDS_UNAVAILABLE_MESSAGE) {
    return FRIENDS_UNAVAILABLE_MESSAGE;
  }
  return publicErrorMessage(status);
}

export function isQuotaExceededMessage(message: string | null | undefined): boolean {
  return message === QUOTA_EXCEEDED_MESSAGE;
}

const PUBLIC_ERRORS = new Set([
  DEFAULT_PUBLIC_ERROR,
  QUOTA_EXCEEDED_MESSAGE,
  CLOUD_STORAGE_REQUIRED_MESSAGE,
  FRIENDS_UNAVAILABLE_MESSAGE,
  ...[400, 401, 403, 404, 413, 422, 429].map(publicErrorMessage),
]);

export function isPublicErrorMessage(message: string): boolean {
  return PUBLIC_ERRORS.has(message);
}

export function isCloudStorageRequiredMessage(message: string): boolean {
  return message === CLOUD_STORAGE_REQUIRED_MESSAGE;
}

/** Map an HTTP error response to a user-visible message (handles quota 429 specially). */
export async function messageFromErrorResponse(res: Response): Promise<string> {
  let detail: unknown;
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    detail = body?.detail;
  } catch {
    // ignore parse errors
  }
  return messageFromStatusAndDetail(res.status, detail);
}
