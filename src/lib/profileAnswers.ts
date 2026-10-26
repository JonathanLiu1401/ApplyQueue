// Profile answers the user never gave are "not answered" (null / "" / []), never a preset.
// Mirrors backend/core/profile_presets.py: the backend decides what the apply engine may use; this
// copy only drives the Profile page's live "Needs your answer" flags as the user edits.
import type { CandidateProfile } from "./types";

export type AnswerState = "unset" | "unconfirmed";

export interface UnansweredField {
  field: string;
  state: AnswerState;
  blocksApply: boolean;
}

/** Preset paths, in page order. */
export const PRESET_FIELDS = [
  "visa_sponsorship_needed",
  "willing_to_relocate",
  "preferred_work_mode",
  "salary_expectation.min",
  "salary_expectation.max",
  "years_of_experience",
  "languages",
  "country",
  "address.country",
  "phone_country_code",
] as const;

/** Questions only the candidate can answer: a form asking one while unset goes to a person. */
export const PREFERENCE_FIELDS: readonly string[] = [
  "visa_sponsorship_needed",
  "willing_to_relocate",
  "preferred_work_mode",
  "salary_expectation.min",
];

export function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const part of path.split(".")) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

export function isUnsetValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === "string") return v.trim() === "";
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "number") return Number.isNaN(v);
  return false;
}

export function confirmList(profile: Partial<CandidateProfile>): string[] {
  const raw = profile._confirm;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (p, i) => typeof p === "string" && (PRESET_FIELDS as readonly string[]).includes(p)
      && raw.indexOf(p) === i && !isUnsetValue(getPath(profile, p)),
  );
}

export function unansweredFields(profile: Partial<CandidateProfile>): UnansweredField[] {
  const pending = new Set(confirmList(profile));
  const out: UnansweredField[] = [];
  for (const field of PRESET_FIELDS) {
    if (field === "salary_expectation.max" && !pending.has(field) && !pending.has("salary_expectation.min")
        && !isUnsetValue(getPath(profile, "salary_expectation.min"))) {
      continue; // a minimum alone is a complete salary answer
    }
    const state: AnswerState | null = pending.has(field)
      ? "unconfirmed"
      : isUnsetValue(getPath(profile, field)) ? "unset" : null;
    if (state) out.push({ field, state, blocksApply: PREFERENCE_FIELDS.includes(field) });
  }
  return out;
}

/** "yes" / "no" / "" (not answered) for a tri-state boolean. */
export function triValue(v: boolean | null | undefined): "" | "yes" | "no" {
  return v === true ? "yes" : v === false ? "no" : "";
}

export function fromTri(v: string): boolean | null {
  return v === "yes" ? true : v === "no" ? false : null;
}

/** Number input text -> number, or null when empty (not answered). */
export function numberOrNull(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}
