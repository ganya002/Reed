export type Mode = "design" | "clone" | "preset";

export type Fit = { level: string; label: string; detail: string };

export type DownloadState = {
  state: string;
  received: number;
  expected: number;
  percent: number | null;
  speed: number | null;
  eta: number | null;
  error: string;
};

export type ModelInfo = {
  id: string;
  task: Mode;
  family: string;
  parameters: string;
  quantization: string;
  official_repo: string;
  summary: string;
  supports_instruct: boolean;
  supports_speaker: boolean;
  supports_reference: boolean;
  bytes: number;
  bytes_source: string;
  bytes_fetched_at: string | null;
  installed: boolean;
  recommended: boolean;
  fit: Fit;
  unsupported_notes: string[];
  download: DownloadState;
};

export type Speaker = { id: string; description: string; native: string };
export type Language = { id: string; label: string; experimental: boolean; note: string };
export type Example = { id: string; label: string; text: string };
export type ReferenceClip = {
  id: string;
  original_name: string;
  transcript: string;
  duration_sec: number;
  warning: string;
  peaks: number[];
  audio_url: string;
};
export type SavedVoice = {
  id: string;
  name: string;
  mode: Mode;
  language: string;
  voice_description: string;
  speaker: string;
  reference_id: string;
  pace: number;
};
export type SayAs = { id: string; written: string; spoken: string };
export type HistoryItem = {
  id: string;
  created_at: string;
  mode: Mode;
  script: string;
  language: string;
  language_note: string;
  voice_description: string;
  speaker: string;
  instruct: string;
  model_id: string;
  reference_id: string;
  duration_sec: number;
  peaks: number[];
  markers: { label: string; start: number }[];
  wav_url: string;
  mp3_url: string;
};
export type Job = {
  id: string;
  state: string;
  mode: Mode;
  model_id: string;
  script: string;
  error: string;
  audio_sec: number;
  elapsed_sec: number;
  load_sec: number | null;
  first_audio_sec: number | null;
  generate_sec: number | null;
  chunks_total: number;
  chunks_done: number;
  markers: { label: string; start: number }[];
  progress: null;
  history_id: string | null;
  language_note: string;
  started_at: number | null;
};
export type Estimate = {
  kind: "rough" | "measured";
  detail: string;
  load_sec: number | null;
  first_audio_sec: number | null;
  generate_sec: number | null;
  sample: { id: string; text: string; sentences: string[] };
};
export type Hardware = {
  chip: string;
  model_name: string;
  model_identifier: string;
  unified_memory_bytes: number;
  memory_label: string;
  macos_name: string;
  macos_version: string;
  macos_build: string;
  disk_free_bytes: number;
  disk_total_bytes: number;
  apple_silicon: boolean;
};
export type Studio = {
  hardware: Hardware;
  models: ModelInfo[];
  languages: Language[];
  speakers: Speaker[];
  examples: Example[];
  sample: { id: string; text: string; sentences: string[] };
  history: HistoryItem[];
  references: ReferenceClip[];
  estimates: Record<string, Estimate>;
  jobs: Job[];
  loaded_model_id: string | null;
  mp3: boolean;
  ffmpeg: boolean;
  preset_note: string;
  clone_guidance: string[];
  parts: number;
  pauses: number;
  cast: string[];
  spoken: string;
  plan_error: string;
  voices: SavedVoice[];
  say_as: SayAs[];
};

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* ignore */
  }
  return `The local server returned ${response.status}.`;
}

export async function getStudio(script: string, mode = "design", speaker = ""): Promise<Studio> {
  const params = new URLSearchParams();
  if (script) params.set("script", script);
  if (mode) params.set("mode", mode);
  if (speaker) params.set("speaker", speaker);
  const query = params.size ? `?${params.toString()}` : "";
  const response = await fetch(`/api/studio${query}`);
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function postJson(url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export { readError };
