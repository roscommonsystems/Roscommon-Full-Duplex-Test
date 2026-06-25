import { useEffect, useState } from "react";

export type Injection = { at_seconds: number; text: string };
export type Scenario = { id: string; name: string; description?: string; injections: Injection[] };

export const useScenarios = () => {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  useEffect(() => {
    fetch("/api/scenarios")
      .then((r) => (r.ok ? r.json() : []))
      .then((s: Scenario[]) => setScenarios(s))
      .catch(() => setScenarios([]));
  }, []);
  return { scenarios };
};
