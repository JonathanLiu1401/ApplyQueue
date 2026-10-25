/** IPC channel names shared by main, preload, and renderer. */
export const IPC = {
  appInfo: "app:info",
  validateConfig: "config:validate",
} as const;

export interface AppInfo {
  name: string;
  version: string;
  platform: string;
  electron: string;
}
