export type FeedbackRating = 'satisfied' | 'unsatisfied';
export type FeedbackOutcome = 'completed' | 'failed';

export interface FeedbackPayload {
  rating: FeedbackRating;
  outcome: FeedbackOutcome;
  comment?: string;
  session_id?: string | null;
  problem_preview?: string;
}

export interface AdminFeedbackItem {
  id: string;
  user_id: string;
  label: string;
  phone_masked?: string;
  display_name?: string;
  rating: FeedbackRating;
  comment: string;
  outcome: FeedbackOutcome;
  problem_preview: string;
  session_id?: string | null;
  created_at: string;
}
