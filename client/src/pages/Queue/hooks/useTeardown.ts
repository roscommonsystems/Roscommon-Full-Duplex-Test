import { useCallback, useEffect, useState } from "react";

export const useTeardown = () => {
  const [available, setAvailable] = useState(false);

  useEffect(() => {
    fetch("/api/teardown/available")
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then((d) => setAvailable(!!d.available))
      .catch(() => setAvailable(false));
  }, []);

  const teardown = useCallback(async (): Promise<{ ok: boolean; error?: string }> => {
    try {
      const r = await fetch("/api/teardown", { method: "POST" });
      if (r.ok) return { ok: true };
      const d = await r.json().catch(() => ({}));
      return { ok: false, error: d.error || `Shutdown failed (${r.status})` };
    } catch {
      return { ok: false, error: "Could not reach the server." };
    }
  }, []);

  return { available, teardown };
};
