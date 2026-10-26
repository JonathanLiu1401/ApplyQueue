import { describe, it, expect } from "vitest";
import { unansweredFields, confirmList, triValue, fromTri, numberOrNull } from "./profileAnswers";
import type { CandidateProfile } from "./types";

const answered: Partial<CandidateProfile> = {
  visa_sponsorship_needed: false, willing_to_relocate: true, preferred_work_mode: "remote",
  salary_expectation: { min: 120000, max: null, currency: "USD", period: "annual" },
  years_of_experience: 6, languages: ["English"], country: "US",
  address: { street: "", city: "", state: "", zip: "", country: "United States" }, phone_country_code: "+1",
};

describe("profileAnswers", () => {
  it("an empty profile has every preset unanswered, preferences blocking", () => {
    const rows = unansweredFields({});
    expect(rows.every((r) => r.state === "unset")).toBe(true);
    expect(rows.filter((r) => r.blocksApply).map((r) => r.field)).toEqual([
      "visa_sponsorship_needed", "willing_to_relocate", "preferred_work_mode", "salary_expectation.min"]);
  });

  it("explicit false and a lone salary minimum count as answers", () => {
    expect(unansweredFields(answered)).toEqual([]);
  });

  it("kept values awaiting confirmation are flagged; bogus or answered-away entries are ignored", () => {
    const p = { ...answered, _confirm: ["visa_sponsorship_needed", "nope", "visa_sponsorship_needed"] };
    expect(confirmList(p)).toEqual(["visa_sponsorship_needed"]);
    expect(unansweredFields(p)).toEqual([{ field: "visa_sponsorship_needed", state: "unconfirmed", blocksApply: true }]);
    expect(confirmList({ ...p, visa_sponsorship_needed: null })).toEqual([]);
  });

  it("tri-state and number helpers keep 'not answered' distinct from No and 0", () => {
    expect(triValue(null)).toBe("");
    expect(triValue(false)).toBe("no");
    expect(fromTri("")).toBeNull();
    expect(fromTri("no")).toBe(false);
    expect(numberOrNull("")).toBeNull();
    expect(numberOrNull("0")).toBe(0);
    expect(numberOrNull("85000")).toBe(85000);
  });
});
