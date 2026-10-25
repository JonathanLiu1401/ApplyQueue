import { z } from "zod";
import { Id, SCHEMA_VERSION } from "./common";

/**
 * Account METADATA only. Passwords and browser sessions never live in this file:
 * they live in the user's private sync repo (credentials/, sessions/) and the local vault mirror.
 */
export const EmailAccount = z.object({
  id: Id,
  provider: z.enum(["gmail", "outlook", "imap"]),
  address: z.email(),
  confirmationLabel: z.string().min(1).default("Job Applications"),
  archiveAfterLabel: z.boolean().default(true),
});

export const PortalAccount = z
  .object({
    id: Id,
    loginUrl: z.url().or(z.literal("")).default(""),
    username: z.string().default(""),
    signInWith: z.enum(["password", "google", "microsoft", "linkedin", "apple"]).default("password"),
    /** Opaque reference into the credential store, e.g. "applyqueue/portal/workday:examplecorp". */
    keychainRef: z.string().default(""),
    notes: z.string().default(""),
  })
  .strict(); // reject unknown keys such as "password"

export const ResumeRule = z.object({
  match: z.object({ titleRegex: z.string().min(1) }),
  resumeId: Id,
});

export const Accounts = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    email: z.array(EmailAccount).default([]),
    portals: z.array(PortalAccount).default([]),
    resumes: z
      .object({
        items: z.array(z.object({ id: Id, path: z.string().min(1) })).default([]),
        rules: z.array(ResumeRule).default([]),
        default: Id.optional(),
      })
      .default({ items: [], rules: [] }),
  })
  .superRefine((a, ctx) => {
    const ids = new Set(a.resumes.items.map((r) => r.id));
    if (a.resumes.default && !ids.has(a.resumes.default)) {
      ctx.addIssue({ code: "custom", path: ["resumes", "default"], message: "default resume id not found in items" });
    }
    a.resumes.rules.forEach((r, i) => {
      if (!ids.has(r.resumeId)) {
        ctx.addIssue({ code: "custom", path: ["resumes", "rules", i, "resumeId"], message: "resume id not found in items" });
      }
    });
  });

export type Accounts = z.infer<typeof Accounts>;
