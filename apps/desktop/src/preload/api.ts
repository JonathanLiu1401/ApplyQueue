import type { AppInfo } from "../shared/ipc";

export type ConfigFile = "profile.json" | "preferences.json" | "answers.json" | "accounts.json";
export type ValidationResult = { ok: true } | { ok: false; issues: { path: string; message: string }[] };

/** The only surface the renderer can reach. Keep it small and typed. */
export interface ApplyQueueApi {
  appInfo(): Promise<AppInfo>;
  validateConfig(file: ConfigFile, data: unknown): Promise<ValidationResult>;
}
