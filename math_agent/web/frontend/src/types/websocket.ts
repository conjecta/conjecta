export type WsEventType =
  | 'session'
  | 'turn_started'
  | 'problem_extracted'
  | 'stage_status'
  | 'llm_start'
  | 'token'
  | 'step_start'
  | 'tool_start'
  | 'tool_done'
  | 'lean'
  | 'lean_verification'
  | 'signal'
  | 'goal_evaluation'
  | 'proof_graph'
  | 'capability_health'
  | 'checkpoint'
  | 'human_input_required'
  | 'error'
  | 'interrupted'
  | 'done'
  | string;

export interface WsBaseEvent {
  type: WsEventType;
}

export interface WsSessionEvent extends WsBaseEvent {
  type: 'session';
  session_id: string;
}

export interface WsTurnStartedEvent extends WsBaseEvent {
  type: 'turn_started';
  turn_id: string;
  conversation_id?: string;
  project_id?: string;
  problem?: string;
}

export interface WsProblemExtractedEvent extends WsBaseEvent {
  type: 'problem_extracted';
  problem: string;
}

export interface WsStageStatusEvent extends WsBaseEvent {
  type: 'stage_status';
  stage: string;
  message?: string;
  step_num?: number;
  total_steps?: number;
  /** "status_bar" = sticky status only; omit/timeline = also show in process log. */
  ui?: 'status_bar' | 'timeline';
}

export interface WsLlmStartEvent extends WsBaseEvent {
  type: 'llm_start';
  label?: string;
}

export interface WsTokenEvent extends WsBaseEvent {
  type: 'token';
  content: string;
}

export interface WsToolStartEvent extends WsBaseEvent {
  type: 'tool_start';
  tool: string;
  input?: unknown;
}

export interface WsToolDoneEvent extends WsBaseEvent {
  type: 'tool_done';
  tool: string;
  output?: unknown;
}

export interface WsStepStartEvent extends WsBaseEvent {
  type: 'step_start';
  step_num?: number;
  action?: string;
}

export interface WsLeanEvent extends WsBaseEvent {
  type: 'lean';
  status?: string;
  statement?: string;
  details?: string;
  output?: string;
}

export interface WsLeanVerificationEvent extends WsBaseEvent {
  type: 'lean_verification';
  status: 'verified' | 'failed' | 'error';
  statement?: string;
  details?: string;
}

export interface WsGoalEvaluationEvent extends WsBaseEvent {
  type: 'goal_evaluation';
  score?: number;
  reasoning?: string;
}

export interface WsProofGraphEvent extends WsBaseEvent {
  type: 'proof_graph';
  proof_graph: {
    root_id?: string;
    active_goal_id?: string;
    goals?: Array<{
      id?: string;
      statement?: string;
      status?: string;
      depends_on?: string[];
      verification_status?: string;
    }>;
  };
}

export interface WsCapabilityHealthEvent extends WsBaseEvent {
  type: 'capability_health';
  capabilities?: Record<string, { status?: string; error?: string; tool_count?: number }>;
}

export interface WsSignalEvent extends WsBaseEvent {
  type: 'signal';
  signal: string;
  stage?: string;
  payload?: unknown;
  data?: unknown;
  message?: string;
}

export interface WsCheckpointEvent extends WsBaseEvent {
  type: 'checkpoint';
  checkpoint_id: string;
  resumable: boolean;
  reason?: string;
}

export type HumanDecision = 'approve' | 'reject' | 'edit' | 'respond';

export interface WsHumanInputRequiredEvent extends WsBaseEvent {
  type: 'human_input_required';
  checkpoint_id: string;
  request_id: string;
  kind: string;
  stage: string;
  question: string;
  details?: Record<string, unknown>;
  allowed_decisions: HumanDecision[];
  resumable: true;
}

export interface WsErrorEvent extends WsBaseEvent {
  type: 'error';
  message: string;
}

export interface WsInterruptedEvent extends WsBaseEvent {
  type: 'interrupted';
  message: string;
  checkpoint_id?: string;
  resumable?: boolean;
}

/** One tool invocation recorded as verification evidence for a finished turn. */
export interface ToolEvidence {
  name: string;
  args_preview?: string;
  success?: boolean;
  output_preview?: string;
  duration_seconds?: number;
}

export interface WsDoneEvent extends WsBaseEvent {
  type: 'done';
  summary?: string;
  final_answer?: string;
  lean_proofs?: string[];
  strategy?: string;
  verification_status?: 'verified' | 'reviewed' | 'unreviewed' | 'best_effort' | 'blocked';
  verification_issues?: string[];
  tool_evidence?: ToolEvidence[];
}

export interface WsUnknownEvent extends WsBaseEvent {
  type: string;
  [key: string]: unknown;
}

export type WsEvent =
  | WsSessionEvent
  | WsTurnStartedEvent
  | WsProblemExtractedEvent
  | WsStageStatusEvent
  | WsLlmStartEvent
  | WsTokenEvent
  | WsStepStartEvent
  | WsToolStartEvent
  | WsToolDoneEvent
  | WsLeanEvent
  | WsLeanVerificationEvent
  | WsSignalEvent
  | WsGoalEvaluationEvent
  | WsProofGraphEvent
  | WsCapabilityHealthEvent
  | WsCheckpointEvent
  | WsHumanInputRequiredEvent
  | WsErrorEvent
  | WsInterruptedEvent
  | WsDoneEvent
  | WsUnknownEvent;

export interface SolveAttachment {
  kind: 'image' | 'pdf';
  data_url: string;
  name: string;
}

export interface SolveRequest {
  problem: string;
  conversation_id?: string;
  model?: string;
  project_id?: string;
  owner_user_id?: string;
  checkpoint_id?: string;
  mode?: 'auto' | 'react' | 'research';
  files?: SolveAttachment[];
  conversation_history?: Array<{ role: string; text: string }>;
}

export interface HumanDecisionRequest {
  request_id: string;
  decision: HumanDecision;
  feedback?: string;
  edited_action?: { name: string; args: Record<string, unknown> };
}

export interface GoalActionRequest {
  action: 'retry' | 'edit';
  statement?: string;
  guidance?: string;
}
