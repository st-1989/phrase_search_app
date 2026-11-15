import { useEffect, useState } from "react";

/** localStorage に保存される状態（使えない環境では普通の state として動く）。 */
export function usePersistent<T>(key: string, initial: T) {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return initial;
      const parsed = JSON.parse(raw);
      return initial && typeof initial === "object" && !Array.isArray(initial) ? { ...initial, ...parsed } : parsed;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(v));
    } catch {
      /* 保存できなくても動作は続ける */
    }
  }, [key, v]);
  return [v, setV] as const;
}
