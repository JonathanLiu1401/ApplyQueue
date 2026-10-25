import { useEffect, useState } from "react";

/** Tiny hash router: no dependency, works with file:// in packaged builds. */
export function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [parts, setParts] = useState<string[]>(read);
  useEffect(() => {
    const onChange = () => setParts(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return parts;
}

export const href = (...parts: string[]) => `#/${parts.join("/")}`;
