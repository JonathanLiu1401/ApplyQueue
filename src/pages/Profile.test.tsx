import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Profile from "./Profile";
import { getProfile, saveProfile, getCountries } from "../lib/api";
import type { CandidateProfile, CountryConfig } from "../lib/types";

// ── i18n: identity translator (returns the raw key) ────────────────────────
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (k: string) => k,
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ── language helpers (side-effecty; stub to no-ops) ─────────────────────────
vi.mock("../i18n", () => ({ loadLanguage: vi.fn() }));
vi.mock("../i18n/languageDetection", () => ({
  getLanguageFromCountry: vi.fn(() => "en"),
  getSavedLanguage: vi.fn(() => null),
}));

vi.mock("../lib/api", () => ({
  getProfile: vi.fn(),
  saveProfile: vi.fn(),
  getCountries: vi.fn(),
}));

const mockGetProfile = vi.mocked(getProfile);
const mockSaveProfile = vi.mocked(saveProfile);
const mockGetCountries = vi.mocked(getCountries);

const usConfig: CountryConfig = {
  name: "United States",
  flag: "🇺🇸",
  date_format: "MM/DD/YYYY",
  currency: "USD",
  salary_period: "annual",
  address_labels: { state: "State", zip: "ZIP" },
  work_auth_options: ["Citizen", "Green Card", "H-1B"],
  show_notice_period: false,
  show_nationality: false,
  show_cover_letter: false,
  show_photo: false,
  show_date_of_birth: false,
  phone_prefix: "+1",
  default_sources: ["linkedin"],
};

const deConfig: CountryConfig = {
  name: "Germany",
  flag: "🇩🇪",
  date_format: "DD.MM.YYYY",
  currency: "EUR",
  salary_period: "annual",
  address_labels: { state: "Bundesland", zip: "PLZ" },
  work_auth_options: ["EU Citizen", "Blue Card"],
  show_notice_period: true,
  show_nationality: true,
  show_cover_letter: true,
  show_photo: true,
  show_date_of_birth: true,
  phone_prefix: "+49",
  default_sources: ["linkedin"],
};

const sampleProfile: Partial<CandidateProfile> = {
  name: "Ada Lovelace",
  email: "ada@example.com",
  country: "US",
  current_role: "Engineer",
  target_job_titles: ["Backend Engineer"],
  skills: ["Python", "Rust"],
  languages: ["English"],
};

describe("Profile page", () => {
  beforeEach(() => {
    mockGetProfile.mockResolvedValue(sampleProfile as CandidateProfile);
    mockGetCountries.mockResolvedValue({
      success: true,
      countries: { US: usConfig, DE: deConfig },
      notice_period_options: ["Immediate", "1 month", "3 months"],
    } as never);
    mockSaveProfile.mockResolvedValue({ success: true } as never);
  });

  it("shows a loading spinner before data resolves, then renders the form", async () => {
    render(<Profile />);
    // The save button (a stable form landmark) is not present while loading.
    expect(screen.queryByRole("button", { name: "save" })).not.toBeInTheDocument();

    await waitFor(() => expect(mockGetProfile).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: "save" })).toBeInTheDocument();
  });

  it("renders fetched profile values into the inputs", async () => {
    render(<Profile />);
    expect(await screen.findByDisplayValue("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByDisplayValue("ada@example.com")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Engineer")).toBeInTheDocument();
    // Tags from the loaded profile render.
    expect(screen.getByText("Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText("Python")).toBeInTheDocument();
  });

  it("populates the country dropdown from getCountries and reflects the selection", async () => {
    render(<Profile />);
    await screen.findByRole("button", { name: "save" });

    const countrySelect = screen.getByDisplayValue("🇺🇸 United States") as HTMLSelectElement;
    expect(countrySelect.value).toBe("US");
    expect(within(countrySelect).getByRole("option", { name: /Germany/ })).toBeInTheDocument();
  });

  it("edits a field and saves, calling saveProfile with the updated payload", async () => {
    const user = userEvent.setup();
    render(<Profile />);
    await screen.findByDisplayValue("Ada Lovelace");

    const nameInput = screen.getByDisplayValue("Ada Lovelace");
    await user.clear(nameInput);
    await user.type(nameInput, "Grace Hopper");

    await user.click(screen.getByRole("button", { name: "save" }));

    await waitFor(() =>
      expect(mockSaveProfile).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Grace Hopper" }),
      ),
    );
    // Success state flips the button label to the "saved" key.
    expect(await screen.findByRole("button", { name: "saved" })).toBeInTheDocument();
  });

  it("adds and removes a tag, persisting the change on save", async () => {
    const user = userEvent.setup();
    render(<Profile />);
    await screen.findByDisplayValue("Ada Lovelace");

    // Add a new skill via the skills TagInput.
    const skillInput = screen.getByPlaceholderText("skills.placeholder");
    await user.type(skillInput, "TypeScript{Enter}");
    expect(await screen.findByText("TypeScript")).toBeInTheDocument();

    // Remove an existing skill ("Python") — its remove button is the sibling of the tag text.
    const pythonTag = screen.getByText("Python").closest("span")!;
    await user.click(within(pythonTag).getByRole("button"));
    await waitFor(() => expect(screen.queryByText("Python")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() => {
      const payload = mockSaveProfile.mock.calls.at(-1)![0];
      expect(payload.skills).toContain("TypeScript");
      expect(payload.skills).not.toContain("Python");
    });
  });

  it("changing country reveals country-conditional fields (Germany: notice period, nationality)", async () => {
    const user = userEvent.setup();
    render(<Profile />);
    await screen.findByRole("button", { name: "save" });

    const countrySelect = screen.getByDisplayValue("🇺🇸 United States");
    await user.selectOptions(countrySelect, "DE");

    // Germany config turns on nationality + notice period.
    expect(await screen.findByText("personal.nationality")).toBeInTheDocument();
    expect(screen.getByText("work.noticePeriod")).toBeInTheDocument();
    expect(screen.getByText("coverLetter.title")).toBeInTheDocument();
  });

  it("shows an error banner when saveProfile rejects (page stays mounted)", async () => {
    mockSaveProfile.mockRejectedValueOnce(new Error("network down"));
    const user = userEvent.setup();
    render(<Profile />);
    await screen.findByRole("button", { name: "save" });

    await user.click(screen.getByRole("button", { name: "save" }));

    expect(await screen.findByText("saveError")).toBeInTheDocument();
    // Form is still present.
    expect(screen.getByDisplayValue("Ada Lovelace")).toBeInTheDocument();
  });

  it("renders with defaults when getProfile/getCountries reject", async () => {
    mockGetProfile.mockRejectedValueOnce(new Error("boom"));
    mockGetCountries.mockRejectedValueOnce(new Error("boom"));
    render(<Profile />);

    // Loading still resolves (finally) and the form renders with nothing preselected: no
    // language, sponsorship, relocation, work mode or salary is assumed for the user.
    expect(await screen.findByRole("button", { name: "save" })).toBeInTheDocument();
    expect(screen.queryByText("English")).not.toBeInTheDocument();
    expect((screen.getByLabelText("work.visaSponsorship") as HTMLSelectElement).value).toBe("");
    expect((screen.getByLabelText("work.willingToRelocate") as HTMLSelectElement).value).toBe("");
    expect((screen.getByLabelText("work.workMode") as HTMLSelectElement).value).toBe("");
    expect((screen.getByLabelText("salary.minimum") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("salary.maximum") as HTMLInputElement).value).toBe("");
  });
});

describe("Profile page: answers the user has not given", () => {
  beforeEach(() => {
    mockGetCountries.mockResolvedValue({
      success: true,
      countries: { US: usConfig, DE: deConfig },
      notice_period_options: [],
    } as never);
    mockSaveProfile.mockReset();
    mockSaveProfile.mockResolvedValue({ success: true } as never);
  });

  it("shows unset preferences as 'Not answered' and flags them as needing input", async () => {
    mockGetProfile.mockResolvedValue({ ...sampleProfile, visa_sponsorship_needed: null, willing_to_relocate: null,
      preferred_work_mode: "", salary_expectation: { min: null, max: null, currency: "USD", period: "annual" } } as never);
    render(<Profile />);
    const banner = await screen.findByTestId("profile-unanswered");
    for (const f of ["visa_sponsorship_needed", "willing_to_relocate", "preferred_work_mode", "salary_expectation.min"]) {
      expect(banner.querySelector(`[data-field="${f}"]`), f).not.toBeNull();
    }
    expect((screen.getByLabelText("work.visaSponsorship") as HTMLSelectElement).value).toBe("");
    expect(screen.getAllByTestId("needs-answer").length).toBeGreaterThanOrEqual(4);
  });

  it("an explicit No is an answer and saves as false; clearing a number saves null", async () => {
    const user = userEvent.setup();
    mockGetProfile.mockResolvedValue({ ...sampleProfile, visa_sponsorship_needed: null,
      salary_expectation: { min: 90000, max: null, currency: "USD", period: "annual" } } as never);
    render(<Profile />);
    await screen.findByDisplayValue("Ada Lovelace");
    const banner = screen.getByTestId("profile-unanswered");
    expect(banner.querySelector('[data-field="visa_sponsorship_needed"]')).not.toBeNull();
    await user.selectOptions(screen.getByLabelText("work.visaSponsorship"), "no");
    expect(screen.getByTestId("profile-unanswered").querySelector('[data-field="visa_sponsorship_needed"]')).toBeNull();
    await user.clear(screen.getByLabelText("salary.minimum"));
    await user.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() => expect(mockSaveProfile).toHaveBeenCalled());
    const payload = mockSaveProfile.mock.calls.at(-1)![0];
    expect(payload.visa_sponsorship_needed).toBe(false);
    expect(payload.salary_expectation.min).toBeNull();
    expect(payload.willing_to_relocate ?? null).toBeNull();
  });

  it("kept-from-an-old-version values ask for confirmation; confirming saves without the flag", async () => {
    const user = userEvent.setup();
    mockGetProfile.mockResolvedValue({ ...sampleProfile, visa_sponsorship_needed: false, willing_to_relocate: true,
      preferred_work_mode: "remote", years_of_experience: 6, phone_country_code: "+1",
      address: { street: "", city: "", state: "", zip: "", country: "United States" },
      salary_expectation: { min: 50000, max: 100000, currency: "USD", period: "annual" },
      _confirm: ["visa_sponsorship_needed", "salary_expectation.min", "salary_expectation.max"] } as never);
    render(<Profile />);
    await screen.findByDisplayValue("Ada Lovelace");
    // Kept, not erased: the value shows, with a "Please confirm" flag.
    expect((screen.getByLabelText("work.visaSponsorship") as HTMLSelectElement).value).toBe("no");
    const flags = screen.getAllByTestId("needs-answer").filter((el) => el.getAttribute("data-state") === "unconfirmed");
    expect(flags.length).toBe(3);
    await user.click(screen.getByRole("button", { name: "unanswered.confirmButton" }));
    await waitFor(() => expect(mockSaveProfile).toHaveBeenCalled());
    const payload = mockSaveProfile.mock.calls.at(-1)![0];
    expect(payload._confirm).toEqual([]);
    expect(payload.visa_sponsorship_needed).toBe(false);
    expect(screen.queryByTestId("profile-unanswered")).not.toBeInTheDocument();
  });

  it("editing one kept value confirms only that one", async () => {
    const user = userEvent.setup();
    mockGetProfile.mockResolvedValue({ ...sampleProfile, visa_sponsorship_needed: false, willing_to_relocate: false,
      _confirm: ["visa_sponsorship_needed", "willing_to_relocate"] } as never);
    render(<Profile />);
    await screen.findByDisplayValue("Ada Lovelace");
    await user.selectOptions(screen.getByLabelText("work.willingToRelocate"), "yes");
    await user.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() => expect(mockSaveProfile).toHaveBeenCalled());
    const payload = mockSaveProfile.mock.calls.at(-1)![0];
    expect(payload._confirm).toEqual(["visa_sponsorship_needed"]);
    expect(payload.willing_to_relocate).toBe(true);
  });
});
