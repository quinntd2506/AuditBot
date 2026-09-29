export interface Bot {
  id: number;
  name: string;
  platform: string;
  mode: 'auto' | 'review';
  skills: string[];
  status: 'idle' | 'auditing' | 'submitting' | 'paused';
  created_at: string;
}

export interface Contest {
  id: number;
  bot_id: number;
  bot_name?: string;
  title: string;
  code_urls: string[];
  status: 'active' | 'upcoming' | 'completed' | 'auditing' | 'submitting';
  earnings: number;
  platform_url?: string;
  synced_from?: string;
  prize_pool?: number;
  token?: string;
  logo_url?: string;
  starts_at?: string;
  ends_at?: string;
  description?: string;
  metadata?: Record<string, unknown>;
  is_finalized?: boolean;
  has_joined?: boolean;
  skip_autojoin?: boolean;
}

export interface Issue {
  id: number;
  contest_id: number;
  platform_issue_id?: string;
  title: string;
  description: string;
  severity: 'High' | 'Medium' | 'Low' | 'Informational';
  classification: string;
  status: 'pending' | 'submitted' | 'valid' | 'invalid' | 'duplicate' | 'verified' | 'skipped' | 'unsupported';
  confidence?: string;
  poc_code?: string;
  submission_report?: string;
  linked_issue_id?: number | null;
}

export interface Skill {
  id: number;
  name: string;
  description: string;
  content: string;
  phases: ('triage' | 'poc' | 'report')[];
  frameworks: string[];
  platforms: string[];
  gate?: string | null;
  created_at: string;
}

export interface AppNotification {
  id: number;
  contest_id: number;
  issue_id?: number | null;
  message: string;
  is_read: boolean;
  created_at: string;
  contest_title?: string;
}

export interface VerificationJob {
  id: string;
  issue_ids: number[];
  status: 'pending' | 'running' | 'done' | 'failed';
  results: Record<string, {
    status: 'queued' | 'verifying' | 'verified' | 'unverified' | 'skipped' | 'failed' | 'invalid' | 'unsupported';
    poc_code?: string;
    reason?: string;
    error?: string;
    framework?: string;
    attempts?: number;
    command?: string;
    stdout?: string;
    stderr?: string;
  }>;
  created_at: string;
  updated_at: string;
}
