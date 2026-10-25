import { describe, it, expect, beforeEach } from "vitest";
import { migrateLegacyStorage } from "./storageMigration";

// This project's jsdom exposes a method-less localStorage, so use a Map shim.
function makeStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
    setItem: (k: string, v: string) => void m.set(k, String(v)),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
  };
}

let localStorage: Storage;

describe("migrateLegacyStorage", () => {
  beforeEach(() => {
    localStorage = makeStorage();
  });

  it("copies preferences, drops telemetry consent and the analytics id", () => {
    localStorage.setItem("langhire_theme", "dark");
    localStorage.setItem("langhire_language", "de");
    localStorage.setItem("langhire_telemetry", "true");
    localStorage.setItem("langhire_ga_client_id", "abc");
    migrateLegacyStorage(localStorage);
    expect(localStorage.getItem("applyqueue_theme")).toBe("dark");
    expect(localStorage.getItem("applyqueue_language")).toBe("de");
    expect(localStorage.getItem("applyqueue_telemetry")).toBeNull();
    expect(localStorage.getItem("applyqueue_ga_client_id")).toBeNull();
    expect(localStorage.getItem("langhire_theme")).toBeNull();
  });

  it("never overwrites an existing ApplyQueue value and runs once", () => {
    localStorage.setItem("applyqueue_theme", "light");
    localStorage.setItem("langhire_theme", "dark");
    migrateLegacyStorage(localStorage);
    expect(localStorage.getItem("applyqueue_theme")).toBe("light");
    localStorage.setItem("langhire_language", "fr");
    migrateLegacyStorage(localStorage);
    expect(localStorage.getItem("applyqueue_language")).toBeNull();
  });
});
