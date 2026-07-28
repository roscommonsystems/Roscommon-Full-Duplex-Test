// MediaRecorder can't produce MP3 in any browser, so the recording (webm/opus
// or mp4/aac) is decoded back to PCM and re-encoded here. The encoder itself is
// imported lazily so it stays out of the initial bundle.
const KBPS = 128;
// One MP3 frame worth of samples per encodeBuffer() call, as lame expects.
const SAMPLES_PER_FRAME = 1152;
// Yield to the event loop every N frames so the UI stays responsive.
const FRAMES_PER_SLICE = 200;

// lame reuses its output buffer between calls, so each chunk has to be copied.
const copyBytes = (chunk: Uint8Array) => {
  const out = new Uint8Array(chunk.length);
  out.set(chunk);
  return out;
};

const toInt16 = (input: Float32Array, offset: number, length: number) => {
  const out = new Int16Array(length);
  for (let i = 0; i < length; i++) {
    const sample = Math.max(-1, Math.min(1, input[offset + i] ?? 0));
    out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
};

/**
 * Re-encode a decoded recording as MP3. Mono and stereo are both kept as-is
 * (the conversation recording is stereo: left = server, right = user mic).
 * `onProgress` is called with a 0..1 fraction while encoding.
 */
export const encodeMp3 = async (
  buffer: AudioBuffer,
  onProgress?: (progress: number) => void,
): Promise<Blob> => {
  const { Mp3Encoder } = await import("@breezystack/lamejs");
  const channels = Math.min(buffer.numberOfChannels, 2);
  const left = buffer.getChannelData(0);
  const right = channels > 1 ? buffer.getChannelData(1) : null;
  const encoder = new Mp3Encoder(channels, buffer.sampleRate, KBPS);
  const parts: Uint8Array<ArrayBuffer>[] = [];

  let framesInSlice = 0;
  for (let offset = 0; offset < buffer.length; offset += SAMPLES_PER_FRAME) {
    const length = Math.min(SAMPLES_PER_FRAME, buffer.length - offset);
    const chunk = right
      ? encoder.encodeBuffer(toInt16(left, offset, length), toInt16(right, offset, length))
      : encoder.encodeBuffer(toInt16(left, offset, length));
    if (chunk.length > 0) {
      parts.push(copyBytes(chunk));
    }
    if (++framesInSlice >= FRAMES_PER_SLICE) {
      framesInSlice = 0;
      onProgress?.(offset / buffer.length);
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  const flushed = encoder.flush();
  if (flushed.length > 0) {
    parts.push(copyBytes(flushed));
  }
  onProgress?.(1);
  return new Blob(parts, { type: "audio/mpeg" });
};

/** e.g. personaplex_audio_2026-07-28_19-42-13.mp3 — local time, filename safe. */
export const timestampedFilename = (prefix: string, extension: string, date = new Date()) => {
  const pad = (value: number) => value.toString().padStart(2, "0");
  const stamp =
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `_${pad(date.getHours())}-${pad(date.getMinutes())}-${pad(date.getSeconds())}`;
  return `${prefix}_${stamp}.${extension}`;
};
