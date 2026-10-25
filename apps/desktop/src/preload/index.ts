import { contextBridge, ipcRenderer } from "electron";
import { IPC } from "../shared/ipc";
import type { ApplyQueueApi } from "./api";

const api: ApplyQueueApi = {
  appInfo: () => ipcRenderer.invoke(IPC.appInfo),
  validateConfig: (file, data) => ipcRenderer.invoke(IPC.validateConfig, file, data),
};

contextBridge.exposeInMainWorld("applyqueue", api);
