import { apiFetch } from '@/api/client';
import type { AdminFeedbackItem, FeedbackPayload } from '@/types/feedback';

export function submitFeedback(body: FeedbackPayload) {
  return apiFetch<{ ok: boolean; feedback: Record<string, unknown> }>('/api/feedback', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function fetchAdminFeedback(params?: { limit?: number; rating?: string }) {
  const query = new URLSearchParams();
  if (params?.limit) query.set('limit', String(params.limit));
  if (params?.rating) query.set('rating', params.rating);
  const suffix = query.toString() ? `?${query}` : '';
  return apiFetch<{ ok: boolean; feedback: AdminFeedbackItem[] }>(
    `/api/admin/feedback${suffix}`,
  );
}
