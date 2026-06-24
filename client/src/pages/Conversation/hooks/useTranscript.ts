import { useCallback, useEffect, useRef, useState } from "react";
import { decodeMessage } from "../../../protocol/encoder";

export type Turn = { speaker: "AI" | "You"; text: string };

// Average-decimate Float32 PCM from inRate down to 16 kHz.
function downsampleTo16k(input: Float32Array, inRate: number): Float32Array {
  if (inRate === 16000) return input;
  const ratio = inRate / 16000;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = end > start ? sum / (end - start) : 0;
  }
  return out;
}

// Merged conversation transcript: the model's inner-monologue text (from the
// /api/chat socket) and the user's transcription (from /api/transcribe) appended
// as alternating "AI:" / "You:" turns in arrival order.
export const useTranscript = (socket: WebSocket | null) => {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [available, setAvailable] = useState(false);

  const append = useCallback((speaker: "AI" | "You", piece: string, joiner: string) => {
    setTurns((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.speaker === speaker) {
        const copy = prev.slice();
        copy[copy.length - 1] = { speaker, text: last.text + joiner + piece };
        return copy;
      }
      return [...prev, { speaker, text: piece }];
    });
  }, []);

  // Model text from the conversation socket.
  useEffect(() => {
    if (!socket) {
      return;
    }
    setTurns([]);
    const onMsg = (e: MessageEvent) => {
      const msg = decodeMessage(new Uint8Array(e.data));
      if (msg.type === "text") {
        append("AI", msg.data, "");
      }
    };
    socket.addEventListener("message", onMsg);
    return () => socket.removeEventListener("message", onMsg);
  }, [socket, append]);

  // Is server-side transcription configured?
  useEffect(() => {
    fetch("/api/transcribe/available")
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then((d) => setAvailable(!!d.available))
      .catch(() => setAvailable(false));
  }, []);

  // User transcription: capture our own mic, stream 16 kHz PCM to /api/transcribe.
  const wsRef = useRef<WebSocket | null>(null);
  useEffect(() => {
    if (!available || !socket) {
      return;
    }
    let stopped = false;
    let ws: WebSocket | null = null;
    let ctx: AudioContext | null = null;
    let sp: ScriptProcessorNode | null = null;
    let stream: MediaStream | null = null;

    (async () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}/api/transcribe`);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.addEventListener("message", (e) => {
        if (typeof e.data === "string") {
          append("You", e.data, " ");
        }
      });
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });
      } catch {
        return;
      }
      if (stopped) {
        return;
      }
      ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(stream);
      sp = ctx.createScriptProcessor(4096, 1, 1);
      const inRate = ctx.sampleRate;
      src.connect(sp);
      sp.connect(ctx.destination);
      sp.onaudioprocess = (ev) => {
        if (stopped || !ws || ws.readyState !== WebSocket.OPEN) {
          return;
        }
        const pcm16 = downsampleTo16k(ev.inputBuffer.getChannelData(0), inRate);
        const out = new ArrayBuffer(pcm16.length * 4);
        new Float32Array(out).set(pcm16);
        ws.send(out);
      };
    })();

    return () => {
      stopped = true;
      try { if (sp) sp.onaudioprocess = null; } catch { /* noop */ }
      try { stream?.getTracks().forEach((t) => t.stop()); } catch { /* noop */ }
      try { ctx?.close(); } catch { /* noop */ }
      try { ws?.close(); } catch { /* noop */ }
      wsRef.current = null;
    };
  }, [available, socket, append]);

  return { turns, available };
};
