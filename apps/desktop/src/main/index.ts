import { app, BrowserWindow, ipcMain, shell } from "electron";
import { join } from "node:path";
import { ConfigFiles, validateConfig, type ConfigFileName } from "@applyqueue/schema";
import { IPC, type AppInfo } from "../shared/ipc";

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 800,
    minHeight: 600,
    title: "ApplyQueue",
    show: false,
    webPreferences: {
      preload: join(import.meta.dirname, "../preload/index.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.once("ready-to-show", () => win.show());

  // Open external links in the user's browser; never navigate the app window away.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e) => e.preventDefault());

  const devUrl = process.env["ELECTRON_RENDERER_URL"];
  if (!app.isPackaged && devUrl) {
    void win.loadURL(devUrl);
  } else {
    void win.loadFile(join(import.meta.dirname, "../renderer/index.html"));
  }
}

ipcMain.handle(IPC.appInfo, (): AppInfo => ({
  name: app.getName(),
  version: app.getVersion(),
  platform: process.platform,
  electron: process.versions.electron ?? "",
}));

ipcMain.handle(IPC.validateConfig, (_e, file: unknown, data: unknown) => {
  if (typeof file !== "string" || !(file in ConfigFiles)) {
    return { ok: false, issues: [{ path: "", message: "Unknown config file" }] };
  }
  return validateConfig(file as ConfigFileName, data);
});

void app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
