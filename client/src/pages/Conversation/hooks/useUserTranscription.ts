import { useCallback, useEffect, useRef, useState } from "react";

export const useUserTranscription = (active: boolean) => {
  const [available, setAvailable] = useState(false);
  const [userText, setUserText] = useState<string[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    fetch("/api/transcribe/available")
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then((d) => setAvailable(!!d.available))
      .catch(() => setAvailable(false));
  }, []);

  useEffect(() => {
    if (!active || !available) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/api/transcribe`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    ws.addEventListener("message", (e) => {
      if (typeof e.data === "string") setUserText((t) => [...t, e.data]);
    });
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
      setUserText([]);
    };
  }, [active, available]);

  const sendAudio = useCallback((chunk: Uint8Array) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(chunk);
  }, []);

  return { available, userText, sendAudio };
};
