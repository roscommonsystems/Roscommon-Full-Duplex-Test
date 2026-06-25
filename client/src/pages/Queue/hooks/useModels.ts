import { useCallback, useEffect, useState } from "react";

export type ModelInfo = { id: string; name: string; description?: string; supports_scenarios?: boolean; voice_wav?: string };
export type Status = {
  current_repo: string | null;
  display_name: string | null;
  state: "loading" | "ready" | "error";
  error: string | null;
};

export const useModels = () => {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [status, setStatus] = useState<Status | null>(null);

  const refreshStatus = useCallback(async (): Promise<Status | null> => {
    try {
      const r = await fetch("/api/status");
      if (!r.ok) return null;
      const s = (await r.json()) as Status;
      setStatus(s);
      return s;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    fetch("/api/models")
      .then((r) => (r.ok ? r.json() : []))
      .then((m: ModelInfo[]) => setModels(m))
      .catch(() => setModels([]));
    refreshStatus();
  }, [refreshStatus]);

  return { models, status, refreshStatus };
};
