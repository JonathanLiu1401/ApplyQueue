import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSafeTimeout } from "./useSafeTimeout";

describe("useSafeTimeout", () => {
  afterEach(() => vi.useRealTimers());

  it("runs the callback while mounted", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const { result } = renderHook(() => useSafeTimeout());
    result.current(fn, 100);
    vi.advanceTimersByTime(100);
    expect(fn).toHaveBeenCalledOnce();
  });

  it("cancels pending callbacks on unmount", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const { result, unmount } = renderHook(() => useSafeTimeout());
    result.current(fn, 100);
    unmount();
    vi.advanceTimersByTime(200);
    expect(fn).not.toHaveBeenCalled();
  });
});
