import type { ApplyQueueApi } from "./api";

declare global {
  interface Window {
    applyqueue: ApplyQueueApi;
  }
}
export {};
