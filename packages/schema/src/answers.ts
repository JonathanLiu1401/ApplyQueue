import { z } from "zod";
import { Id, SCHEMA_VERSION } from "./common";

export const AnswerSource = z.enum(["user", "learned", "derived"]);

export const AnswerEntry = z.object({
  id: Id,
  /** Case-insensitive substrings or /regex/ strings matched against the question text. */
  patterns: z.array(z.string().min(1)).min(1),
  answer: z.string().min(1),
  scope: z.object({ company: z.string().nullable().default(null) }).default({ company: null }),
  source: AnswerSource.default("user"),
  /** ISO timestamp when the user confirmed this answer. Learned answers start unconfirmed. */
  confirmedAt: z.iso.datetime().optional(),
});

export const Answers = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    bank: z.array(AnswerEntry).default([]),
    freeText: z.record(z.string(), z.string()).default({}),
  })
  .superRefine((a, ctx) => {
    const seen = new Set<string>();
    a.bank.forEach((e, i) => {
      if (seen.has(e.id)) {
        ctx.addIssue({ code: "custom", path: ["bank", i, "id"], message: `Duplicate answer id "${e.id}"` });
      }
      seen.add(e.id);
    });
  });

export type AnswerEntry = z.infer<typeof AnswerEntry>;
export type Answers = z.infer<typeof Answers>;
export type AnswersInput = z.input<typeof Answers>;
