import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type RefObject,
} from "react";
import {
  getStudio,
  postJson,
  readError,
  type DownloadState,
  type HistoryItem,
  type Job,
  type Mode,
  type ModelInfo,
  type ReferenceClip,
  type SavedVoice,
  type Studio,
} from "./api";

const MODES: { id: Mode; title: string; detail: string }[] = [
  { id: "design", title: "Design", detail: "Describe a voice" },
  { id: "clone", title: "Clone", detail: "Use a reference clip" },
  { id: "preset", title: "Preset", detail: "Published speakers" },
];

const HISTORY_FILTERS: { id: Mode | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "design", label: "Design" },
  { id: "clone", label: "Clone" },
  { id: "preset", label: "Preset" },
];

export function App() {
  const [draft] = useState(loadDraft);
  const [studio, setStudio] = useState<Studio | null>(null);
  const [connected, setConnected] = useState(true);
  const [mode, setMode] = useState<Mode>(draft.mode ?? "design");
  const [modelId, setModelId] = useState(draft.modelId ?? "");
  const [script, setScript] = useState(draft.script ?? "");
  const [language, setLanguage] = useState(draft.language ?? "english");
  const [description, setDescription] = useState(draft.description ?? "");
  const [delivery, setDelivery] = useState(draft.delivery ?? "");
  const [speaker, setSpeaker] = useState(draft.speaker ?? "Ryan");
  const [referenceId, setReferenceId] = useState("");
  const [ownsVoice, setOwnsVoice] = useState(false);
  const [temperature, setTemperature] = useState(draft.temperature ?? 0.9);
  const [pace, setPace] = useState(draft.pace ?? 1);
  const [namingVoice, setNamingVoice] = useState(false);
  const [voiceName, setVoiceName] = useState("");
  const [confirmVoice, setConfirmVoice] = useState("");
  const [sayOpen, setSayOpen] = useState(false);
  const [sayWritten, setSayWritten] = useState("");
  const [saySpoken, setSaySpoken] = useState("");
  const [historyMode, setHistoryMode] = useState<Mode | "all">("all");
  const [historyQuery, setHistoryQuery] = useState("");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("reed-theme") === "dark" ? "dark" : "light";
    } catch {
      return "light";
    }
  });
  const [appliedDescription, setAppliedDescription] = useState(
    draft.appliedDescription ?? "",
  );
  const voiceApplied =
    description.trim().length >= 8 && description.trim() === appliedDescription;
  const drawerRef = useRef<HTMLElement>(null);
  const modelsTrigger = useRef<HTMLButtonElement>(null);

  function applyVoice() {
    if (description.trim().length < 8) return;
    setAppliedDescription(description.trim());
    setFormError("");
  }

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("reed-theme", theme);
    } catch {
      /* Storage may be disabled. */
    }
  }, [theme]);

  useEffect(() => {
    if (!inspectorOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const workspace = document.querySelector<HTMLElement>(".workspace");
    const header = document.querySelector<HTMLElement>(".top");
    workspace?.setAttribute("inert", "");
    header?.setAttribute("inert", "");
    drawerRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        setInspectorOpen(false);
      }
      if (event.key !== "Tab") return;
      const items = Array.from(
        drawerRef.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), a[href], input:not(:disabled), select, summary, [tabindex='0']",
        ) ?? [],
      ).filter((el) => el.getClientRects().length > 0);
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      workspace?.removeAttribute("inert");
      header?.removeAttribute("inert");
      window.removeEventListener("keydown", onKey, true);
      (previous?.isConnected ? previous : modelsTrigger.current)?.focus();
    };
  }, [inspectorOpen]);
  const [job, setJob] = useState<Job | null>(null);
  const [jobSeenAt, setJobSeenAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultId, setResultId] = useState<string | null>(null);
  const [playToken, setPlayToken] = useState(0);
  const [copied, setCopied] = useState(false);
  const [undoScript, setUndoScript] = useState<string | null>(null);
  const [historyPicked, setHistoryPicked] = useState(false);
  const scriptBox = useRef<HTMLTextAreaElement>(null);
  const voiceBox = useRef<HTMLTextAreaElement>(null);
  const scriptRef = useRef(script);
  scriptRef.current = script;
  const generateRef = useRef<() => void>(() => {});
  const cancelRef = useRef<string | null>(null);
  const historyRef = useRef<HistoryItem[]>([]);
  const resultIdRef = useRef<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const [recording, setRecording] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingUrl, setPendingUrl] = useState("");
  const [transcript, setTranscript] = useState("");
  const [confirmDelete, setConfirmDelete] = useState("");

  const refresh = useCallback(
    async (currentScript: string) => {
      try {
        const next = await getStudio(currentScript, mode, speaker);
        setStudio(next);
        setConnected(true);
        setModelId((current) => current || preferredModel(next, mode));
        setResultId((current) => current ?? next.history[0]?.id ?? null);
        setJob((current) => current ?? next.jobs[0] ?? null);
      } catch {
        setConnected(false);
      }
    },
    [mode, speaker],
  );

  useEffect(() => {
    const handle = window.setTimeout(() => {
      void refresh(script);
    }, 300);
    return () => window.clearTimeout(handle);
  }, [script, refresh]);

  useEffect(() => {
    let source: EventSource | null = null;
    let stopped = false;
    let retry = 0;
    const connect = () => {
      source = new EventSource("/api/events");
      source.onopen = () => {
        setConnected(true);
        void refresh(scriptRef.current);
      };
      source.onerror = () => {
        setConnected(false);
        source?.close();
        source = null;
        if (!stopped) retry = window.setTimeout(connect, 1000);
      };
      source.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "download") {
          setStudio((current) => {
            if (!current) return current;
            return {
              ...current,
              models: current.models.map((model) =>
                model.id === data.model_id
                  ? {
                      ...model,
                      download: data.download,
                      installed: data.download.state === "ready",
                    }
                  : model,
              ),
            };
          });
        }
        if (data.type === "job") {
          setJob(data.job);
          setJobSeenAt(Date.now());
          if (
            data.job.state === "completed" ||
            data.job.state === "failed" ||
            data.job.state === "cancelled"
          ) {
            void refresh(scriptRef.current);
            if (data.job.history_id) {
              setResultId(data.job.history_id);
              setPlayToken((token) => token + 1);
              setHistoryPicked(true);
            }
          }
        }
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      source?.close();
    };
  }, [refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        generateRef.current();
      }
      if (event.key === "Escape" && cancelRef.current) {
        event.preventDefault();
        void postJson(`/api/jobs/${cancelRef.current}/cancel`, {});
        return;
      }
      if (event.key === "Escape") {
        const field = event.target;
        if (
          field instanceof HTMLInputElement &&
          field.classList.contains("history-search") &&
          field.value
        ) {
          event.preventDefault();
          setHistoryQuery("");
        }
      }
      const target = event.target;
      const inField =
        target instanceof HTMLElement &&
        Boolean(target.closest("textarea, input, select"));
      const onControl =
        target instanceof HTMLElement && Boolean(target.closest("button, a"));
      if (
        !inField &&
        !onControl &&
        (event.key === "ArrowDown" || event.key === "ArrowUp")
      ) {
        const items = historyRef.current;
        if (!items.length) return;
        event.preventDefault();
        const index = items.findIndex(
          (item) => item.id === resultIdRef.current,
        );
        const delta = event.key === "ArrowDown" ? 1 : -1;
        const next =
          index < 0
            ? 0
            : Math.min(items.length - 1, Math.max(0, index + delta));
        if (index >= 0 && items[next].id === resultIdRef.current) return;
        setHistoryPicked(true);
        setResultId(items[next].id);
        setPlayToken((token) => token + 1);
      }
      if (!inField && !event.metaKey && !event.ctrlKey && !event.altKey) {
        if (event.key === "1") setMode("design");
        if (event.key === "2") setMode("clone");
        if (event.key === "3") setMode("preset");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const payload: Draft = {
      mode,
      modelId,
      script,
      language,
      description,
      delivery,
      speaker,
      temperature,
      pace,
      appliedDescription,
    };
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
    } catch {
      /* Keep the session usable without storage. */
    }
  }, [
    mode,
    modelId,
    script,
    language,
    description,
    delivery,
    speaker,
    temperature,
    pace,
    appliedDescription,
  ]);

  const models = studio?.models.filter((model) => model.task === mode) ?? [];
  const model = models.find((item) => item.id === modelId) ?? models[0];
  const languageInfo = studio?.languages.find((item) => item.id === language);
  const reference =
    studio?.references.find((item) => item.id === referenceId) ?? null;
  const result =
    studio?.history.find((item) => item.id === (resultId || job?.history_id)) ??
    null;
  const playingHint = result ? voiceHint(result, studio?.references ?? []) : "";
  const sameVoice =
    mode === "design"
      ? result?.mode === "design" &&
        playingHint === appliedDescription.replace(/\s+/g, " ").trim()
      : mode === "preset"
        ? result?.mode === "preset" &&
          playingHint === speaker.replaceAll("_", " ")
        : result?.mode === "clone" &&
          playingHint === (reference?.original_name ?? "");
  const showPlayingHint = Boolean(playingHint) && !sameVoice;
  const takeLanguage =
    result && result.language !== language
      ? (studio?.languages.find((item) => item.id === result.language)?.label ??
        "")
      : "";
  const estimate = model ? studio?.estimates[model.id] : undefined;

  useEffect(() => {
    if (!studio || mode !== "clone") return;
    if (studio.references.some((item) => item.id === referenceId)) return;
    if (studio.references[0]) setReferenceId(studio.references[0].id);
  }, [studio, mode, referenceId]);

  useEffect(() => {
    if (!studio) return;
    const available = studio.models.filter((item) => item.task === mode);
    if (!available.some((item) => item.id === modelId)) {
      setModelId(preferredModel(studio, mode));
    }
  }, [mode, studio, modelId]);

  const blockReason = useMemo(() => {
    if (!connected) return "The local backend is disconnected.";
    if (!script.trim()) return "Write the words you want to hear.";
    if (script.length > 5000) return "Keep this take under 5,000 characters.";
    if (!model) return "Choose a checkpoint.";
    if (model.download.state !== "ready")
      return "Download this checkpoint before generating.";
    if (mode === "design" && !voiceApplied)
      return "Apply your voice description before generating.";
    if (mode === "clone" && !reference) {
      return studio && studio.references.length > 0
        ? "Select a reference clip."
        : "Add a reference clip.";
    }
    if (mode === "clone" && !ownsVoice)
      return "Confirm that you have permission to use this voice.";
    if (mode === "preset" && delivery.trim() && !model.supports_instruct) {
      return "This checkpoint does not support delivery instructions.";
    }
    if (studio?.plan_error) return studio.plan_error;
    return "";
  }, [
    connected,
    script,
    model,
    mode,
    voiceApplied,
    reference,
    ownsVoice,
    delivery,
    studio,
  ]);

  async function onGenerate() {
    if (
      !model ||
      blockReason ||
      busy ||
      (job && ["queued", "loading", "generating"].includes(job.state))
    )
      return;
    setFormError("");
    setBusy(true);
    const body: Record<string, unknown> = {
      mode,
      model_id: model.id,
      script,
      language,
      temperature,
      pace,
      speaker: mode === "preset" ? speaker : "",
      instruct:
        mode === "design"
          ? appliedDescription
          : mode === "preset"
            ? delivery
            : "",
      reference_id: mode === "clone" ? referenceId : "",
      owns_voice: mode === "clone" ? ownsVoice : false,
    };
    try {
      const response = await postJson("/api/generate", body);
      if (!response.ok) {
        setFormError(await readError(response));
        return;
      }
      const created = (await response.json()) as Job;
      setJob(created);
      setJobSeenAt(Date.now());
    } catch {
      setConnected(false);
      setFormError("The local backend is disconnected.");
    } finally {
      setBusy(false);
    }
  }
  generateRef.current = () => {
    void onGenerate();
  };

  const canSaveVoice =
    mode === "design"
      ? voiceApplied
      : mode === "preset"
        ? Boolean(speaker)
        : Boolean(reference);

  function recallVoice(voice: SavedVoice) {
    setMode(voice.mode);
    setLanguage(voice.language);
    setFormError("");
    setPace(voice.pace);
    setNamingVoice(false);
    if (voice.mode === "design") {
      setDescription(voice.voice_description);
      setAppliedDescription(voice.voice_description.trim());
    }
    if (voice.mode === "preset" && voice.speaker) setSpeaker(voice.speaker);
    if (voice.mode === "clone") {
      if (studio?.references.some((item) => item.id === voice.reference_id)) {
        setReferenceId(voice.reference_id);
      } else {
        setFormError("That reference clip is no longer on this Mac.");
      }
    }
  }

  async function saveVoice(event: { preventDefault: () => void }) {
    event.preventDefault();
    const response = await postJson("/api/voices", {
      name: voiceName,
      mode,
      language,
      voice_description: appliedDescription,
      speaker,
      reference_id: referenceId,
      pace,
    });
    if (!response.ok) {
      setFormError(await readError(response));
      return;
    }
    setVoiceName("");
    setNamingVoice(false);
    await refresh(script);
  }

  async function saveSay(event: { preventDefault: () => void }) {
    event.preventDefault();
    const response = await postJson("/api/say-as", {
      written: sayWritten,
      spoken: saySpoken,
    });
    if (!response.ok) {
      setFormError(await readError(response));
      return;
    }
    setSayWritten("");
    setSaySpoken("");
    await refresh(script);
  }

  async function onDownload(action: "download" | "pause" | "remove") {
    if (!model) return;
    setFormError("");
    const response = await postJson(`/api/models/${action}`, {
      model_id: model.id,
    });
    if (!response.ok) {
      setFormError(await readError(response));
      return;
    }
    const download = (await response.json()) as DownloadState;
    setStudio((current) => {
      if (!current) return current;
      return {
        ...current,
        models: current.models.map((item) =>
          item.id === model.id
            ? { ...item, download, installed: download.state === "ready" }
            : item,
        ),
      };
    });
  }

  async function onBenchmark() {
    if (!model) return;
    setFormError("");
    const response = await postJson("/api/benchmark", {
      model_id: model.id,
      reference_id: referenceId,
      owns_voice: ownsVoice,
    });
    if (!response.ok) {
      setFormError(await readError(response));
      return;
    }
    const created = (await response.json()) as Job;
    setJob(created);
    setJobSeenAt(Date.now());
  }

  async function uploadReference(file: File) {
    if (transcript.trim().length < 2) {
      setFormError("Add a transcript of the words spoken in the clip.");
      return;
    }
    const data = new FormData();
    data.set("file", file);
    data.set("transcript", transcript.trim());
    const response = await fetch("/api/references", {
      method: "POST",
      body: data,
    });
    if (!response.ok) {
      setFormError(await readError(response));
      return;
    }
    const clip = (await response.json()) as ReferenceClip;
    setReferenceId(clip.id);
    setPendingFile(null);
    if (pendingUrl) URL.revokeObjectURL(pendingUrl);
    setPendingUrl("");
    setOwnsVoice(false);
    await refresh(script);
  }

  async function toggleRecord() {
    if (recording && recorder.current) {
      recorder.current.stop();
      setRecording(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const media = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      media.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      media.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunks, { type: media.mimeType || "audio/webm" });
        const file = new File([blob], "recording.webm", { type: blob.type });
        if (pendingUrl) URL.revokeObjectURL(pendingUrl);
        setPendingFile(file);
        setPendingUrl(URL.createObjectURL(blob));
      };
      recorder.current = media;
      media.start();
      setRecording(true);
    } catch {
      setFormError("Reed could not access the microphone.");
    }
  }

  const elapsed = liveElapsed(job, jobSeenAt, now);
  const active = job && ["queued", "loading", "generating"].includes(job.state);
  cancelRef.current = active && job ? job.id : null;
  useEffect(() => {
    document.title = active ? "Generating · Reed" : "Reed";
  }, [active]);
  useEffect(() => {
    const field = voiceBox.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(Math.max(field.scrollHeight, 52), 180)}px`;
  }, [description, mode]);
  const history = (studio?.history ?? []).filter((item) => {
    if (historyMode !== "all" && item.mode !== historyMode) return false;
    const query = historyQuery.trim().toLowerCase();
    if (!query) return true;
    return [
      item.script,
      item.language,
      item.speaker,
      item.voice_description,
      voiceHint(item, studio?.references ?? []),
    ]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
  historyRef.current = history;
  resultIdRef.current = result?.id ?? null;
  useEffect(() => {
    if (!historyPicked) return;
    document
      .querySelector(".take-wrap.on")
      ?.scrollIntoView({ block: "nearest" });
  }, [result?.id, historyPicked]);

  function reuseTake(item: HistoryItem) {
    setMode(item.mode);
    setScript(item.script);
    setLanguage(item.language || "english");
    setModelId(item.model_id || "");
    if (item.mode === "design") {
      setDescription(item.voice_description || "");
      setAppliedDescription((item.voice_description || "").trim());
    }
    if (item.mode === "preset") {
      if (item.speaker) setSpeaker(item.speaker);
      setDelivery(item.instruct || "");
    }
    if (item.mode === "clone" && item.reference_id)
      setReferenceId(item.reference_id);
    setResultId(item.id);
    window.setTimeout(() => scriptBox.current?.focus(), 0);
  }

  return (
    <div className="app">
      <header className="top">
        <a
          className="word"
          href="#"
          aria-label="Reed studio"
          onClick={(event) => event.preventDefault()}
        >
          <ReedMark />
          <span>
            reed<span className="brand-dot">.</span>
          </span>
        </a>
        <div className="breadcrumb">
          <span>Workspace</span>
          <span className="slash">/</span>
          <strong>Voice studio</strong>
        </div>
        <div className={connected ? "connection" : "connection off"}>
          <i />
          {connected ? "Running locally" : "Disconnected"}
        </div>
        <button
          className="icon-button mobile-menu"
          aria-label="Toggle history"
          aria-expanded={railOpen}
          onClick={() => setRailOpen(!railOpen)}
        >
          <Icon name="menu" />
        </button>
        <button
          className="icon-button"
          aria-label={theme === "light" ? "Use dark theme" : "Use light theme"}
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
        >
          <Icon name={theme === "light" ? "moon" : "sun"} />
        </button>
        <button
          className="btn models-trigger"
          ref={modelsTrigger}
          aria-haspopup="dialog"
          aria-expanded={inspectorOpen}
          onClick={() => setInspectorOpen(true)}
        >
          <Icon name="sliders" />
          Models{" "}
          {studio?.models.some((item) => item.installed) && (
            <span className="installed-dot" />
          )}
        </button>
      </header>
      {!connected && (
        <p className="banner" role="alert">
          Your local server is disconnected. Start Reed with ./run to reconnect.
        </p>
      )}
      {formError && (
        <div className="form-error" role="alert">
          <span>{formError}</span>
          <button
            className="icon-button"
            aria-label="Dismiss error"
            onClick={() => setFormError("")}
          >
            <Icon name="close" />
          </button>
        </div>
      )}
      <div className="workspace">
        <aside
          className={railOpen ? "rail mobile-open" : "rail"}
          aria-label="Workspace navigation"
        >
          <div className="rail-heading">CREATE</div>
          <button
            className="studio-nav"
            onClick={() => {
              setRailOpen(false);
              scriptBox.current?.focus();
            }}
          >
            <Icon name="wave" />
            <span>Voice studio</span>
            <span className="nav-badge">Local</span>
          </button>
          <button
            className="new-take"
            onClick={() => {
              setUndoScript(script);
              setScript("");
              setFormError("");
              setRailOpen(false);
              scriptBox.current?.focus();
            }}
          >
            <Icon name="plus" />
            New script <kbd>⌘ ↵</kbd>
          </button>
          {studio && studio.voices.length > 0 && (
            <>
              <p className="section-label">Voices</p>
              <div className="voice-list">
                {studio.voices.map((voice) => (
                  <div className="voice-row" key={voice.id}>
                    <button
                      className="btn btn-quiet name"
                      type="button"
                      onClick={() => recallVoice(voice)}
                      title={voice.mode}
                    >
                      {voice.name}
                    </button>
                    {confirmVoice === voice.id ? (
                      <>
                        <button
                          className="btn btn-quiet btn-danger"
                          type="button"
                          onClick={() => {
                            setConfirmVoice("");
                            void fetch(`/api/voices/${voice.id}`, {
                              method: "DELETE",
                            }).then(() => refresh(script));
                          }}
                        >
                          Delete
                        </button>
                        <button
                          className="btn btn-quiet"
                          type="button"
                          onClick={() => setConfirmVoice("")}
                        >
                          Keep
                        </button>
                      </>
                    ) : (
                      <button
                        className="btn btn-quiet"
                        type="button"
                        onClick={() => setConfirmVoice(voice.id)}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
          <p className="section-label" title="Arrow keys move between takes.">
            History
          </p>
          <div className="filters" role="group" aria-label="Filter history">
            {HISTORY_FILTERS.map((item) => {
              const count = (studio?.history ?? []).filter(
                (take) => item.id === "all" || take.mode === item.id,
              ).length;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={historyMode === item.id}
                  onClick={() => setHistoryMode(item.id)}
                >
                  {item.label}
                  {studio ? <span className="num">{count}</span> : null}
                </button>
              );
            })}
          </div>
          <input
            className="history-search"
            type="search"
            aria-label="Search takes"
            placeholder="Search takes"
            value={historyQuery}
            onChange={(event) => setHistoryQuery(event.target.value)}
          />
          <div className="history">
            {studio && history.length === 0 && (
              <p className="empty">
                {studio.history.length === 0
                  ? "Takes stay on this Mac."
                  : historyQuery.trim()
                    ? "No takes match this search."
                    : "No takes in this filter."}
              </p>
            )}
            {history.map((item) => (
              <HistoryRow
                key={item.id}
                item={item}
                now={now}
                hint={voiceHint(item, studio?.references ?? [])}
                selected={item.id === result?.id}
                expanded={historyPicked && item.id === result?.id}
                confirming={confirmDelete === item.id}
                onSelect={() => {
                  setHistoryPicked(true);
                  setResultId(item.id);
                  setPlayToken((token) => token + 1);
                }}
                onReuse={() => reuseTake(item)}
                onAsk={() => setConfirmDelete(item.id)}
                onCancel={() => setConfirmDelete("")}
                onDelete={async () => {
                  await fetch(`/api/history/${item.id}`, { method: "DELETE" });
                  if (resultId === item.id) setResultId(null);
                  setConfirmDelete("");
                  await refresh(script);
                }}
              />
            ))}
          </div>

          <div className="rail-footer">
            <div className="privacy-icon">
              <Icon name="shield" />
            </div>
            <div>
              <strong>Yours. Entirely.</strong>
              <p>Voices and audio stay on this Mac.</p>
            </div>
          </div>
        </aside>
        <main className="stage" id="studio">
          <div className="stage-inner">
            <header className="studio-heading">
              <div>
                <p className="eyebrow">YOUR LOCAL VOICE STUDIO</p>
                <h1>Words, meet voice.</h1>
                <p className="intro">
                  A little character. A lot to say. Make it sound like you
                  imagined.
                </p>
              </div>
              <span className="studio-number" aria-hidden="true">
                01 / STUDIO
              </span>
            </header>
            <div className="studio-grid">
              <section className="sheet" aria-label="Script">
                <div className="card-heading">
                  <div className="card-title">
                    <Icon name="text" />
                    <h2>The script</h2>
                  </div>
                  <span className="label-actions">
                    {!script.trim() && studio && (
                      <button
                        className="btn btn-quiet"
                        onClick={() => setScript(studio.sample.text)}
                      >
                        Try a sample <Icon name="arrow" />
                      </button>
                    )}
                    {script.trim() && (
                      <button
                        className="btn btn-quiet"
                        onClick={() => {
                          setUndoScript(script);
                          setScript("");
                        }}
                      >
                        Clear
                      </button>
                    )}
                    {!script.trim() && undoScript && (
                      <button
                        className="btn btn-quiet"
                        onClick={() => {
                          setScript(undoScript);
                          setUndoScript(null);
                        }}
                      >
                        Undo
                      </button>
                    )}
                  </span>
                </div>
                <textarea
                  ref={scriptBox}
                  className="script"
                  aria-label="Words to speak"
                  value={script}
                  placeholder={
                    "Every voice starts with a few words.\nWhat do you want yours to say?"
                  }
                  onChange={(event) => setScript(event.target.value)}
                />
                <div className="script-foot">
                  <span>
                    <Icon name="text" />
                    Plain text, full of possibility.
                  </span>
                  <span
                    className={
                      script.length > 5000 ? "char-count over" : "char-count"
                    }
                  >
                    {script.length.toLocaleString()} <span>/ 5,000</span>
                  </span>
                </div>
                <details className="script-tools">
                  <summary>
                    <Icon name="sliders" /> Script tools & pronunciation{" "}
                    <Icon name="chevron" />
                  </summary>
                  <div className="tools-content">
                    <p className="hint">
                      A blank line adds a breath. Use [pause] or [pause 1.2] for
                      a pause. In Preset mode, start a line with Ryan: to change
                      speakers.
                    </p>
                    {studio && studio.parts > 1 && (
                      <p className="hint">
                        {studio.parts} parts will be generated in order.
                      </p>
                    )}
                    {studio?.spoken &&
                      studio.spoken.replace(/\s+/g, " ").trim() !==
                        script.replace(/\s+/g, " ").trim() && (
                        <p className="hint">The model hears: {studio.spoken}</p>
                      )}
                    <div className="say-block">
                      <button
                        className="btn btn-quiet"
                        type="button"
                        aria-expanded={sayOpen}
                        onClick={() => setSayOpen((open) => !open)}
                      >
                        Pronunciations
                        {studio?.say_as.length
                          ? ` · ${studio.say_as.length}`
                          : ""}
                      </button>
                      {sayOpen && (
                        <>
                          {studio?.say_as.map((item) => (
                            <div className="say-row" key={item.id}>
                              <span>{item.written}</span>
                              <span className="meta">{item.spoken}</span>
                              <button
                                className="btn btn-quiet"
                                type="button"
                                onClick={() => {
                                  void fetch(`/api/say-as/${item.id}`, {
                                    method: "DELETE",
                                  }).then(() => refresh(script));
                                }}
                              >
                                Remove
                              </button>
                            </div>
                          ))}
                          <form
                            className="say-row"
                            onSubmit={(event) => void saveSay(event)}
                          >
                            <input
                              aria-label="Written word"
                              value={sayWritten}
                              placeholder="Written"
                              onChange={(event) =>
                                setSayWritten(event.target.value)
                              }
                            />
                            <input
                              aria-label="Spoken form"
                              value={saySpoken}
                              placeholder="Spoken as"
                              onChange={(event) =>
                                setSaySpoken(event.target.value)
                              }
                            />
                            <button className="btn" type="submit">
                              Add
                            </button>
                          </form>
                        </>
                      )}
                    </div>

                    <label
                      className="slider-label variation-control"
                      title="Controls variation between generations. Higher values produce less predictable delivery."
                    >
                      <span>Voice variation</span>
                      <input
                        type="range"
                        min={0.1}
                        max={1.5}
                        step={0.05}
                        value={temperature}
                        aria-label="Voice variation"
                        aria-valuetext={temperature.toFixed(2)}
                        onChange={(event) =>
                          setTemperature(Number(event.target.value))
                        }
                      />
                      <span className="num">{temperature.toFixed(2)}</span>
                    </label>
                  </div>
                </details>
                <div className="sheet-bar">
                  <label className="sr" htmlFor="language">
                    Language
                  </label>
                  <select
                    id="language"
                    aria-label="Language"
                    value={language}
                    onChange={(event) => setLanguage(event.target.value)}
                  >
                    {studio?.languages.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                        {item.experimental ? " · experimental" : ""}
                      </option>
                    ))}
                  </select>
                  <label
                    className="slider-label"
                    title="Pace changes the length after synthesis and keeps the pitch. It is not a model control."
                  >
                    <span>Pace</span>
                    <input
                      type="range"
                      min={0.8}
                      max={1.25}
                      step={0.05}
                      value={pace}
                      aria-valuetext={`${pace.toFixed(2)} times`}
                      onChange={(event) => setPace(Number(event.target.value))}
                    />
                    <span className="num">{pace.toFixed(2)}×</span>
                  </label>
                  <span className="grow" />
                  {estimate?.kind === "measured" &&
                    script.trim() &&
                    !active && (
                      <span
                        className="keys"
                        title="Estimated time to finish this script, scaled from a local measurement on this Mac. Loading is separate."
                      >
                        about {formatSeconds(estimate.generate_sec)}
                      </span>
                    )}
                  <span className="keys" aria-hidden="true">
                    ⌘↵
                  </span>
                  {active && job && (
                    <button
                      className="btn"
                      type="button"
                      onClick={() =>
                        void postJson(`/api/jobs/${job.id}/cancel`, {})
                      }
                    >
                      Cancel
                    </button>
                  )}
                  <button
                    className="btn btn-primary generate"
                    type="button"
                    aria-keyshortcuts="Meta+Enter"
                    title={active ? undefined : blockReason || "⌘↩"}
                    disabled={Boolean(blockReason) || busy || Boolean(active)}
                    onClick={() => void onGenerate()}
                  >
                    {active
                      ? `Generating ${formatSeconds(elapsed)}`
                      : mode === "preset" && speaker
                        ? `Generate · ${speaker.replaceAll("_", " ")}`
                        : "Generate speech"}
                  </button>
                </div>

                <p className="generation-hint" aria-live="polite">
                  {active
                    ? "Creating on your Mac. You can keep working here."
                    : blockReason ||
                      "Ready when you are. ⌘ / Ctrl + Enter to generate."}
                </p>
              </section>
              <section className="voice-card" aria-label="Voice settings">
                <div className="card-heading">
                  <div className="card-title">
                    <Icon name="wave" />
                    <h2>The voice</h2>
                  </div>
                  <Icon name="wave" />
                </div>
                <div className="modes" role="group" aria-label="Creation mode">
                  {MODES.map((item) => (
                    <button
                      key={item.id}
                      className="mode"
                      aria-pressed={mode === item.id}
                      title={item.detail}
                      onClick={() => {
                        setMode(item.id);
                        setFormError("");
                      }}
                    >
                      <ModeIcon id={item.id} />
                      {item.title}
                    </button>
                  ))}
                </div>
                <div className="voice-content" key={mode}>
                  {mode === "design" && (
                    <div className="voice-block">
                      <div className="voice-intro">
                        <h3>Imagine a voice.</h3>
                        <p>
                          Describe its character, accent and the way it speaks.
                        </p>
                      </div>
                      <label
                        className="field-label"
                        htmlFor="voice-description"
                      >
                        VOICE DESCRIPTION <span>Not spoken aloud</span>
                      </label>
                      <textarea
                        id="voice-description"
                        ref={voiceBox}
                        className="voice"
                        aria-label="Voice description"
                        value={description}
                        placeholder="A warm, expressive voice with a soft British accent. Calm, slightly husky, with a smile in the delivery…"
                        onChange={(event) => setDescription(event.target.value)}
                      />
                      <div className="example-label">A starting point</div>
                      <div className="examples">
                        {studio?.examples.map((example) => (
                          <button
                            key={example.id}
                            className="btn"
                            aria-pressed={description === example.text}
                            onClick={() => setDescription(example.text)}
                          >
                            {example.label}
                          </button>
                        ))}
                      </div>
                      <button
                        className={
                          voiceApplied
                            ? "btn apply-voice applied"
                            : "btn apply-voice"
                        }
                        disabled={description.trim().length < 8 || voiceApplied}
                        onClick={applyVoice}
                      >
                        <Icon name={voiceApplied ? "check" : "arrow"} />
                        {voiceApplied
                          ? "Voice applied"
                          : appliedDescription
                            ? "Apply changes"
                            : "Apply voice"}
                      </button>
                      <p
                        className={
                          voiceApplied ? "apply-status success" : "apply-status"
                        }
                        role="status"
                      >
                        {voiceApplied
                          ? "This description will be used for your next generation."
                          : appliedDescription
                            ? "You have unapplied changes. Apply them before generating."
                            : "Describe your voice, then apply it to your script."}
                      </p>
                      {appliedDescription && !voiceApplied && (
                        <button
                          className="btn btn-quiet"
                          onClick={() => setDescription(appliedDescription)}
                        >
                          Discard changes
                        </button>
                      )}
                    </div>
                  )}
                  {mode === "clone" && studio && (
                    <ClonePanel
                      dragOver={dragOver}
                      onDragOver={(event) => {
                        event.preventDefault();
                        setDragOver(true);
                      }}
                      onDragLeave={() => setDragOver(false)}
                      onDrop={(event) => {
                        event.preventDefault();
                        setDragOver(false);
                        const file = event.dataTransfer.files?.[0];
                        if (!file) return;
                        if (pendingUrl) URL.revokeObjectURL(pendingUrl);
                        setPendingFile(file);
                        setPendingUrl(URL.createObjectURL(file));
                      }}
                      studio={studio}
                      reference={reference}
                      transcript={transcript}
                      setTranscript={setTranscript}
                      ownsVoice={ownsVoice}
                      setOwnsVoice={setOwnsVoice}
                      recording={recording}
                      pendingUrl={pendingUrl}
                      pendingFile={pendingFile}
                      fileRef={fileRef}
                      onRecord={() => void toggleRecord()}
                      onFile={(file) => {
                        if (pendingUrl) URL.revokeObjectURL(pendingUrl);
                        setPendingFile(file);
                        setPendingUrl(URL.createObjectURL(file));
                      }}
                      onUse={() =>
                        pendingFile && void uploadReference(pendingFile)
                      }
                      onSelect={setReferenceId}
                      onDelete={async (id) => {
                        await fetch(`/api/references/${id}`, {
                          method: "DELETE",
                        });
                        if (referenceId === id) setReferenceId("");
                        await refresh(script);
                      }}
                    />
                  )}
                  {mode === "preset" && studio && model && (
                    <div className="preset-block">
                      <p className="hint" style={{ paddingTop: 12 }}>
                        {studio.preset_note}
                      </p>
                      <div
                        className="speakers"
                        role="group"
                        aria-label="Preset speakers"
                      >
                        {studio.speakers.map((item) => (
                          <button
                            key={item.id}
                            className="speaker"
                            aria-pressed={speaker === item.id}
                            onClick={() => setSpeaker(item.id)}
                          >
                            <strong>{item.id.replaceAll("_", " ")}</strong>
                            <small title={item.description}>
                              {item.description}
                            </small>
                            <small className="lang">{item.native}</small>
                          </button>
                        ))}
                      </div>
                      {model.supports_instruct ? (
                        <>
                          <label className="sheet-label" htmlFor="delivery">
                            Delivery
                          </label>
                          <textarea
                            id="delivery"
                            className="voice"
                            aria-label="Delivery instruction"
                            value={delivery}
                            placeholder="Optional. Quietly, with a smile in the voice."
                            onChange={(event) =>
                              setDelivery(event.target.value)
                            }
                          />
                        </>
                      ) : (
                        <p className="note">
                          This 0.6B CustomVoice checkpoint does not accept
                          delivery instructions. The 1.7B CustomVoice checkpoint
                          does.
                          {delivery.trim()
                            ? " The note you wrote is kept, and it is sent only with that checkpoint."
                            : ""}
                        </p>
                      )}
                    </div>
                  )}
                </div>
                <div className="save-row">
                  {namingVoice ? (
                    <form
                      className="save-row inner"
                      onSubmit={(event) => void saveVoice(event)}
                    >
                      <input
                        aria-label="Voice name"
                        value={voiceName}
                        placeholder="Name this voice"
                        autoFocus
                        onChange={(event) => setVoiceName(event.target.value)}
                      />
                      <button
                        className="btn"
                        type="submit"
                        disabled={!voiceName.trim() || !canSaveVoice}
                      >
                        Save
                      </button>
                      <button
                        className="btn btn-quiet"
                        type="button"
                        onClick={() => setNamingVoice(false)}
                      >
                        Cancel
                      </button>
                    </form>
                  ) : (
                    <button
                      className="btn btn-quiet"
                      type="button"
                      disabled={!canSaveVoice}
                      onClick={() => setNamingVoice(true)}
                    >
                      Save voice
                    </button>
                  )}
                </div>

                <button
                  className="model-summary"
                  onClick={() => setInspectorOpen(true)}
                >
                  <div className="model-icon">
                    <Icon name="chip" />
                  </div>
                  <span>
                    <strong>
                      {model ? `Qwen3 · ${model.parameters}` : "Choose a model"}
                    </strong>
                    <small>
                      {model
                        ? `${model.family} · ${model.quantization}`
                        : "Local speech engine"}
                    </small>
                  </span>
                  <span
                    className={
                      model?.installed ? "model-state ready" : "model-state"
                    }
                  >
                    {model?.installed ? "Ready" : "Set up"}
                  </span>
                  <Icon name="chevron" />
                </button>
              </section>
            </div>
            {languageInfo?.experimental && (
              <p className="note language-note">{languageInfo.note}</p>
            )}
            {(result ||
              active ||
              (job &&
                (job.state === "failed" || job.state === "cancelled"))) && (
              <section className="listen-dock" aria-label="Audio result">
                <div className="sheet-label dock-label">
                  <span>
                    {active && job && job.chunks_done > 0
                      ? "Live preview"
                      : result
                        ? "Your audio"
                        : "Generation"}
                  </span>
                  {!(active && job && job.chunks_done > 0) &&
                    (showPlayingHint || takeLanguage) && (
                      <span className="dock-aside">
                        {takeLanguage && (
                          <span className="dock-hint">{takeLanguage}</span>
                        )}
                        {showPlayingHint && (
                          <span className="dock-hint" title={playingHint}>
                            {playingHint}
                          </span>
                        )}
                      </span>
                    )}
                </div>
                {active && (
                  <>
                    <div
                      className="meter indeterminate"
                      role="progressbar"
                      aria-label="Generating"
                      aria-busy="true"
                    >
                      <span />
                    </div>
                    <p className="status" aria-live="polite">
                      {jobStatus(job, elapsed)}
                    </p>
                  </>
                )}
                {result && !active && !sameVoice && (
                  <p className="previous-take-note">
                    Previously generated audio · generate again to hear your
                    current voice settings.
                  </p>
                )}
                {!active &&
                  job &&
                  (job.state === "failed" || job.state === "cancelled") && (
                    <p className="status" role="alert">
                      {jobStatus(job, elapsed)}
                    </p>
                  )}
                {active && job && job.chunks_done > 0 ? (
                  <Transport
                    src={`/api/jobs/${job.id}/preview?n=${job.chunks_done}`}
                    peaks={[]}
                    durationHint={job.audio_sec}
                    playToken={0}
                    wavUrl=""
                    mp3Url=""
                    mp3={false}
                    note={
                      Math.abs(pace - 1) >= 0.01
                        ? "The rest is still generating. Pace applies when the take finishes."
                        : "The rest is still generating."
                    }
                    downloads={false}
                    hotkey
                    follow
                    markers={job.markers || []}
                  />
                ) : (
                  result && (
                    <>
                      <Transport
                        src={result.wav_url}
                        peaks={result.peaks}
                        durationHint={result.duration_sec}
                        playToken={result.id === resultId ? playToken : 0}
                        wavUrl={`${result.wav_url}?download=1`}
                        mp3Url={result.mp3_url}
                        mp3={Boolean(studio?.mp3)}
                        note={result.language_note}
                        hotkey
                        markers={result.markers || []}
                      />
                      {result.script.trim() !== script.trim() && (
                        <div className="listen-row">
                          <p className="listen-script">{result.script}</p>
                          <button
                            className="btn btn-quiet"
                            type="button"
                            onClick={() => {
                              void navigator.clipboard
                                .writeText(result.script)
                                .then(() => {
                                  setCopied(true);
                                  window.setTimeout(
                                    () => setCopied(false),
                                    1200,
                                  );
                                });
                            }}
                          >
                            {copied ? "Copied" : "Copy"}
                          </button>
                        </div>
                      )}
                    </>
                  )
                )}
              </section>
            )}

            {!(
              result ||
              active ||
              (job && (job.state === "failed" || job.state === "cancelled"))
            ) && (
              <section className="output-placeholder" aria-label="Audio result">
                <div className="empty-wave" aria-hidden="true">
                  {[8, 16, 26, 14, 34, 46, 24, 38, 18, 30, 12, 22, 8].map(
                    (height, i) => (
                      <i key={i} style={{ height }} />
                    ),
                  )}
                </div>
                <div>
                  <h3>Your next great take starts here.</h3>
                  <p>
                    Generate speech to listen, fine-tune and export your audio.
                  </p>
                </div>
                <span className="output-format">
                  WAV {studio?.mp3 ? "/ MP3" : ""}
                </span>
              </section>
            )}
            <footer className="studio-footer">
              <span>
                <Icon name="shield" />
                Private by design. Powered by your Mac.
              </span>
              <span>Made to be heard.</span>
            </footer>
          </div>
        </main>
      </div>
      {inspectorOpen && (
        <div
          className="drawer-layer"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setInspectorOpen(false);
          }}
        >
          <aside
            className="side"
            role="dialog"
            aria-modal="true"
            aria-labelledby="models-title"
            ref={drawerRef}
          >
            <header className="drawer-heading">
              <div>
                <p className="eyebrow">ON YOUR MACHINE</p>
                <h2 id="models-title">Models & performance</h2>
              </div>
              <button
                className="icon-button"
                aria-label="Close models"
                onClick={() => setInspectorOpen(false)}
              >
                <Icon name="close" />
              </button>
            </header>
            <div className="machine-card">
              <Icon name="chip" />
              <div>
                <strong>{studio?.hardware.chip || "Your Mac"}</strong>
                <p>{studio ? machineLine(studio) : "Reading hardware…"}</p>
              </div>
            </div>
            {model && studio && (
              <ModelPanel
                models={models}
                selectedId={model.id}
                hardwareFree={studio.hardware.disk_free_bytes}
                loadedId={studio.loaded_model_id}
                onSelect={setModelId}
                onDownload={() => void onDownload("download")}
                onPause={() => void onDownload("pause")}
                onRemove={() => void onDownload("remove")}
              />
            )}
            {model && estimate && (
              <section className="panel">
                <h2>
                  {estimate.kind === "measured" ? "This Mac" : "Estimate"}
                </h2>
                {estimate.kind === "measured" && (
                  <div className="timing">
                    <div>
                      <span>Load</span>
                      <b className="num">
                        {estimate.load_sec == null || estimate.load_sec < 0.05
                          ? "In memory"
                          : formatSeconds(estimate.load_sec)}
                      </b>
                    </div>
                    <div>
                      <span>First audio</span>
                      <b className="num">
                        {formatSeconds(estimate.first_audio_sec)}
                      </b>
                    </div>
                    <div>
                      <span>This script</span>
                      <b className="num">
                        {formatSeconds(estimate.generate_sec)}
                      </b>
                    </div>
                  </div>
                )}
                <details className="fold">
                  <summary>
                    {estimate.kind === "measured"
                      ? "How this estimate works"
                      : "What this is based on"}
                  </summary>
                  <p className="sample">{estimate.detail}</p>
                  <p className="sample">
                    {estimate.sample.sentences.join(" ")}
                  </p>
                </details>
                <button
                  className="btn"
                  type="button"
                  onClick={() => void onBenchmark()}
                  disabled={
                    !model ||
                    model.download.state !== "ready" ||
                    Boolean(active) ||
                    (mode === "clone" && (!reference || !ownsVoice))
                  }
                >
                  Measure
                </button>
                {mode === "clone" && (
                  <p className="hint" style={{ padding: "8px 0 0" }}>
                    The sample uses the selected reference. The clip stays on
                    this Mac.
                  </p>
                )}
              </section>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function ModelPanel({
  models,
  selectedId,
  hardwareFree,
  loadedId,
  onSelect,
  onDownload,
  onPause,
  onRemove,
}: {
  models: ModelInfo[];
  selectedId: string;
  hardwareFree: number;
  loadedId: string | null;
  onSelect: (id: string) => void;
  onDownload: () => void;
  onPause: () => void;
  onRemove: () => void;
}) {
  const model = models.find((item) => item.id === selectedId) ?? models[0];
  const [confirmRemove, setConfirmRemove] = useState(false);
  useEffect(() => setConfirmRemove(false), [selectedId]);
  if (!model) return null;
  const download = model.download;
  const remaining = Math.max(download.expected - download.received, 0);
  return (
    <section className="panel">
      <h2>Choose an engine</h2>
      {models.map((item) => (
        <button
          key={item.id}
          type="button"
          className="model"
          title={item.quantization === "bfloat16" ? "bfloat16" : undefined}
          aria-pressed={item.id === model.id}
          onClick={() => onSelect(item.id)}
        >
          <div className="title">
            <b>
              {item.parameters} {item.family}
            </b>
            {item.id === model.id && <Icon name="check" />}
          </div>
          <span className="meta">
            {formatBytes(item.bytes)} ·{" "}
            {item.quantization === "bfloat16" ? "bf16" : item.quantization}
            {item.recommended ? " · Recommended" : ""}
            {item.download.state === "ready" ? " · Installed" : ""}
            {loadedId === item.id ? " · In memory" : ""}
          </span>
        </button>
      ))}
      {model.id === selectedId && (
        <div className="model-body">
          <p className="meta">{model.fit.detail}</p>
          <details className="fold">
            <summary>About this model</summary>
            <p className="hint" style={{ padding: "8px 0 0" }}>
              {model.summary}
            </p>
            {model.unsupported_notes.map((note) => (
              <p className="hint" style={{ padding: "6px 0 0" }} key={note}>
                {note}
              </p>
            ))}
            <p className="meta">MLX conversion of {model.official_repo}</p>
          </details>
          {download.state === "downloading" && download.percent != null && (
            <div
              className="meter"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(download.percent)}
              aria-label="Download progress"
            >
              <span style={{ width: `${download.percent}%` }} />
            </div>
          )}
          {download.state === "verifying" && (
            <p className="hint" style={{ padding: "8px 0 0" }}>
              Verifying files. This is separate from the download.
            </p>
          )}
          <p className="meta" aria-live="polite">
            {downloadLine(download, remaining)}
          </p>
          {download.error && <p className="note">{download.error}</p>}
          <p className="meta">
            {formatBytes(hardwareFree)} free. About 1.5 GB is kept in reserve.
          </p>
          <div className="row" style={{ padding: "8px 0 0" }}>
            {download.state !== "ready" &&
              download.state !== "downloading" &&
              download.state !== "verifying" && (
                <button className="btn" type="button" onClick={onDownload}>
                  {download.state === "paused" || download.state === "error"
                    ? "Resume"
                    : "Download"}
                </button>
              )}
            {download.state === "downloading" && (
              <button className="btn" type="button" onClick={onPause}>
                Pause
              </button>
            )}
            {download.state !== "missing" &&
              (confirmRemove ? (
                <>
                  <button
                    className="btn btn-quiet btn-danger"
                    type="button"
                    onClick={() => {
                      setConfirmRemove(false);
                      onRemove();
                    }}
                  >
                    Remove files
                  </button>
                  <button
                    className="btn btn-quiet"
                    type="button"
                    onClick={() => setConfirmRemove(false)}
                  >
                    Keep
                  </button>
                </>
              ) : (
                <button
                  className="btn btn-quiet"
                  type="button"
                  onClick={() => setConfirmRemove(true)}
                >
                  Remove
                </button>
              ))}
          </div>
        </div>
      )}
    </section>
  );
}

function ClonePanel({
  studio,
  dragOver,
  onDragOver,
  onDragLeave,
  onDrop,
  reference,
  transcript,
  setTranscript,
  ownsVoice,
  setOwnsVoice,
  recording,
  pendingUrl,
  pendingFile,
  fileRef,
  onRecord,
  onFile,
  onUse,
  onSelect,
  onDelete,
}: {
  studio: Studio;
  dragOver: boolean;
  onDragOver: (event: DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent) => void;
  reference: ReferenceClip | null;
  transcript: string;
  setTranscript: (value: string) => void;
  ownsVoice: boolean;
  setOwnsVoice: (value: boolean) => void;
  recording: boolean;
  pendingUrl: string;
  pendingFile: File | null;
  fileRef: RefObject<HTMLInputElement | null>;
  onRecord: () => void;
  onFile: (file: File) => void;
  onUse: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [confirmId, setConfirmId] = useState("");
  return (
    <div
      className={dragOver ? "clone-block dropping" : "clone-block"}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <ul className="guidance">
        {studio.clone_guidance.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      {!studio.ffmpeg && (
        <p className="note">
          Reading reference clips needs ffmpeg, which Reed did not find.
        </p>
      )}
      <div className="row upload-zone" style={{ paddingTop: 8 }}>
        <button className="btn" type="button" onClick={onRecord}>
          <Icon name="mic" />
          {recording ? "Stop recording" : "Record"}
        </button>
        <button
          className="btn"
          type="button"
          onClick={() => fileRef.current?.click()}
        >
          <Icon name="upload" />
          Upload clip
        </button>
        <span className="meta">or drop a clip</span>
        <input
          ref={fileRef}
          hidden
          type="file"
          accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.webm,.caf"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onFile(file);
            event.target.value = "";
          }}
        />
      </div>
      <div className="sheet-label">
        <span>
          {pendingFile
            ? `Transcript · ${pendingFile.name}`
            : "Transcript for the next clip"}
        </span>
      </div>
      <textarea
        className="transcript"
        aria-label="Transcript of the reference"
        value={transcript}
        onChange={(event) => setTranscript(event.target.value)}
        placeholder="The exact words spoken in the clip you record or upload."
      />
      {pendingUrl && (
        <div className="ref-card">
          <audio className="player" controls src={pendingUrl} />
          <div className="row" style={{ padding: "8px 0 0" }}>
            <button
              className="btn btn-primary"
              type="button"
              onClick={onUse}
              disabled={!pendingFile}
            >
              Use this clip
            </button>
          </div>
        </div>
      )}
      {studio.references.length > 0 && (
        <div className="sheet-label">
          <span>On this Mac</span>
        </div>
      )}
      {studio.references.map((clip) => (
        <article
          key={clip.id}
          className={reference?.id === clip.id ? "ref-card on" : "ref-card"}
        >
          <header>
            <button
              className="link"
              type="button"
              onClick={() => onSelect(clip.id)}
            >
              {clip.original_name}
            </button>
            {confirmId === clip.id ? (
              <>
                <button
                  className="btn btn-quiet btn-danger"
                  type="button"
                  onClick={() => {
                    setConfirmId("");
                    onDelete(clip.id);
                  }}
                >
                  Delete
                </button>
                <button
                  className="btn btn-quiet"
                  type="button"
                  onClick={() => setConfirmId("")}
                >
                  Keep
                </button>
              </>
            ) : (
              <button
                className="btn btn-quiet"
                type="button"
                onClick={() => setConfirmId(clip.id)}
              >
                Delete
              </button>
            )}
          </header>
          <p title={clip.transcript}>{clip.transcript}</p>
          <Transport
            src={clip.audio_url}
            peaks={clip.peaks}
            durationHint={clip.duration_sec}
            playToken={0}
            wavUrl={clip.audio_url}
            mp3Url=""
            mp3={false}
            note=""
            downloads={false}
          />
          <p className="meta">
            {formatSeconds(clip.duration_sec)}
            {clip.warning ? ` · ${clip.warning}` : ""}
            {reference?.id === clip.id ? " · Selected" : ""}
          </p>
        </article>
      ))}
      <label className="check">
        <input
          type="checkbox"
          checked={ownsVoice}
          onChange={(event) => setOwnsVoice(event.target.checked)}
        />
        <span>I own this voice or have permission to use it.</span>
      </label>
    </div>
  );
}

function HistoryRow({
  item,
  now,
  hint,
  selected,
  expanded,
  confirming,
  onSelect,
  onReuse,
  onAsk,
  onCancel,
  onDelete,
}: {
  item: HistoryItem;
  now: number;
  hint: string;
  selected: boolean;
  expanded: boolean;
  confirming: boolean;
  onSelect: () => void;
  onReuse: () => void;
  onAsk: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={selected ? "take-wrap on" : "take-wrap"}
      aria-current={selected ? "true" : undefined}
    >
      <button
        className="take"
        type="button"
        title={hint || undefined}
        onClick={onSelect}
        onDoubleClick={onReuse}
      >
        <span className="take-script">{item.script}</span>
        {hint && <span className="take-hint">{hint}</span>}
        <span className="take-meta">
          {labelMode(item.mode)} · {formatSeconds(item.duration_sec)} ·{" "}
          {relativeTime(item.created_at, now)}
          {item.language_note ? " · experimental" : ""}
        </span>
      </button>
      {expanded && (
        <div className="take-actions">
          <button className="btn btn-quiet" type="button" onClick={onReuse}>
            Reuse
          </button>
          {confirming ? (
            <>
              <button
                className="btn btn-quiet btn-danger"
                type="button"
                onClick={onDelete}
              >
                Delete
              </button>
              <button
                className="btn btn-quiet"
                type="button"
                onClick={onCancel}
              >
                Keep
              </button>
            </>
          ) : (
            <button className="btn btn-quiet" type="button" onClick={onAsk}>
              Delete
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Transport({
  src,
  peaks,
  durationHint,
  playToken,
  wavUrl,
  mp3Url,
  mp3,
  note,
  downloads = true,
  hotkey = false,
  follow = false,
  markers = [],
}: {
  src: string;
  peaks: number[];
  durationHint: number;
  playToken: number;
  wavUrl: string;
  mp3Url: string;
  mp3: boolean;
  note: string;
  downloads?: boolean;
  hotkey?: boolean;
  follow?: boolean;
  markers?: { label: string; start: number }[];
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const loadedSrc = useRef("");
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(durationHint || 0);
  const [rate, setRate] = useState(1);

  useEffect(() => {
    if (follow) return;
    setPlaying(false);
    setTime(0);
    setDuration(durationHint || 0);
  }, [src, durationHint, follow]);

  useEffect(() => {
    if (!follow) return;
    const audio = audioRef.current;
    if (!audio || !src) return;
    if (!audio.paused && loadedSrc.current) return;
    if (loadedSrc.current === src) return;
    loadedSrc.current = src;
    audio.src = src;
    audio.load();
    setPlaying(false);
    setTime(0);
  }, [follow, src]);

  useEffect(() => {
    if (!playToken) return;
    const audio = audioRef.current;
    if (!audio) return;
    audio.playbackRate = rate;
    void audio.play().catch(() => setPlaying(false));
  }, [playToken, src, rate]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = rate;
  }, [rate]);

  useEffect(() => {
    if (!hotkey) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== " ") return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        target.closest("textarea, input, select, button, a")
      )
        return;
      event.preventDefault();
      const audio = audioRef.current;
      if (!audio) return;
      if (audio.paused) void audio.play();
      else audio.pause();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hotkey]);

  const progress = duration > 0 ? time / duration : 0;

  function seek(ratio: number) {
    const audio = audioRef.current;
    if (!audio || !Number.isFinite(audio.duration)) return;
    audio.currentTime = Math.min(1, Math.max(0, ratio)) * audio.duration;
  }

  return (
    <div>
      <audio
        ref={audioRef}
        src={follow ? undefined : src}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
        onLoadedMetadata={(event) =>
          setDuration(event.currentTarget.duration || durationHint)
        }
        onEnded={() => setPlaying(false)}
      />
      <div className="transport">
        <button
          className="play"
          type="button"
          aria-label={playing ? "Pause" : "Play"}
          aria-keyshortcuts={hotkey ? "Space" : undefined}
          title={hotkey ? "Space" : undefined}
          onClick={() => {
            const audio = audioRef.current;
            if (!audio) return;
            if (audio.paused) void audio.play();
            else audio.pause();
          }}
        >
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <div className="wave-wrap">
          <Waveform peaks={peaks} progress={progress} onSeek={seek} />
          <input
            className="scrub"
            type="range"
            min={0}
            max={1000}
            value={Math.round(progress * 1000)}
            aria-label="Playback position"
            onChange={(event) => seek(Number(event.target.value) / 1000)}
          />
        </div>
      </div>
      <div className="transport-meta">
        <span className="time num">
          {clockTime(time)} / {clockTime(duration || durationHint)}
        </span>
        <button
          className="btn btn-quiet"
          type="button"
          aria-label={`Playback speed ${rate} times`}
          onClick={() =>
            setRate((current) =>
              current === 1 ? 1.25 : current === 1.25 ? 1.5 : 1,
            )
          }
        >
          {rate === 1 ? "1×" : `${rate}×`}
        </button>
        {downloads && (
          <a className="btn btn-quiet" href={wavUrl}>
            WAV
          </a>
        )}
        {downloads &&
          (mp3 ? (
            <a className="btn btn-quiet" href={mp3Url}>
              MP3
            </a>
          ) : (
            <span className="meta">No MP3</span>
          ))}
      </div>
      {markers.length > 1 && (
        <div className="markers">
          {markers.map((marker) => (
            <button
              key={`${marker.label}-${marker.start}`}
              className="btn btn-quiet"
              type="button"
              onClick={() => {
                const audio = audioRef.current;
                if (!audio || !Number.isFinite(audio.duration)) return;
                audio.currentTime = Math.min(
                  audio.duration,
                  Math.max(0, marker.start),
                );
                void audio.play();
              }}
            >
              {marker.label} · {clockTime(marker.start)}
            </button>
          ))}
        </div>
      )}
      {note && (
        <p className="note" style={{ margin: "10px 0 0" }}>
          {note}
        </p>
      )}
    </div>
  );
}

function Waveform({
  peaks,
  progress = 0,
  onSeek,
}: {
  peaks: number[];
  progress?: number;
  onSeek?: (ratio: number) => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const draw = () => {
      const context = canvas.getContext("2d");
      if (!context) return;
      const width = canvas.clientWidth || 240;
      const height = 36;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      const count = Math.max(peaks.length, 1);
      const gap = width / count;
      peaks.forEach((peak, index) => {
        const x = index * gap + gap / 2;
        const bar = Math.max(2, peak * (height - 4));
        context.strokeStyle =
          (index + 0.5) / count <= progress ? "#4e896b" : "#94a89c";
        context.lineWidth = Math.max(1.25, gap * 0.55);
        context.lineCap = "round";
        context.beginPath();
        context.moveTo(x, height / 2 - bar / 2);
        context.lineTo(x, height / 2 + bar / 2);
        context.stroke();
      });
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [peaks, progress]);

  return (
    <canvas
      ref={ref}
      className="wave"
      aria-hidden="true"
      onClick={
        onSeek
          ? (event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              onSeek((event.clientX - rect.left) / rect.width);
            }
          : undefined
      }
    />
  );
}

function ModeIcon({ id }: { id: Mode }) {
  if (id === "design") {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <path
          d="M3 13.2 3.4 11 10.2 4.2l2.2 2.2L5.6 13.2H3Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinejoin="round"
        />
        <path
          d="M9.2 5.2 11 3.4a1.2 1.2 0 0 1 1.7 0l.5.5a1.2 1.2 0 0 1 0 1.7L11.4 7.4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
        />
      </svg>
    );
  }
  if (id === "clone") {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <path
          d="M2 8h1.2M4.6 5.2v5.6M7 3.5v9M9.4 6v4M12 4.4v7.2M14 8h.2"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <circle
        cx="8"
        cy="5.2"
        r="2.1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <path
        d="M3.6 13.2c.6-2.3 2.2-3.4 4.4-3.4s3.8 1.1 4.4 3.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M3.2 1.8v8.4L10 6 3.2 1.8Z" fill="currentColor" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M3 2h2.2v8H3zM6.8 2H9v8H6.8z" fill="currentColor" />
    </svg>
  );
}

const DRAFT_KEY = "reed-draft-v1";

type Draft = {
  mode?: Mode;
  modelId?: string;
  script?: string;
  language?: string;
  description?: string;
  appliedDescription?: string;
  delivery?: string;
  speaker?: string;
  temperature?: number;
  pace?: number;
};

function loadDraft(): Draft {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return {};
    const data = JSON.parse(raw) as Draft;
    if (
      data.mode &&
      data.mode !== "design" &&
      data.mode !== "clone" &&
      data.mode !== "preset"
    ) {
      data.mode = "design";
    }
    if (
      typeof data.temperature !== "number" ||
      data.temperature < 0.1 ||
      data.temperature > 1.5
    ) {
      data.temperature = 0.9;
    }
    if (typeof data.pace !== "number" || data.pace < 0.8 || data.pace > 1.25) {
      data.pace = 1;
    }
    return data;
  } catch {
    return {};
  }
}

function preferredModel(studio: Studio, mode: Mode) {
  const models = studio.models.filter((model) => model.task === mode);
  return (models.find((model) => model.recommended) ?? models[0])?.id ?? "";
}

function liveElapsed(job: Job | null, seenAt: number | null, now: number) {
  if (!job) return 0;
  if (!["queued", "loading", "generating"].includes(job.state))
    return job.elapsed_sec || 0;
  const extra = seenAt ? Math.max(0, (now - seenAt) / 1000) : 0;
  return (job.elapsed_sec || 0) + extra;
}

function jobStatus(job: Job | null, elapsed: number) {
  if (!job) return "";
  if (job.state === "queued") return "Queued.";
  if (job.state === "loading")
    return `Loading the checkpoint. ${formatSeconds(elapsed)} elapsed. Cancel applies before synthesis.`;
  if (job.state === "generating") {
    const chunk =
      job.chunks_total > 1
        ? ` Part ${job.chunks_done} of ${job.chunks_total}.`
        : "";
    const ready =
      job.chunks_done > 0
        ? " The start can play while the rest generates."
        : "";
    return `Generating. ${formatSeconds(elapsed)} elapsed. ${formatSeconds(job.audio_sec)} of audio so far.${chunk}${ready} This is not a completion percentage.`;
  }
  if (job.state === "failed") return job.error || "Generation failed.";
  if (job.state === "cancelled") return job.error || "Cancelled.";
  if (job.state === "completed") {
    const bits = [
      job.load_sec != null ? `loaded in ${formatSeconds(job.load_sec)}` : "",
      job.first_audio_sec != null
        ? `first audio ${formatSeconds(job.first_audio_sec)}`
        : "",
      job.generate_sec != null
        ? `generated in ${formatSeconds(job.generate_sec)}`
        : "",
    ].filter(Boolean);
    return bits.join(" · ");
  }
  return "";
}

function downloadLine(download: DownloadState, remaining: number) {
  if (download.state === "ready") return "Ready on disk.";
  if (download.state === "missing") return "Not installed.";
  if (download.state === "paused")
    return `Paused at ${formatBytes(download.received)}. Resume continues the same files.`;
  if (download.state === "verifying") return "Verifying the checkpoint.";
  if (download.state === "error")
    return "Download failed. The checkpoint is not marked ready.";
  if (download.state === "downloading") {
    const speed = download.speed
      ? `${formatBytes(download.speed)}/s`
      : "measuring speed";
    const eta = download.eta
      ? ` · about ${formatSeconds(download.eta)} left`
      : "";
    const expected = download.expected
      ? ` of ${formatBytes(download.expected)}`
      : "";
    return `${formatBytes(download.received)}${expected} · ${formatBytes(remaining)} remaining · ${speed}${eta}`;
  }
  return download.state;
}

function voiceHint(
  item: HistoryItem,
  references: { id: string; original_name: string }[],
) {
  if (item.mode === "design")
    return item.voice_description.replace(/\s+/g, " ").trim();
  if (item.mode === "preset" && item.speaker)
    return item.speaker.replaceAll("_", " ");
  if (item.mode === "clone" && item.reference_id) {
    return (
      references.find((clip) => clip.id === item.reference_id)?.original_name ??
      ""
    );
  }
  return "";
}

function labelMode(mode: Mode) {
  if (mode === "design") return "Designed";
  if (mode === "clone") return "Cloned";
  return "Preset";
}

function machineLine(studio: Studio) {
  const hardware = studio.hardware;
  return [
    hardware.chip || "Chip unknown",
    hardware.model_identifier,
    hardware.memory_label || formatBytes(hardware.unified_memory_bytes),
    [hardware.macos_name, hardware.macos_version].filter(Boolean).join(" "),
    `${formatBytes(hardware.disk_free_bytes)} free`,
  ]
    .filter(Boolean)
    .join("  ·  ");
}

function formatBytes(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)} GB`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(0)} MB`;
  if (abs >= 1000) return `${(value / 1000).toFixed(0)} KB`;
  return `${Math.round(value)} B`;
}

function relativeTime(value: string, now: number) {
  const then = Date.parse(value);
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, (now - then) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 90 * 60) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function clockTime(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "0:00";
  const whole = Math.max(0, Math.floor(value));
  const minutes = Math.floor(whole / 60);
  const seconds = whole % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function formatSeconds(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  if (value < 30) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${minutes}m ${seconds}s`;
}

function ReedMark() {
  return (
    <svg
      width="30"
      height="30"
      viewBox="0 0 30 30"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M6 19V11M12 24V6M18 21V9M24 17V13"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

type IconName =
  | "arrow"
  | "chevron"
  | "check"
  | "close"
  | "plus"
  | "wave"
  | "shield"
  | "chip"
  | "sliders"
  | "text"
  | "moon"
  | "sun"
  | "menu"
  | "mic"
  | "upload";
function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    arrow: <path d="M4 10h12m-5-5 5 5-5 5" />,
    chevron: <path d="m7 4 6 6-6 6" />,
    check: <path d="m4 10 4 4 8-8" />,
    close: <path d="m5 5 10 10M15 5 5 15" />,
    plus: <path d="M10 4v12M4 10h12" />,
    wave: <path d="M3 8v4m3-7v10m4-13v16m4-12v8m3-5v2" />,
    shield: (
      <>
        <path d="M10 2 3 5v5c0 4 7 8 7 8s7-4 7-8V5l-7-3Z" />
        <path d="m7 10 2 2 4-4" />
      </>
    ),
    chip: (
      <>
        <rect x="5" y="5" width="10" height="10" rx="2" />
        <path d="M8 2v3m4-3v3M8 15v3m4-3v3M2 8h3m-3 4h3m10-4h3m-3 4h3M8 8h4v4H8z" />
      </>
    ),
    sliders: (
      <>
        <path d="M3 5h5m4 0h5M3 15h9m4 0h1M3 10h1m4 0h9" />
        <circle cx="10" cy="5" r="2" />
        <circle cx="6" cy="10" r="2" />
        <circle cx="14" cy="15" r="2" />
      </>
    ),
    text: <path d="M4 4h12M10 4v12M7 16h6" />,
    moon: <path d="M17 11a7 7 0 0 1-8-8 7.5 7.5 0 1 0 8 8Z" />,
    sun: (
      <>
        <circle cx="10" cy="10" r="3" />
        <path d="M10 1v2m0 14v2M1 10h2m14 0h2M4 4l1 1m10 10 1 1M4 16l1-1M15 5l1-1" />
      </>
    ),
    menu: <path d="M3 5h14M3 10h14M3 15h14" />,
    mic: (
      <>
        <rect x="7" y="2" width="6" height="10" rx="3" />
        <path d="M4 9a6 6 0 0 0 12 0M10 15v3m-3 0h6" />
      </>
    ),
    upload: <path d="M10 13V2m-4 4 4-4 4 4M3 12v5h14v-5" />,
  };
  return (
    <svg
      className="icon"
      width="18"
      height="18"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
