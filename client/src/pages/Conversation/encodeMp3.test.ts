import { describe, expect, it } from "vitest";
import { encodeMp3, timestampedFilename } from "./encodeMp3";

// AudioBuffer doesn't exist under node; encodeMp3 only uses these four members.
const fakeStereoBuffer = (seconds: number, sampleRate = 48000) => {
  const length = seconds * sampleRate;
  const channel = (freq: number) => {
    const data = new Float32Array(length);
    for (let i = 0; i < length; i++) {
      data[i] = Math.sin((2 * Math.PI * freq * i) / sampleRate) * 0.5;
    }
    return data;
  };
  const channels = [channel(440), channel(880)];
  return {
    numberOfChannels: 2,
    sampleRate,
    length,
    getChannelData: (index: number) => channels[index],
  } as unknown as AudioBuffer;
};

describe("encodeMp3", () => {
  it("produces an mp3 blob with a valid frame header", async () => {
    const blob = await encodeMp3(fakeStereoBuffer(1));
    const bytes = new Uint8Array(await blob.arrayBuffer());
    expect(blob.type).toBe("audio/mpeg");
    // MPEG frame sync: 11 set bits.
    expect(bytes[0]).toBe(0xff);
    expect(bytes[1] & 0xe0).toBe(0xe0);
    // Roughly one second at 128 kbps.
    expect(bytes.length).toBeGreaterThan(12000);
    expect(bytes.length).toBeLessThan(20000);
  });

  it("reports progress ending at 1", async () => {
    const progress: number[] = [];
    await encodeMp3(fakeStereoBuffer(15), (value) => progress.push(value));
    expect(progress.length).toBeGreaterThan(1);
    expect(progress[progress.length - 1]).toBe(1);
    expect(progress).toStrictEqual([...progress].sort((a, b) => a - b));
  });
});

describe("timestampedFilename", () => {
  it("includes a filename-safe local timestamp", () => {
    const name = timestampedFilename("personaplex_audio", "mp3", new Date(2026, 6, 28, 9, 5, 3));
    expect(name).toBe("personaplex_audio_2026-07-28_09-05-03.mp3");
  });
});
