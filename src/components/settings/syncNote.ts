/** The one-line note shown after "Sync now". Never says "Synced." for a refused sync. */
export function syncNote(r: { success: boolean; offline?: boolean; refused?: string; auth_error?: string }) {
  if (r.auth_error) return `Not synced: ${r.auth_error}`;
  if (r.refused) return `Not synced: ${r.refused}`;
  if (r.offline) return "Offline: changes are queued.";
  return r.success ? "Synced." : "Not synced.";
}
