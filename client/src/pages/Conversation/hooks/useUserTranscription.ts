import { useEffect, useState } from "react";

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

export const useUserTranscription = (active: boolean) => {
  const [available, setAvailable] = useState(false);
  const [userText, setUserText] = useState<string[]>([]);

  useEffect(() => {
    fetch("/api/transcribe/available")
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then((d) => setAvailable(!!d.available))
      .catch(() => setAvailable(false));
  }, []);

  useEffect(() => {
    if (!active || !available) return;
    let stopped = false;
    let ws: WebSocket | null = null;
    let ctx: AudioContext | null = null;
    let sp: ScriptProcessorNode | null = null;
    let stream: MediaStream | null = null;

    (async () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}/api/transcribe`);
      ws.binaryType = "arraybuffer";
      ws.addEventListener("message", (e) => {
        if (typeof e.data === "string") setUserText((t) => [...t, e.data]);
      });
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });
      } catch {
        return;
      }
      if (stopped) return;
      ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(stream);
      sp = ctx.createScriptProcessor(4096, 1, 1);
      const inRate = ctx.sampleRate;
      src.connect(sp);
      sp.connect(ctx.destination); // required for onaudioprocess to fire (output stays silent)
      sp.onaudioprocess = (ev) => {
        if (stopped || !ws || ws.readyState !== WebSocket.OPEN) return;
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
      setUserText([]);
    };
  }, [active, available]);

  return { available, userText };
};
