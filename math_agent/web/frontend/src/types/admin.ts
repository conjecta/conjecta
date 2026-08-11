export interface AdminSummary {
  users: number;
  active_users: number;
  runs: number;
  completed_runs: number;
  failed_runs: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  avg_tokens_per_run: number;
}

export interface AdminDailyPoint {
  date: string;
  tokens: number;
  runs: number;
  users: number;
}

export interface AdminUserRow {
  id: string;
  phone: string;
  created_at: string;
  last_login_at: string;
  last_active_at: string;
  runs: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
}

export interface AdminRunRecord {
  id: string;
  user_id: string;
  phone: string;
  project_id: string;
  problem: string;
  answer?: string;
  mode: string;
  model: string;
  status: string;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
}

export interface AdminOverview {
  ok: boolean;
  period_days: number;
  summary: AdminSummary;
  daily: AdminDailyPoint[];
  users: AdminUserRow[];
  records: AdminRunRecord[];
  generated_at: string;
}
