import type { JobStatus } from "./types";

/**
 * Job statuses (mirrors backend/core/job_status.py).
 * A job counts as submitted only when ApplyQueue read a confirmation from the
 * page itself (submitted_unconfirmed), and is confirmed once a confirmation
 * email is matched too. The agent's own "success" never counts (#89).
 */
export const SUBMITTED_STATUSES: readonly JobStatus[] = ["submitted_unconfirmed", "confirmed"];
export const MANUAL_STATUSES: readonly JobStatus[] = [
  "pending",
  "submitted_unconfirmed",
  "confirmed",
  "needs_human",
  "failed",
  "blocked",
];

/** Map LangHire's legacy "applied" onto submitted_unconfirmed. */
export function normalizeStatus(status: string | undefined | null): JobStatus {
  if (status === "applied") return "submitted_unconfirmed";
  return (status || "pending") as JobStatus;
}

export function isSubmitted(status: string | undefined | null): boolean {
  return SUBMITTED_STATUSES.includes(normalizeStatus(status));
}

export const STATUS_I18N_KEY: Record<JobStatus, string> = {
  pending: "status.pending",
  in_progress: "status.inProgress",
  submitted_unconfirmed: "status.submittedUnconfirmed",
  confirmed: "status.confirmed",
  needs_human: "status.needsHuman",
  failed: "status.failed",
  blocked: "status.blocked",
};

export const STATUS_FALLBACK_LABEL: Record<JobStatus, string> = {
  pending: "Pending",
  in_progress: "In Progress",
  submitted_unconfirmed: "Submitted (awaiting email)",
  confirmed: "Confirmed",
  needs_human: "Needs you",
  failed: "Failed",
  blocked: "Blocked",
};

/** Best date for "when was this submitted" (legacy rows only have applied_at). */
export function submittedDate(job: { submitted_at?: string | null; applied_at?: string | null }): string {
  return job.submitted_at || job.applied_at || "";
}
