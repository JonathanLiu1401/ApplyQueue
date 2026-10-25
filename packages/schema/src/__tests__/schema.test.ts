import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { Accounts, Answers, Preferences, Profile, validateConfig } from "../index";
import { alexProfile } from "./fixtures";

describe("Profile", () => {
  it("accepts a minimal fictional profile and fills defaults", () => {
    const p = Profile.parse(alexProfile);
    expect(p.demographics.gender).toBe("decline");
    expect(p.educationPolicy.singleGraduationDate).toBe("last");
    expect(p.references).toEqual([]);
  });

  it("rejects a bad phone and a bad email", () => {
    expect(Profile.safeParse({ ...alexProfile, phone: { e164: "555-0100", country: "US" } }).success).toBe(false);
    expect(Profile.safeParse({ ...alexProfile, email: "not-an-email" }).success).toBe(false);
  });

  it("rejects an unknown schemaVersion", () => {
    expect(Profile.safeParse({ ...alexProfile, schemaVersion: 2 }).success).toBe(false);
  });
});

describe("Preferences / categories", () => {
  const base = { schemaVersion: 1 };

  it("works for any job domain", () => {
    const p = Preferences.parse({
      ...base,
      roleType: "full-time",
      categories: [{ id: "welding", label: "Welding", priority: 1, include: ["welder"] }],
    });
    expect(p.categories[0]?.apply).toBe(true);
    expect(p.apply.requireEmailProof).toBe(true);
    expect(p.apply.autoSubmit).toBe(false);
  });

  it("rejects duplicate category ids", () => {
    const cat = { id: "x", label: "X", priority: 1, include: ["x"] };
    expect(Preferences.safeParse({ ...base, categories: [cat, cat] }).success).toBe(false);
  });

  it("rejects a term that ends before it starts", () => {
    const term = { id: "t", label: "T", start: "2027-09-01", end: "2027-06-01" };
    expect(Preferences.safeParse({ ...base, term }).success).toBe(false);
  });

  it("rejects a category without keywords", () => {
    expect(Preferences.safeParse({ ...base, categories: [{ id: "x", label: "X", priority: 1, include: [] }] }).success).toBe(false);
  });
});

describe("Answers", () => {
  it("accepts a bank entry and defaults source to user", () => {
    const a = Answers.parse({ schemaVersion: 1, bank: [{ id: "age", patterns: ["are you 18"], answer: "Yes" }] });
    expect(a.bank[0]?.source).toBe("user");
  });

  it("rejects empty answers (never blank)", () => {
    expect(Answers.safeParse({ schemaVersion: 1, bank: [{ id: "a", patterns: ["q"], answer: "" }] }).success).toBe(false);
  });
});

describe("Accounts", () => {
  it("refuses a password field on a portal entry", () => {
    const r = Accounts.safeParse({ schemaVersion: 1, portals: [{ id: "workday:examplecorp", password: "hunter2" }] });
    expect(r.success).toBe(false);
  });

  it("checks resume rule references", () => {
    const r = Accounts.safeParse({
      schemaVersion: 1,
      resumes: { items: [{ id: "primary", path: "documents/resume.pdf" }], rules: [{ match: { titleRegex: "x" }, resumeId: "missing" }] },
    });
    expect(r.success).toBe(false);
  });
});

describe("validateConfig", () => {
  it("never echoes values back", () => {
    const secret = "SENTINEL-VALUE-123";
    const r = validateConfig("profile.json", { ...alexProfile, email: secret });
    expect(r.ok).toBe(false);
    expect(JSON.stringify(r)).not.toContain(secret);
  });
});

describe("starter packs", () => {
  const dir = fileURLToPath(new URL("../../../../templates/packs", import.meta.url));
  for (const f of readdirSync(dir).filter((n) => n.endsWith(".json"))) {
    it(`${f} validates`, () => {
      const pack = JSON.parse(readFileSync(join(dir, f), "utf8"));
      expect(Preferences.safeParse(pack.preferences).success).toBe(true);
      expect(Answers.safeParse(pack.answers).success).toBe(true);
    });
  }
});
