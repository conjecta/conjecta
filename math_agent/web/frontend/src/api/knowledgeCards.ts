import { apiFetch, buildQuery } from '@/api/client';
import type { KnowledgeCardDetail, KnowledgeCardSummary, CardRevision } from '@/types/knowledgeCards';

export interface PublishPayload {
  title?: string;
  statement?: string;
  body?: string;
  tags?: string[];
  domain?: string;
  visibility?: 'private' | 'friends' | 'team' | 'public';
  source_run_session_id?: string;
  source_run_share_token?: string;
}

export function publishKnowledgeCard(
  projectId: string,
  kind: string,
  itemId: string,
  payload: PublishPayload,
): Promise<{ ok: boolean; card: KnowledgeCardSummary; revision: CardRevision }> {
  return apiFetch(`/api/projects/${encodeURIComponent(projectId)}/knowledge/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}/publish`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function publishTurnCard(
  projectId: string,
  turnId: string,
  payload: PublishPayload,
): Promise<{ ok: boolean; card: KnowledgeCardSummary; revision: CardRevision }> {
  return apiFetch(`/api/projects/${encodeURIComponent(projectId)}/turns/${encodeURIComponent(turnId)}/publish-card`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function listMyCards(): Promise<{ ok: boolean; cards: KnowledgeCardSummary[] }> {
  return apiFetch('/api/knowledge-cards');
}

export function listPublicCards(params: { q?: string; tags?: string; limit?: number; offset?: number } = {}) {
  return apiFetch<{ ok: boolean; cards: KnowledgeCardSummary[] }>(`/api/knowledge-cards/public${buildQuery(params)}`);
}

export function listFriendCards(params: { q?: string; tags?: string; limit?: number; offset?: number } = {}) {
  return apiFetch<{ ok: boolean; cards: KnowledgeCardDetail[] }>(`/api/knowledge-cards/friends${buildQuery(params)}`);
}

export function getCard(cardId: string): Promise<{ ok: boolean; card: KnowledgeCardDetail }> {
  return apiFetch(`/api/knowledge-cards/${encodeURIComponent(cardId)}`);
}

export function importCard(cardId: string, targetProjectId: string): Promise<{ ok: boolean; imported: Record<string, unknown> }> {
  return apiFetch(`/api/knowledge-cards/${encodeURIComponent(cardId)}/import`, {
    method: 'POST',
    body: JSON.stringify({ target_project_id: targetProjectId }),
  });
}

export function exportCard(
  cardId: string,
  format: string,
): Promise<{ ok: boolean; format: string; content: string }> {
  return apiFetch(`/api/knowledge-cards/${encodeURIComponent(cardId)}/export/${encodeURIComponent(format)}`);
}
