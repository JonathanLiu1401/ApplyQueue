/**
 * One-time copy of upstream LangHire localStorage keys to ApplyQueue keys.
 *
 * Preferences (theme, language, tailor options, ...) carry over. Telemetry
 * consent and the analytics client id do NOT: ApplyQueue telemetry is opt-in,
 * so a LangHire "enabled" flag must not silently opt the user in.
 */
const LEGACY_PREFIX = "langhire_";
const NEW_PREFIX = "applyqueue_";
const NOT_MIGRATED = new Set(["telemetry", "ga_client_id"]);
const MARKER = "applyqueue_storage_migrated";

export function migrateLegacyStorage(storage: Storage = localStorage): void {
  try {
    if (storage.getItem(MARKER)) return;
    const legacyKeys: string[] = [];
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i);
      if (key && key.startsWith(LEGACY_PREFIX)) legacyKeys.push(key);
    }
    for (const key of legacyKeys) {
      const suffix = key.slice(LEGACY_PREFIX.length);
      const value = storage.getItem(key);
      if (!NOT_MIGRATED.has(suffix) && value !== null && storage.getItem(NEW_PREFIX + suffix) === null) {
        storage.setItem(NEW_PREFIX + suffix, value);
      }
      storage.removeItem(key);
    }
    storage.setItem(MARKER, "1");
  } catch {
    // Storage unavailable (private mode / quota) — nothing to migrate.
  }
}
