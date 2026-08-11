export type CardVisibility = 'private' | 'friends' | 'team' | 'public';
export type CardStatus = 'draft' | 'published' | 'archived';

export interface KnowledgeCardSummary {
  id: string;
  owner_user_id: string;
  project_id: string;
  source_item_id: string;
  source_item_kind: string;
  latest_revision_id: string;
  visibility: CardVisibility;
  status: CardStatus;
  citation_count: number;
  star_count: number;
  created_at: string;
  updated_at: string;
}

export interface CardRevision {
  id: string;
  card_id: string;
  revision_number: number;
  title: string;
  statement: string;
  body: string;
  formal_status: string;
  lean_name: string;
  lean_code: string;
  evidence_id: string;
  source_run_session_id: string;
  source_run_share_token: string;
  tags: string[];
  domain: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface KnowledgeCardDetail {
  card: KnowledgeCardSummary;
  revision: CardRevision;
}
