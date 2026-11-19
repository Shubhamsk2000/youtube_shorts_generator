# yt_video_gen: Shorts Studio

Long YouTube video (or your own file) → vertical 9:16 shorts with face tracking and word-by-word captions,
reviewed and exported from one web app. Everything runs locally on CPU; an AI model (Gemini free tier by default)
picks the moments.

## Setup (once)

```bash
sudo apt install -y ffmpeg
python3 -m venv .venv
PIP_CACHE_DIR=.cache/pip .venv/bin/pip install -e ".[dev]"
cp .env.example .env                 # add GEMINI_API_KEY
cd web && npm install --cache ../.cache/npm && npm run build && cd ..
.venv/bin/shorts doctor
```

## Studio (the web app)

```bash
.venv/bin/studio serve               # http://127.0.0.1:8000
```

Deploying to AWS and updating it after code changes: **[docs/DEPLOY.md](docs/DEPLOY.md)**.

- **Projects**: paste a link or upload a file. Optionally **tell the AI what to look for** ("every time they talk
  about money"): *These first* picks those moments first plus the other strong ones, *Only these* picks just them
  (if nothing matches you still get the best moments). Matching clips get a **Focus** badge and filter. The note can
  be changed on the project page and the moments picked again (the transcript is reused). Totals across all projects (clips, to review, approved, exported,
  disk), search, filter (processing / to review / ready / failed) and sort.
- **Project page**: weighted overall progress with elapsed time and the current step's time left, **Stop** (finished
  steps are kept, Resume continues), Retry, **Re-render** with the current settings, delete (optionally with the
  exported files). Clips can be filtered, sorted (score, hook, most replayed, length, order in the video), approved in
  bulk, and edited: title, on-screen hook, banner on/off, description and hashtags (on-screen changes re-render just
  that preview). Copy the upload text, export HD one by one or all approved, cancel an export, download every HD clip
  as one `.zip`. Keyboard on a focused clip: <kbd>A</kbd> approve, <kbd>X</kbd> reject, <kbd>E</kbd> edit,
  <kbd>Enter</kbd> open the editor.
- **Clip editor** (click a preview): the full 16:9 clip with the window that becomes the short drawn on top, next to
  a live 9:16 preview. Drag the window to move it, drag its edges to make it wider (9:16 fills the screen; 3:4,
  13:16, 1:1 ... up to the whole 16:9 frame are shown full-width over a blurred copy of the video). Every change is a
  keyframe: the window moves smoothly between keyframes and jumps at camera cuts; shots without keyframes keep the
  automatic framing. A wider window can keep following faces. Also: hook text, banner, title, approve/reject,
  undo/redo, Shorts safe zones, and the pauses that pacing removes on the timeline. Saving re-renders that clip.
  Keyframes live in `framing/<clip>.json` (the face analysis is never changed: "Reset" removes keyframes).
- **Settings**: moment picking, captions, hook banner, pacing, layout defaults, export quality, download. Saved to
  `configs/shorts.local.yaml` (git-ignored) on top of `configs/shorts.yaml`; every value is validated before it is
  saved, and each can be reset to the `shorts.yaml` default. **Channel card** (tab): name, handle, picture and theme
  of the tweet-card background.
- **System**: setup checks, the worker queue, AI model status, and disk usage per project (delete from there).

Heavy work runs through one background queue (one task at a time: the pipeline is CPU and RAM bound); the nav shows
what is running. `shorts serve` starts the same Studio.

## Headless

```bash
.venv/bin/shorts make <url>          # the whole pipeline
.venv/bin/shorts export <video_id>   # approved clips (--rank 1 --rank 3, or --all)
```

The AI picks moments (find pass + editor pass, YouTube "Most replayed" data when available), 9:16 reframing with
face tracking (facecam-over-gameplay gets a split layout), tightened pauses, word-by-word captions, an on-screen hook,
loudness at −14 LUFS. Finished files: `data/shorts/exports/<video>/` (each `.mp4` with a `.txt` holding the title,
description and hashtags).

Long videos and live streams (up to 36 h): when the whole video wouldn't fit comfortably (`download.mode: auto`),
only its audio is downloaded for transcription and picking, then only the picked clips' video, each into its own
file (`workspace/<id>/sources/`). Smaller videos are downloaded whole, the clips are cut out, and the full download
is deleted. Whisper saves each 30 min chunk as it goes, so a stopped job resumes where it was.

## AI providers

Every AI call goes through `src/common/llm.py`; how each provider is called lives in `src/common/llm_providers.py`
(Gemini, or any OpenAI-compatible API such as NVIDIA NIM, OpenRouter, Groq, Ollama). Choose the provider per task in
**`configs/llm.yaml`**: no code changes. The model picker in Studio saves its choice to `configs/llm.local.yaml`.

## Layout

```
src/common/       shared: AI doorway (llm.py) + providers (llm_providers.py), ffmpeg helpers, job lock, runner, paths
src/studio/       the Studio server: mounts /api/shorts, queue status, serves web/studio
src/shorts/       the app: stages/ (download, transcribe, select, sources, reframe, captions, render), server.py (API),
                  settings.py (what the Settings page may change), card.py (tweet card), cli.py
configs/          shorts.yaml, llm.yaml (+ *.local.yaml written by Studio, git-ignored)
web/studio/       the React app: src/kit (components, theme, polling, toasts), src/shorts, src/app (shell, settings, system)
data/             all media (git-ignored): shorts/{uploads,workspace,exports}
tests/            shorts/, common/
.cache/           models, pip/npm caches, Hugging Face (git-ignored)
```

UI development with hot reload: `studio serve`, then `cd web && npm run dev` (http://localhost:5173, proxies `/api`).
