# Reed

Reed is a local voice studio for Apple silicon. It designs, clones, and plays speech on your Mac. Reference clips and generated audio stay on this machine. The only network use is downloading model weights from Hugging Face.

## Run

On an Apple silicon Mac, with [uv](https://docs.astral.sh/uv/), Node.js, and ffmpeg:

```bash
./run
```

Then open http://127.0.0.1:8733

The server binds to `127.0.0.1` only. `./run` creates a project virtualenv with Python 3.12, installs the pinned stack from `uv.lock`, builds the studio, and starts it.

## What it uses

Inference is [MLX Audio](https://github.com/Blaizzy/mlx-audio) 0.4.4. Reed does not use CUDA or FlashAttention.

| Mode | Checkpoint | API |
| --- | --- | --- |
| Design a voice | `mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit` or `-bf16` | `generate_voice_design` |
| Clone a voice | `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit`, plus 1.7B 8-bit and bfloat16 | `generate` with `ref_audio` and `ref_text` |
| Preset voice | `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`, plus 1.7B 8-bit and bfloat16 | `generate_custom_voice` |

These are MLX conversions of the official `Qwen/Qwen3-TTS-12Hz-*` checkpoints. The 8-bit VoiceDesign, Base, and CustomVoice models are the practical defaults on a 16 GB Mac. bfloat16 is available when you want more precision and have the disk and memory for it.

Official languages are Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, and Italian. Norwegian is offered as an experiment: these checkpoints have no Norwegian language token, and Reed says so in the studio. It does not promise pronunciation or dialect.

The 0.6B CustomVoice checkpoint does not support delivery instructions. The 1.7B CustomVoice checkpoint does. Base clone checkpoints follow the reference clip and do not take a written voice description.

Preset speakers are the published CustomVoice voices: Vivian, Serena, Uncle_Fu, Dylan, Eric, Ryan, Aiden, Ono_Anna, and Sohee. They are not voices you designed or cloned.

## Files

Models, history, and references live in `data/` inside this project. Deleting a take or a reference removes the local audio. A checkpoint is marked ready only after its weights, speech tokenizer, and config are present and the file sizes match the repository manifest.

## Develop

```bash
uv sync --python 3.12 --group dev
uv run pytest
npm --prefix frontend run dev
uv run --python 3.12 uvicorn --app-dir backend reed.main:app --host 127.0.0.1 --port 8733
```
