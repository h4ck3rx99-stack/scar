// Data hooks for screens: load on mount and on demand (no polling), with loading/error states.
import { useCallback, useEffect, useState } from "react";
import { humanError } from "../api/client";
import { useRuntime } from "../store/runtime";

export interface Loaded<T> {
  data: T | null;
  loading: boolean;
  error: string;
  reload: () => Promise<void>;
}

export function useApiGet<T>(path: string | null, deps: unknown[] = []): Loaded<T> {
  const api = useRuntime((s) => s.api);
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    if (!api || !path) {
      setLoading(!api);
      return;
    }
    setLoading(true);
    try {
      setData(await api.get<T>(path));
      setError("");
    } catch (e) {
      setError(humanError(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, path, ...deps]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, loading, error, reload };
}

export interface SettingField {
  name: string;
  kind: "bool" | "int" | "float" | "list" | "text" | "choice";
  choices: unknown[];
  group: string;
  description: string;
  value: unknown;
  default: unknown;
}

export function useSettings() {
  const api = useRuntime((s) => s.api);
  const q = useApiGet<{ fields: SettingField[] }>("/settings");
  const byName = Object.fromEntries((q.data?.fields ?? []).map((f) => [f.name, f])) as Record<string, SettingField>;
  const save = useCallback(
    async (values: Record<string, unknown>): Promise<string | null> => {
      if (!api) return "SCAR isn't connected yet.";
      try {
        await api.patch("/settings", { values });
        await q.reload();
        return null;
      } catch (e) {
        return humanError(e);
      }
    },
    [api, q],
  );
  const value = <T,>(name: string, fallback: T): T => (byName[name]?.value as T | undefined) ?? fallback;
  return { ...q, fields: q.data?.fields ?? [], byName, save, value };
}
