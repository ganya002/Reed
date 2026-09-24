import type { Page } from "@playwright/test";
import type { Studio, Job, ModelInfo } from "../src/api";

export const description =
  "A warm, expressive British voice with a gentle texture and a playful delivery.";
const sample = {
  id: "sample",
  text: "Some stories are better heard. Give these words a little character. Let your imagination do the talking.",
  sentences: [
    "Some stories are better heard.",
    "Give these words a little character.",
    "Let your imagination do the talking.",
  ],
};
const makeModel = (task: "design" | "clone" | "preset"): ModelInfo => ({
  id: `test-${task}`,
  task,
  family:
    task === "design"
      ? "VoiceDesign"
      : task === "clone"
        ? "Base"
        : "CustomVoice",
  parameters: task === "design" ? "1.7B" : "0.6B",
  quantization: "8-bit",
  official_repo: "Qwen/test",
  summary: "Test fixture; no inference is performed.",
  supports_instruct: task === "design",
  supports_speaker: task === "preset",
  supports_reference: task === "clone",
  bytes: 3100000000,
  bytes_source: "test",
  bytes_fetched_at: null,
  installed: true,
  recommended: true,
  fit: {
    level: "comfortable",
    label: "Recommended",
    detail: "A comfortable fit for your Mac.",
  },
  unsupported_notes: [],
  download: {
    state: "ready",
    received: 3100000000,
    expected: 3100000000,
    percent: 100,
    speed: null,
    eta: null,
    error: "",
  },
});
export function studioFixture(): Studio {
  return {
    hardware: {
      chip: "Apple M4",
      model_name: "MacBook Air",
      model_identifier: "Mac16,12",
      unified_memory_bytes: 16 * 1024 ** 3,
      memory_label: "16 GB",
      macos_name: "macOS",
      macos_version: "27.0",
      macos_build: "test",
      disk_free_bytes: 82 * 1024 ** 3,
      disk_total_bytes: 256 * 1024 ** 3,
      apple_silicon: true,
    },
    models: ["design", "clone", "preset"].map((task) =>
      makeModel(task as "design" | "clone" | "preset"),
    ),
    languages: [
      { id: "english", label: "English", experimental: false, note: "" },
      {
        id: "norwegian",
        label: "Norwegian",
        experimental: true,
        note: "Norwegian is experimental. Pronunciation and dialect may vary.",
      },
    ],
    speakers: [
      {
        id: "Ryan",
        description: "Clear, warm and natural.",
        native: "English",
      },
      {
        id: "Serena",
        description: "Gentle and expressive.",
        native: "Chinese",
      },
    ],
    examples: [
      { id: "warm", label: "Warm narrator", text: description },
      {
        id: "bright",
        label: "Bright & playful",
        text: "A bright young adult voice, playful and energetic, with a natural American accent.",
      },
      {
        id: "story",
        label: "Storyteller",
        text: "A rich, measured older voice with a gentle rasp and a warm Scottish accent.",
      },
    ],
    sample,
    history: [],
    references: [],
    estimates: {
      "test-design": {
        kind: "measured",
        detail: "Test timing fixture.",
        load_sec: 0,
        first_audio_sec: 3.2,
        generate_sec: 8.6,
        sample,
      },
    },
    jobs: [],
    loaded_model_id: "test-design",
    mp3: true,
    ffmpeg: true,
    preset_note: "Choose a voice to get started.",
    clone_guidance: [
      "Use a clean clip with a single speaker.",
      "Keep the reference between 1.2 and 25 seconds.",
    ],
    parts: 1,
    pauses: 0,
    cast: [],
    spoken: "",
    plan_error: "",
    voices: [],
    say_as: [],
  };
}
export async function mockStudio(page: Page, data = studioFixture()) {
  const requests: Record<string, unknown>[] = [];
  await page.addInitScript(() => {
    class LocalEventSource {
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      constructor() {
        (window as any).__events = this;
        setTimeout(() => this.onopen?.(), 0);
      }
      close() {}
    }
    (window as any).EventSource = LocalEventSource;
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/studio") return route.fulfill({ json: data });
    if (url.pathname === "/api/generate") {
      const body = route.request().postDataJSON();
      requests.push(body);
      return route.fulfill({
        json: {
          id: "job",
          state: "queued",
          mode: body.mode,
          model_id: body.model_id,
          script: body.script,
          error: "",
          audio_sec: 0,
          elapsed_sec: 0,
          load_sec: null,
          first_audio_sec: null,
          generate_sec: null,
          chunks_total: 1,
          chunks_done: 0,
          markers: [],
          progress: null,
          history_id: null,
          language_note: "",
          started_at: null,
        } satisfies Job,
      });
    }
    if (url.pathname === "/api/voices" && route.request().method() === "POST") {
      data.voices.push({ id: "saved", ...route.request().postDataJSON() });
      return route.fulfill({ json: data.voices.at(-1) });
    }
    return route.fulfill({ json: {} });
  });
  return { data, requests };
}
