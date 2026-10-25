import { z } from "zod";
import { Accounts } from "./accounts";
import { Answers } from "./answers";
import { Preferences } from "./preferences";
import { Profile } from "./profile";

export * from "./common";
export * from "./profile";
export * from "./preferences";
export * from "./answers";
export * from "./accounts";

/** File name (under userData/config/) -> schema. */
export const ConfigFiles = {
  "profile.json": Profile,
  "preferences.json": Preferences,
  "answers.json": Answers,
  "accounts.json": Accounts,
} as const;

export type ConfigFileName = keyof typeof ConfigFiles;

export type ValidationResult =
  | { ok: true }
  | { ok: false; issues: { path: string; message: string }[] };

/** Validate one config file's parsed JSON. Never echoes field values back (they may be personal). */
export function validateConfig(file: ConfigFileName, data: unknown): ValidationResult {
  const result = ConfigFiles[file].safeParse(data);
  if (result.success) return { ok: true };
  return {
    ok: false,
    issues: result.error.issues.map((i) => ({ path: i.path.join("."), message: i.message })),
  };
}

export { z };
