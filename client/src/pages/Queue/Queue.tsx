import moshiProcessorUrl from "../../audio-processor.ts?worker&url";
import { FC, useEffect, useState, useCallback, useRef, MutableRefObject } from "react";
import eruda from "eruda";
import { useSearchParams } from "react-router-dom";
import { Conversation } from "../Conversation/Conversation";
import { Button } from "../../components/Button/Button";
import { useModelParams } from "../Conversation/hooks/useModelParams";
import { env } from "../../env";
import { prewarmDecoderWorker } from "../../decoder/decoderWorker";
import { useLocalStorage } from "../Conversation/hooks/useLocalStorage";
import { useModels, ModelInfo, Status } from "./hooks/useModels";
import { useTeardown } from "./hooks/useTeardown";

// Voice presets with human-readable descriptions.
// "Natural" voices are tuned for warm, conversational delivery; "Variety"
// voices cover a more expressive, varied range. (Descriptions are starting
// points — refine per-voice after listening to each sample.)
const VOICE_OPTIONS = [
  { value: "NATF0.pt", label: "Natural Female 1 — warm, conversational" },
  { value: "NATF1.pt", label: "Natural Female 2 — warm, conversational" },
  { value: "NATF2.pt", label: "Natural Female 3 — warm, conversational" },
  { value: "NATF3.pt", label: "Natural Female 4 — warm, conversational" },
  { value: "NATM0.pt", label: "Natural Male 1 — warm, conversational" },
  { value: "NATM1.pt", label: "Natural Male 2 — warm, conversational" },
  { value: "NATM2.pt", label: "Natural Male 3 — warm, conversational" },
  { value: "NATM3.pt", label: "Natural Male 4 — warm, conversational" },
  { value: "VARF0.pt", label: "Variety Female 1 — expressive, varied" },
  { value: "VARF1.pt", label: "Variety Female 2 — expressive, varied" },
  { value: "VARF2.pt", label: "Variety Female 3 — expressive, varied" },
  { value: "VARF3.pt", label: "Variety Female 4 — expressive, varied" },
  { value: "VARF4.pt", label: "Variety Female 5 — expressive, varied" },
  { value: "VARM0.pt", label: "Variety Male 1 — expressive, varied" },
  { value: "VARM1.pt", label: "Variety Male 2 — expressive, varied" },
  { value: "VARM2.pt", label: "Variety Male 3 — expressive, varied" },
  { value: "VARM3.pt", label: "Variety Male 4 — expressive, varied" },
  { value: "VARM4.pt", label: "Variety Male 5 — expressive, varied" },
];

const TEXT_PROMPT_PRESETS = [
  {
    label: "Assistant (default)",
    text: "You are a wise and friendly teacher. Answer questions or provide advice in a clear and engaging way.",
  },
  {
    label: "Medical office (service)",
    text: "You work for Dr. Jones's medical office, and you are receiving calls to record information for new patients. Information: Record full name, date of birth, any medication allergies, tobacco smoking history, alcohol consumption history, and any prior medical conditions. Assure the patient that this information will be confidential, if they ask.",
  },
  {
    label: "Bank (service)",
    text: "You work for First Neuron Bank which is a bank and your name is Alexis Kim. Information: The customer's transaction for $1,200 at Home Depot was declined. Verify customer identity. The transaction was flagged due to unusual location (transaction attempted in Miami, FL; customer normally transacts in Seattle, WA).",
  },
  {
    label: "Astronaut (fun)",
    text: "You enjoy having a good conversation. Have a technical discussion about fixing a reactor core on a spaceship to Mars. You are an astronaut on a Mars mission. Your name is Alex. You are already dealing with a reactor core meltdown on a Mars mission. Several ship systems are failing, and continued instability will lead to catastrophic failure. You explain what is happening and you urgently ask for help thinking through how to stabilize the reactor.",
  },
];

interface HomepageProps {
  showMicrophoneAccessMessage: boolean;
  startConnection: () => Promise<void>;
  textPrompt: string;
  setTextPrompt: (value: string) => void;
  voicePrompt: string;
  setVoicePrompt: (value: string) => void;
  models: ModelInfo[];
  selectedRepo: string;
  setSelectedRepo: (value: string) => void;
  loadedName: string | null;
  switchError: string | null;
  teardownAvailable: boolean;
  onTeardown: () => void;
  teardownError: string | null;
}

const Homepage = ({
  startConnection,
  showMicrophoneAccessMessage,
  textPrompt,
  setTextPrompt,
  voicePrompt,
  setVoicePrompt,
  models,
  selectedRepo,
  setSelectedRepo,
  loadedName,
  switchError,
  teardownAvailable,
  onTeardown,
  teardownError,
}: HomepageProps) => {
  return (
    <div className="text-center h-screen w-screen p-4 flex flex-col items-center pt-8">
      <div className="mb-6">
        <h1 className="text-4xl text-black">Roscommon Full Duplex Test</h1>
        <p className="text-sm text-gray-600 mt-2">
          Full duplex conversational AI with text and voice control.
        </p>
        {loadedName && (
          <p className="text-xs text-gray-500 mt-1">
            Loaded: <span className="font-medium">{loadedName}</span>
          </p>
        )}
      </div>

      <div className="flex flex-grow justify-center items-center flex-col gap-6 w-full min-w-[500px] max-w-2xl">
        <div className="w-full">
          <label htmlFor="text-prompt" className="block text-left text-base font-medium text-gray-700 mb-2">
            Text Prompt:
          </label>
          <div className="border border-gray-300 rounded p-3 mb-3 bg-gray-50">
            <span className="text-xs font-medium text-gray-500 block mb-2">Examples:</span>
            <div className="flex flex-wrap gap-2 justify-center">
              {TEXT_PROMPT_PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  onClick={() => setTextPrompt(preset.text)}
                  className="px-3 py-1 text-xs bg-white hover:bg-gray-100 text-gray-700 rounded-full border border-gray-300 transition-colors focus:outline-none focus:ring-2 focus:ring-[#76b900]"
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>
          <textarea
            id="text-prompt"
            name="text-prompt"
            value={textPrompt}
            onChange={(e) => setTextPrompt(e.target.value)}
            className="w-full h-32 min-h-[80px] max-h-64 p-3 bg-white text-black border border-gray-300 rounded resize-y focus:outline-none focus:ring-2 focus:ring-[#76b900] focus:border-transparent"
            placeholder="Enter your text prompt..."
            maxLength={1000}
          />
          <div className="text-right text-xs text-gray-500 mt-1">
            {textPrompt.length}/1000
          </div>
        </div>

        <div className="w-full">
          <label htmlFor="model-select" className="block text-left text-base font-medium text-gray-700 mb-2">
            Model:
          </label>
          <select
            id="model-select"
            value={selectedRepo}
            onChange={(e) => setSelectedRepo(e.target.value)}
            className="w-full p-3 bg-white text-black border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-[#76b900] focus:border-transparent"
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
          </select>
        </div>

        <div className="w-full">
          <label htmlFor="voice-prompt" className="block text-left text-base font-medium text-gray-700 mb-2">
            Voice:
          </label>
          <select
            id="voice-prompt"
            name="voice-prompt"
            value={voicePrompt}
            onChange={(e) => setVoicePrompt(e.target.value)}
            className="w-full p-3 bg-white text-black border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-[#76b900] focus:border-transparent"
          >
            {VOICE_OPTIONS.map((voice) => (
              <option key={voice.value} value={voice.value}>
                {voice.label}
              </option>
            ))}
          </select>
        </div>

        {showMicrophoneAccessMessage && (
          <p className="text-center text-red-500">Please enable your microphone before proceeding</p>
        )}

        {switchError && <p className="text-center text-red-500">{switchError}</p>}

        <Button onClick={async () => await startConnection()}>Connect</Button>

        {teardownAvailable && (
          <div className="mt-10 flex flex-col items-center">
            {teardownError && (
              <p className="text-center text-red-500 mb-2 text-sm">{teardownError}</p>
            )}
            <button
              type="button"
              onClick={onTeardown}
              className="px-3 py-1 text-xs text-gray-400 hover:text-red-600 underline focus:outline-none"
            >
              Shut down instance
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export const Queue:FC = () => {
  const theme = "light" as const;  // Always use light theme
  const [searchParams] = useSearchParams();
  const overrideWorkerAddr = searchParams.get("worker_addr");
  const [hasMicrophoneAccess, setHasMicrophoneAccess] = useState<boolean>(false);
  const [showMicrophoneAccessMessage, setShowMicrophoneAccessMessage] = useState<boolean>(false);
  const modelParams = useModelParams();
  const { models, status, refreshStatus } = useModels();
  const [selectedRepo, setSelectedRepo] = useLocalStorage<string>("selectedRepo", "");
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const { available: teardownAvailable, teardown } = useTeardown();
  const [destroyed, setDestroyed] = useState(false);
  const [teardownError, setTeardownError] = useState<string | null>(null);
  useEffect(() => {
    // A remembered model that the server no longer offers falls back to whatever
    // is currently loaded, otherwise the dropdown would show a stale selection.
    if (selectedRepo && models.length > 0 && !models.some((m) => m.id === selectedRepo)) {
      setSelectedRepo(status?.current_repo ?? "");
      return;
    }
    if (!selectedRepo && status?.current_repo) setSelectedRepo(status.current_repo);
  }, [status, selectedRepo, models, setSelectedRepo]);

  const handleTeardown = useCallback(async () => {
    if (!window.confirm("Destroy this instance? This stops billing and ends the demo.")) return;
    setTeardownError(null);
    const res = await teardown();
    if (res.ok) setDestroyed(true);
    else setTeardownError(res.error || "Shutdown failed.");
  }, [teardown]);

  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  const audioContext = useRef<AudioContext | null>(null);
  const worklet = useRef<AudioWorkletNode | null>(null);
  
  // enable eruda in development
  useEffect(() => {
    if(env.VITE_ENV === "development") {
      eruda.init();
    }
    () => {
      if(env.VITE_ENV === "development") {
        eruda.destroy();
      }
    };
  }, []);

  const getMicrophoneAccess = useCallback(async () => {
    try {
      await window.navigator.mediaDevices.getUserMedia({ audio: true });
      setHasMicrophoneAccess(true);
      return true;
    } catch(e) {
      console.error(e);
      setShowMicrophoneAccessMessage(true);
      setHasMicrophoneAccess(false);
    }
    return false;
}, [setHasMicrophoneAccess, setShowMicrophoneAccessMessage]);

  const startProcessor = useCallback(async () => {
    if(!audioContext.current) {
      audioContext.current = new AudioContext();
      // Prewarm decoder worker as soon as we have audio context
      // This gives WASM time to load while user grants mic access
      prewarmDecoderWorker(audioContext.current.sampleRate);
    }
    if(worklet.current) {
      return;
    }
    let ctx = audioContext.current;
    ctx.resume();
    try {
      worklet.current = new AudioWorkletNode(ctx, 'moshi-processor');
    } catch (err) {
      await ctx.audioWorklet.addModule(moshiProcessorUrl);
      worklet.current = new AudioWorkletNode(ctx, 'moshi-processor');
    }
    worklet.current.connect(ctx.destination);
  }, [audioContext, worklet]);

  const startConnection = useCallback(async() => {
      await startProcessor();
      const hasAccess = await getMicrophoneAccess();
      if (hasAccess) {
      // Values are already set in modelParams, they get passed to Conversation
    }
  }, [startProcessor, getMicrophoneAccess]);

  // Poll /api/status until the supervisor settles on "ready" or "error".
  // Returns the final status, or null if the connection was lost or we unmounted.
  const pollUntilSettled = useCallback(async (): Promise<Status | null> => {
    let nullCount = 0;
    for (;;) {
      if (!mountedRef.current) return null;
      await new Promise((res) => setTimeout(res, 2000));
      const st = await refreshStatus();
      if (!mountedRef.current) return null;
      if (st === null) {
        nullCount += 1;
        if (nullCount >= 5) return null;
        continue;
      }
      nullCount = 0;
      if (st.state === "ready" || st.state === "error") return st;
    }
  }, [refreshStatus]);

  const ensureModelLoaded = useCallback(async (): Promise<boolean> => {
    if (!selectedRepo) {
      setSwitchError("Please select a model first.");
      return false;
    }
    const s = await refreshStatus();
    if (s && s.state === "ready" && s.current_repo === selectedRepo) return true;
    setSwitching(true);
    setSwitchError(null);
    // Start (or wait out) a switch to the selected model. The supervisor only
    // runs one switch at a time and returns 409 "busy" while another is in
    // flight — so a 409 isn't a failure, it means "wait, then try again".
    for (let attempt = 0; attempt < 10; attempt++) {
      const r = await fetch("/api/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo: selectedRepo }),
      });
      if (r.status === 200) { setSwitching(false); return true; }
      if (r.status === 202 || r.status === 409) {
        const st = await pollUntilSettled();
        if (!mountedRef.current) return false;
        if (st === null) {
          setSwitchError("Lost connection to the server while loading the model.");
          setSwitching(false);
          return false;
        }
        if (st.state === "ready" && st.current_repo === selectedRepo) {
          setSwitching(false);
          return true;
        }
        if (st.state === "error") {
          setSwitchError(st.error || "Model failed to load.");
          setSwitching(false);
          return false;
        }
        // Settled on a different model (a 409'd switch finished) — retry ours.
        continue;
      }
      // 400 (unknown repo) or anything unexpected.
      setSwitchError("Could not start model switch.");
      setSwitching(false);
      return false;
    }
    setSwitchError("The model is taking too long to switch. Please try again.");
    setSwitching(false);
    return false;
  }, [refreshStatus, pollUntilSettled, selectedRepo]);

  const connect = useCallback(async () => {
    const ok = await ensureModelLoaded();
    if (ok) await startConnection();
  }, [ensureModelLoaded, startConnection]);

  if (destroyed) {
    return (
      <div className="text-center h-screen flex flex-col items-center justify-center gap-3">
        <h1 className="text-2xl text-black">Instance destroyed — billing stopped.</h1>
        <p className="text-sm text-gray-600">You can close this tab.</p>
      </div>
    );
  }

  if (switching) {
    const name = models.find((m) => m.id === selectedRepo)?.name ?? selectedRepo;
    return (
      <div className="text-center h-screen flex flex-col items-center justify-center gap-3">
        <h1 className="text-2xl text-black">Loading {name}…</h1>
        <p className="text-sm text-gray-600">This takes ~45 seconds. Please wait.</p>
      </div>
    );
  }

  return (
    <>
      {(hasMicrophoneAccess && audioContext.current && worklet.current) ? (
        <Conversation
        workerAddr={overrideWorkerAddr ?? ""}
        audioContext={audioContext as MutableRefObject<AudioContext|null>}
        worklet={worklet as MutableRefObject<AudioWorkletNode|null>}
        theme={theme}
        startConnection={startConnection}
        modelName={status?.display_name ?? null}
        teardownAvailable={teardownAvailable}
        onTeardown={handleTeardown}
        {...modelParams}
        />
      ) : (
        <Homepage
          startConnection={connect}
          showMicrophoneAccessMessage={showMicrophoneAccessMessage}
          textPrompt={modelParams.textPrompt}
          setTextPrompt={modelParams.setTextPrompt}
          voicePrompt={modelParams.voicePrompt}
          setVoicePrompt={modelParams.setVoicePrompt}
          models={models}
          selectedRepo={selectedRepo}
          setSelectedRepo={setSelectedRepo}
          loadedName={status?.display_name ?? null}
          switchError={switchError}
          teardownAvailable={teardownAvailable}
          onTeardown={handleTeardown}
          teardownError={teardownError}
        />
      )}
    </>
  );
};
