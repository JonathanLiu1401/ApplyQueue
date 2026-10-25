import { useCallback, useEffect, useRef } from "react";

/**
 * `setTimeout` that is cancelled when the component unmounts, so delayed
 * state updates ("Saved!" flashes, toasts) never fire on an unmounted tree.
 */
export function useSafeTimeout() {
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, []);
  return useCallback((fn: () => void, ms: number) => {
    const id = setTimeout(() => {
      timers.current.delete(id);
      fn();
    }, ms);
    timers.current.add(id);
    return id;
  }, []);
}
