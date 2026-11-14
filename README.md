# yt_video_gen

**Studio**: one web app for vertical (9:16) short videos, with two tools behind it that share one Python
environment, one model cache, one job queue and one UI:

| App | Command | Input → output |
|---|---|---|
| **Clipwise (shorts)** | `shorts` | Long YouTube video → face-tracked shorts with word-by-word captions |
| **Anime edits** | `anime` | Anime episode → tweet-card shorts (blurred background + your "tweet" + the scene): single scenes, 40-60 s montages, or AMVs cut to the beat of your songs |

Everything runs locally on CPU; Gemini (free tier) picks the moments.

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
.venv/bin/studio serve               # http://127.0.0.1:8000: Home, Shorts, Anime, Music, Channel
```

Deploying to AWS and updating it after code changes: **[docs/DEPLOY.md](docs/DEPLOY.md)**.

Both tools run their heavy work through ONE shared queue (one job at a time: the pipelines are CPU and RAM
bound); the nav shows what is running. `shorts serve` and `anime serve` start the same Studio.

## Shorts (long video → shorts)

```bash
.venv/bin/shorts make <url>          # headless (or use Studio → Shorts)
.venv/bin/shorts export <video_id>   # approved clips (--rank 1 --rank 3, or --all)
```

Gemini picks moments (find pass + editor pass, YouTube "Most replayed" data when available), 9:16 reframing with
face tracking (facecam-over-gameplay gets a split layout), tightened pauses, word-by-word captions, an on-screen hook,
loudness at −14 LUFS. Finished files: `data/shorts/exports/<video>/`. Settings: `configs/shorts.yaml`.

## Anime edits (episode → edits)

In Studio → Anime: drop an episode (or put it in `data/anime/episodes/`), choose single scenes, montage or
song edit, how many, audio (Auto = English dub if the file has one), press **Make shorts**. Then watch the previews, edit the hook text/title
(re-renders), approve, and **Export**. **Channel card** sets your name, handle, picture and theme.

Headless:

```bash
.venv/bin/anime card "nobody was ready for this"   # check your card
.venv/bin/anime make data/anime/episodes/ep01.mkv  # previews in data/anime/workspace/<episode>/previews/
.venv/bin/anime make ep01.mkv --style montage -n 3  # 40-60 s multi-beat hype edits instead of single scenes
.venv/bin/anime make ep01.mkv --style song          # AMVs cut to songs from data/anime/music/
.venv/bin/anime list ep01.mkv                       # the picks: score, hook text, timestamps
.venv/bin/anime export ep01.mkv --rank 1 --rank 2   # 1080x1920 + .txt (title/description/hashtags)
```

How a short is made:

1. **ingest**: picks the audio track (English dub first, else Japanese). English audio is transcribed with Whisper
   `small.en` (word timings → word-by-word captions that match the dub). Japanese audio uses the text subtitle track
   (or a `.srt`/`.ass` next to it; Whisper translation as a fallback), without signs/song lyrics. Never copied.
2. **signals**: loudness, motion and every shot change; finds the OP/ED/preview (chapters, lyric styles, or a
   music-without-dialogue guess) so they are never picked.
3. **select**: Gemini reads all dialogue + an action/loudness timeline and proposes complete scenes (`clip`,
   about 60 s when the scene supports it, 20-90 s) or multi-beat edits with transitions and effects (`montage`).
4. **watch**: Gemini watches a 270p proxy of each pick, drops duds, tightens in/out, places zoom punches,
   rewrites the hook to match what is on screen.
5. **edit**: cut points snap to shot changes and never split a subtitle line; auto zoom punches on hits; montages
   are topped up with the episode's most intense unused moments until they reach 40 s. Song edits: the best song
   for each edit is picked from the music library (mood, energy, not overused), and shots are laid on its beats
   (faster cuts on the drop, the biggest moments on the drop, punches on the hits, flashes every 2 bars).
6. **render**: one ffmpeg pass: segments → slow-mo / zoom punch / shake / freeze → cuts or transitions
   (flash, zoom, whip, fade, glitch) → blurred background + tweet card + rounded video → captions → −14 LUFS.

Rerunning resumes from cache; `--again` asks Gemini for new picks. Settings: `configs/anime.yaml`.

**Music library** (song edits): Studio → Music, or put files in `data/anime/music/` (subfolders `hype/`, `epic/`,
`sad/`, `chill/`, `funny/` set the mood; otherwise Gemini listens once and tags it). Each song is analysed once:
tempo, beat grid, downbeats, energy, the strongest hits, and its best 40-60 s section. Use licensed music
(YouTube Audio Library): label songs add a second copyright claim.

## AI providers

Every AI call goes through `src/common/llm.py`; how each provider is called lives in `src/common/llm_providers.py`
(Gemini, or any OpenAI-compatible API such as NVIDIA NIM, OpenRouter, Groq, Ollama). Choose the provider per task in
**`configs/llm.yaml`**: no code changes.

## Layout

```
src/common/       shared: AI doorway (llm.py) + providers (llm_providers.py), ffmpeg helpers, job lock, runner, paths
src/studio/       the Studio server: mounts both APIs (/api/shorts, /api/anime), queue status, serves web/studio
src/shorts/       long video → shorts (stages/, cli.py, server.py)
src/anime/        anime edits (stages/ incl. amv.py, music.py, card.py, cli.py)
configs/          shorts.yaml, anime.yaml, llm.yaml (which AI provider answers what)
web/studio/       the one React app: src/kit (components + theme), src/shorts, src/anime, src/app (shell, home)
data/             all media (git-ignored): shorts/{uploads,workspace,exports}, anime/{episodes,music,workspace,exports}
tests/            shorts/, anime/, common/
.cache/           models, pip/npm caches, Hugging Face (git-ignored)
```

UI development with hot reload: `studio serve`, then `cd web && npm run dev` (http://localhost:5173, proxies `/api`).
UI settings (channel card) are saved to `configs/anime.local.yaml` (git-ignored).
