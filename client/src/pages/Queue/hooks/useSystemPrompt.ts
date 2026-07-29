import { useEffect, useState } from "react";

/**
 * The prompt this deployment was configured with (SYSTEM_PROMPT in .env), or
 * null when there isn't one. When present it is the default prompt — Queue
 * applies it to the text prompt box — as well as the "Customized" preset.
 * The server decides what counts as configured — it also rejects strings too
 * short to be a prompt — so there is no length rule here, and none is wanted:
 * two copies of it would drift.
 */
export const useSystemPrompt = (): string | null => {
  const [prompt, setPrompt] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/system-prompt")
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then((d) => setPrompt(d.available && typeof d.prompt === "string" ? d.prompt : null))
      .catch(() => setPrompt(null));
  }, []);

  return prompt;
};
