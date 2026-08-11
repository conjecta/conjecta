export interface ProjectDoc {
  id: string;
  name?: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

import type { ToolEvidence } from './websocket';

export interface ProjectTurn {
  id: string;
  conversation_id?: string;
  role?: 'user' | 'assistant';
  problem: string;
  answer: string;
  attachments?: Array<{ kind?: string; name?: string; [key: string]: unknown }>;
  created_at?: string;
  verification_status?: string;
  strategy?: string;
  lean_proofs?: string[];
  verification_issues?: string[];
  tool_evidence?: ToolEvidence[];
  session_id?: string;
  [key: string]: unknown;
}

export interface ProjectConversation {
  id: string;
  title: string;
  turns: ProjectTurn[];
  created_at?: string;
  updated_at?: string;
}

export interface Project {
  id: string;
  name: string;
  doc: ProjectDoc;
  starred?: boolean;
  updatedAt?: string;
  turns?: ProjectTurn[];
  owner_user_id?: string;
  role?: 'lead' | 'collaborator' | string;
}

export interface ReviewQueueItem {
  id: string;
  status: 'open' | 'held' | 'approved' | 'rejected';
  reason?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface Fact {
  id: string;
  statement: string;
  statement_zh?: string;
  why_zh?: string;
  source?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface Intuition {
  id: string;
  text: string;
  title_zh?: string;
  body_zh?: string;
  source?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface Trick {
  id: string;
  name: string;
  description: string;
  title_zh?: string;
  body_zh?: string;
  [key: string]: unknown;
}

export interface Material {
  id: string;
  project_id?: string;
  kind?: string;
  label?: string;
  text: string;
  source?: string;
  status?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface Source {
  id: string;
  title: string;
  url?: string;
  [key: string]: unknown;
}

export interface KnowledgeBundle {
  facts: Fact[];
  intuitions: Intuition[];
  tricks: Trick[];
}

export type KnowledgeGraphNodeKind = 'fact' | 'intuition' | 'technique' | 'material' | 'source';

export interface KnowledgeGraphNode {
  id: string;
  kind: KnowledgeGraphNodeKind | string;
  label: string;
  body?: string;
  status?: string;
  confidence?: number;
  source?: string;
  created_at?: string;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeGraphEdge {
  id: string;
  source: string;
  target: string;
  kind: 'derived_from' | 'supports' | 'uses' | 'related_to' | 'contradicts' | 'references' | string;
  label?: string;
  evidence?: string;
  weight?: number;
  created_at?: string;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeGraphResponse {
  ok: boolean;
  project_id: string;
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  summary?: string;
  source: string;
  error?: string;
}

export interface NextStepSuggestion {
  id: string;
  title: string;
  action: 'invoke_lean' | 'extract_knowledge' | 'open_subsession' | 'fetch_reference' | 'note';
  detail: string;
  target_step?: string;
}

export interface LeanJob {
  id: string;
  code: string;
  status: 'pending' | 'running' | 'completed' | 'error';
  output?: string;
  error?: string;
}

export interface ApiError {
  detail?: string;
  error?: string;
  message?: string;
}

export interface MathNewsItem {
  id: string;
  source: 'quanta' | 'arxiv';
  title_zh: string;
  summary_zh: string;
  url: string;
  published_at: string;
  fetched_at: string;
}

export interface MathNewsResponse {
  items: MathNewsItem[];
  updated_at: string | null;
}
