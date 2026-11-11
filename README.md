# Clipwise (shorts)

Long YouTube video in → vertical 9:16 shorts with word-by-word captions out. Runs locally on CPU.

## Setup

```bash
sudo apt install -y ffmpeg
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # add GEMINI_API_KEY (needed from milestone 2)
cd web && npm install && npm run build && cd ..
.venv/bin/shorts doctor
```

## Use

```bash
.venv/bin/shorts serve           # web UI at http://127.0.0.1:8000
.venv/bin/shorts make <url>      # or headless, from the terminal
```

Review the quick previews, then click **Export HD** on a clip (or **Export approved**). Finished
1080×1920 files land in `exports/<video>/` with a `.txt` next to each (title, description, hashtags,
hook text). Headless: `.venv/bin/shorts export <video_id>` (approved), `--rank 1 --rank 3`, or `--all`.

What a short gets: Gemini picks moments (two passes: find, then an editor pass that compares all picks,
tightens the opening line and writes the hook text/title/hashtags; YouTube "Most replayed" data is used
when available), 9:16 reframing (face tracking; facecam-over-gameplay videos get a split layout: cam on top,
gameplay below), tightened pauses, word-by-word captions, an on-screen hook, and loudness normalised to −14 LUFS.

Debug the framing of one clip: `.venv/bin/shorts debug-reframe <video_id> <rank>` (16:9 video with the
detected shots, faces and cam boxes).

UI development with hot reload: run `shorts serve` and, in `web/`, `npm run dev` (http://localhost:5173, proxies `/api`).

## Layout

- `src/shorts/stages/`: one module per pipeline stage, each writes a JSON/media file into `workspace/<video_id>/`
- `src/shorts/pipeline.py`: stage order, shared by CLI and server
- `src/shorts/server.py`: FastAPI API, single-worker job queue, serves `web/dist`
- `web/`: React + Vite + Tailwind UI
- `config.yaml`: all tunables

Finished stages are cached on disk; rerunning (or **Retry** in the UI) resumes where it stopped.
