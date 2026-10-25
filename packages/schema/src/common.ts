import { z } from "zod";

/** Current on-disk config schema version. Bump with a migration in packages/core. */
export const SCHEMA_VERSION = 1 as const;

/** YYYY-MM */
export const YearMonth = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/, "Expected YYYY-MM");
/** YYYY-MM-DD */
export const IsoDate = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/, "Expected YYYY-MM-DD");
/** ISO 3166-1 alpha-2 country code, e.g. "US". */
export const CountryCode = z.string().regex(/^[A-Z]{2}$/, "Expected a 2-letter country code");
/** Short, stable machine id (kebab/colon-case), e.g. "data-analyst" or "workday:examplecorp". */
export const Id = z.string().min(1).max(120).regex(/^[a-z0-9][a-z0-9:._-]*$/, "Use lowercase letters, digits, and - _ . :");

/** "Decline to self-identify" is always a valid explicit choice. */
export const Decline = z.literal("decline");
