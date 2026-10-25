import { z } from "zod";
import { Id, IsoDate, SCHEMA_VERSION } from "./common";

export const RoleType = z.enum(["internship", "new-grad", "full-time", "part-time", "contract", "any"]);

/** A user-defined job category (tier). Works for any field: software, nursing, finance, trades... */
export const Category = z.object({
  id: Id,
  label: z.string().min(1),
  /** 1 = highest priority. */
  priority: z.number().int().min(1).max(100),
  /** false = track but never apply. */
  apply: z.boolean().default(true),
  /** Case-insensitive keywords; any match puts a posting in this category. */
  include: z.array(z.string().min(1)).min(1),
  /** Case-insensitive keywords that disqualify a posting from this category. */
  exclude: z.array(z.string().min(1)).default([]),
  resumeId: Id.optional(),
});

export const Term = z
  .object({ id: Id, label: z.string().min(1), start: IsoDate.optional(), end: IsoDate.optional() })
  .refine((t) => !t.start || !t.end || t.start <= t.end, { message: "term.start must be on or before term.end" });

export const Preferences = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    roleType: RoleType.default("any"),
    term: Term.optional(),
    categories: z.array(Category).default([]),
    locations: z
      .object({
        /** e.g. "US:*", "US:WA", "remote" */
        include: z.array(z.string()).default([]),
        exclude: z.array(z.string()).default([]),
        weights: z.record(z.string(), z.number().min(0)).default({}),
      })
      .default({ include: [], exclude: [], weights: {} }),
    companies: z
      .object({
        banned: z.array(z.string()).default([]),
        bannedIndustries: z.array(z.string()).default([]),
        targets: z.array(z.string()).default([]),
        notes: z.record(z.string(), z.string()).default({}),
      })
      .default({ banned: [], bannedIndustries: [], targets: [], notes: {} }),
    limits: z
      .object({
        dailyCap: z.number().int().min(0).default(50),
        perDomainGapSec: z.number().int().min(0).default(8),
        linkedinEasyApply: z
          .object({
            minGapSec: z.number().int().min(0).default(60),
            maxGapSec: z.number().int().min(0).default(90),
            dailyMax: z.number().int().min(0).default(30),
          })
          .refine((l) => l.minGapSec <= l.maxGapSec, { message: "minGapSec must be <= maxGapSec" })
          .default({ minGapSec: 60, maxGapSec: 90, dailyMax: 30 }),
      })
      .default({ dailyCap: 50, perDomainGapSec: 8, linkedinEasyApply: { minGapSec: 60, maxGapSec: 90, dailyMax: 30 } }),
    apply: z
      .object({
        autoSubmit: z.boolean().default(false),
        /** A submission only counts after a real confirmation email. */
        requireEmailProof: z.boolean().default(true),
      })
      .default({ autoSubmit: false, requireEmailProof: true }),
  })
  .superRefine((p, ctx) => {
    const seen = new Set<string>();
    p.categories.forEach((c, i) => {
      if (seen.has(c.id)) {
        ctx.addIssue({ code: "custom", path: ["categories", i, "id"], message: `Duplicate category id "${c.id}"` });
      }
      seen.add(c.id);
    });
  });

export type Category = z.infer<typeof Category>;
export type Preferences = z.infer<typeof Preferences>;
export type PreferencesInput = z.input<typeof Preferences>;
