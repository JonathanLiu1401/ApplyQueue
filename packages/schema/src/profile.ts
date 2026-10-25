import { z } from "zod";
import { CountryCode, Decline, SCHEMA_VERSION, YearMonth } from "./common";

export const Name = z.object({
  first: z.string().min(1),
  middle: z.string().default(""),
  last: z.string().min(1),
  preferred: z.string().default(""),
  /** Full legal name if it differs from first/middle/last. */
  legal: z.string().default(""),
});

export const Phone = z.object({
  /** E.164, e.g. +15555550100 */
  e164: z.string().regex(/^\+[1-9]\d{6,14}$/, "Expected E.164 format, e.g. +15555550100"),
  country: CountryCode,
});

export const Address = z.object({
  line1: z.string().default(""),
  line2: z.string().default(""),
  city: z.string().default(""),
  region: z.string().default(""),
  postal: z.string().default(""),
  country: CountryCode.or(z.literal("")).default(""),
  county: z.string().default(""),
});

export const Links = z.object({
  linkedin: z.url().or(z.literal("")).default(""),
  github: z.url().or(z.literal("")).default(""),
  website: z.url().or(z.literal("")).default(""),
  other: z.array(z.url()).default([]),
});

export const Education = z.object({
  school: z.string().min(1),
  /** Alternate spellings a form dropdown might use. */
  aliases: z.array(z.string()).default([]),
  degree: z.string().min(1),
  major: z.string().default(""),
  minor: z.string().default(""),
  gpa: z.number().nonnegative().optional(),
  gpaScale: z.number().positive().default(4),
  start: YearMonth.optional(),
  end: YearMonth.optional(),
  expected: z.boolean().default(false),
});

export const WorkAuthCountry = z.object({
  authorized: z.boolean(),
  /** Free-form basis, e.g. "citizen", "permanent-resident", "student-visa", "work-visa". */
  basis: z.string().min(1),
  sponsorshipNow: z.boolean(),
  sponsorshipFuture: z.boolean(),
  /** US export-control "U.S. person" status, if relevant. */
  usPerson: z.boolean().optional(),
  clearance: z.string().default("none"),
});

export const WorkAuth = z.object({
  citizenships: z.array(CountryCode).default([]),
  countries: z.record(CountryCode, WorkAuthCountry).default({}),
});

const EeoChoice = z.union([Decline, z.string().min(1)]);

export const Demographics = z.object({
  gender: EeoChoice.default("decline"),
  race: EeoChoice.default("decline"),
  hispanic: EeoChoice.default("decline"),
  veteran: EeoChoice.default("decline"),
  disability: EeoChoice.default("decline"),
});

export const Reference = z.object({
  name: z.string().min(1),
  relation: z.string().default(""),
  contact: z.string().default(""),
  /** Ask before sharing this reference with an employer. */
  requireConsent: z.boolean().default(true),
});

export const Profile = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  name: Name,
  email: z.email(),
  phone: Phone.optional(),
  address: Address.default(Address.parse({})),
  links: Links.default(Links.parse({})),
  education: z.array(Education).default([]),
  educationPolicy: z
    .object({ singleGraduationDate: z.enum(["first", "last"]).default("last") })
    .default({ singleGraduationDate: "last" }),
  workAuth: WorkAuth.default({ citizenships: [], countries: {} }),
  demographics: Demographics.default(Demographics.parse({})),
  references: z.array(Reference).default([]),
});

export type Profile = z.infer<typeof Profile>;
export type ProfileInput = z.input<typeof Profile>;
