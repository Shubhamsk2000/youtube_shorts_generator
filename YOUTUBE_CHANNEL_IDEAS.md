# YouTube channel ideas: everything in one place

Last updated: 2026-11-14. This file collects every channel idea discussed so far, the four reference channels,
what was tested, what was learned, and the recommended next step. The earlier idea files are copied in full
at the end (appendices), so this is the only file you need to read.

**Contents**

1. [Where we stand](#1-where-we-stand)
2. [Lessons learned so far](#2-lessons-learned-so-far)
3. [The four reference channels](#3-the-four-reference-channels)
4. [Comparison and recommendation](#4-comparison-and-recommendation)
5. [Plan A (recommended): Manim maths and logic Shorts](#5-plan-a-recommended-manim-maths-and-logic-shorts)
6. [Plan B: Hindi data and map explainers](#6-plan-b-hindi-data-and-map-explainers)
7. [Other automatable ideas from the discussion](#7-other-automatable-ideas-from-the-discussion)
8. [Earlier ideas and their status](#8-earlier-ideas-and-their-status)
9. [Open decisions and next steps](#9-open-decisions-and-next-steps)
10. Appendices: [A. CHANNEL_IDEAS.md](#appendix-a-channel_ideasmd-puppet-animation-plan-and-discussion-log) ·
    [B. NPC Life idea](#appendix-b-npc_life_magical_customer_support_channel_ideamd) ·
    [C. History puppet style guide](#appendix-c-styleshistory-puppetstylemd)

---

## 1. Where we stand

| Channel / idea | Status |
|---|---|
| Long video → Shorts (existing pipeline) | **Working**, deployed on AWS. Fast on the server (1 GB download in seconds, 50-min transcript in ~15 min) |
| Anime edits (existing pipeline) | **Working** |
| Hindi history puppet animation (Animation tab in Studio) | **Built and tested, rejected on quality.** It looks like a slideshow with wobble, not like Historically |
| Hand-acted code animation (Taj Mahal architect test, 23.5 s) | **Made, rejected.** Better motion, but still far from Historically, and every shot must be hand-acted in code, so it can't run as a pipeline |
| Chubby couple + friend (English) | Not built. Same problem: needs real drawing/animation |
| NPC Life + Magical Customer Support (your idea file) | Not built. Same problem: depends on animation quality |
| **Manim maths/logic Shorts (Lingualin style)** | **Recommended next.** Visuals are pure code, which AI can generate |
| Hindi data & map explainers | Strong second choice. Visuals are pure code from public data |

---

## 2. Lessons learned so far

**What automates well, and what doesn't**
- **Automation works when the visuals already exist or can be computed.** The Shorts pipeline works because
  the footage exists and the code only chooses moments and follows faces. Maps, charts and maths animation
  work because code draws them perfectly every time.
- **Character animation does not automate at high quality (yet).** Channels like Historically, Puddle Art
  Studio and Punkey Doodles are made by artists and animators: rigs with 15–20 parts, hand-keyed acting,
  custom-drawn gags. Our attempts:
  - **Picture swaps** (whole pose images + bounce, the Animation tab) read as a slideshow.
  - **Code rigs** (the Taj test) move better, but each shot has to be acted by hand, which doesn't scale.
- AI image-to-video (Veo, Kling, Hailuo) is the only automated route to "alive" characters today. It is
  costly or rate-limited, characters drift between shots, and there is no exact lip-sync. Revisit when it
  gets cheaper and more consistent.

**Tools and quotas found while testing**
- **Gemini image API (Nano Banana 2.1, `gemini-nano-banana-2.1`):** free-tier quota is **0**, so it needs
  billing. Making images by hand on the Gemini website works with the Pro plan (daily limits; don't automate
  the website).
- **Gemini text-to-speech:** works for Hindi, but the free quota is **about 10 lines a day per model**.
  Studio chains 4 TTS models and falls back to Microsoft's free Hindi voices (unlimited, flatter).
- **TTS reading its instructions aloud:** happened on some lines; fixed by a "director's notes / transcript"
  prompt format plus an automatic length check and retry.
- **Free image generators:**
  - Puter.js is user-pays (not free for us).
  - Cloudflare Workers AI and Pollinations are limited.
  - A local model is impossible on this laptop (integrated AMD GPU, 5 GB RAM).

**YouTube rules that matter for automated channels**
- Since July 2025, **"inauthentic" (mass-produced, repetitive) content is not monetised.** Template videos
  where only the facts change, AI slideshows and Reddit-story-over-gameplay are at risk. Each video needs
  original work and value; keep a human review step.
- **Re-using other people's videos** (e.g. the current MrBeast clips) risks Content ID blocks and "reused
  content" monetisation refusals. Clip only creators who allow it.

**Already built and reusable for any new channel**
- **AI layer:** every AI call goes through `src/common/llm.py`; providers live in
  `src/common/llm_providers.py` (Gemini, any OpenAI-compatible API such as NVIDIA NIM, OpenRouter, Groq or
  Ollama, and Microsoft voices). Choose per task in `configs/llm.yaml`.
- **Studio:** one web app and one job queue for all tools, deployed on AWS (`scripts/deploy.sh`),
  `scripts/fetch-exports.sh` downloads only new exports.
- **Remotion renderer** (`web/animator/`): code-drawn video, maps, charts, Hindi captions (Baloo 2 font),
  −14 LUFS loudness, quality checks.
- **Audio:** sound effects made in code, music library by mood, ducking under voice.

---

## 3. The four reference channels

View counts are from the channels' recent uploads, checked 2026-11-14.

### 3.1 Lingualin: maths and logic animated with Manim

- **Link:** https://www.youtube.com/@lingualin
- **What it is:** short maths and logic puzzles, paradoxes and "aha" visualisations, animated entirely in
  code with **Manim** (the open-source Python animation library made by 3Blue1Brown).
- **Format:**
  - Mostly 30–60 s Shorts, some 2–8 min videos.
  - Black background, neon colours (cyan, magenta, green, yellow), clean geometric shapes, equations.
  - Minimal text; a coloured title line at the top of each thumbnail.
- **Typical titles and views (Shorts):**
  - The Biggest Sofa That Can Turn a Corner: 1.9M
  - How Many Folds to Reach the Edge of the Universe?: 1.8M
  - Is There a Tiny Gap Between 0.999… and 1?: 1.1M
  - The Obstacle Explained in 35 seconds: 955k
  - The Tangram Paradox Explained in 30 seconds: 704k
  - Why Does 7 Break the Pattern?: 672k
  - What If a Clock Wasn't a Circle?: 636k
  - Trajectoids: Shapes That Follow Your Path: 599k
  - Do You Know the Exact Length of This Nail?: 447k
  - The Smartest Way to Cut a Pizza: 442k
  - The 196 Trick: 414k
- **From the screenshot you shared:**
  - A Polygon With 2 Sides?: 1.3M
  - Euler's Formula Says We'll Only Drift Apart: 957k
  - How Does Intelligence Emerge: 767k
  - Not a Coincidence (√2 + √3 ≈ π): 697k
  - Where Did the Cookie Go? (missing cookie puzzle): 673k
  - The Curve Where Every Path Takes the Same Time: 499k
  - Why 59.27% Changes Everything (percolation): 301k
  - 3 Colors Aren't Enough (poker chip problem): 150k
  - Never to Return Where It Began (irrational numbers): 98k
  - Can You Trust What You See? (sphere illusion): 92k
  - Squid Game, Math Edition: 80k
  - Birthday Paradox Explained in 50 seconds
- **Long videos:** fewer views (2k–600k): "How Tall Would a Paper Be After 103 Folds?" 604k, "If Math
  Functions Had a Sound" 525k.
- **How it's made:** Python code (Manim scenes), music, and short on-screen text. Often no voice.
- **Automatable?** **Yes, the best of the four.** The visuals are code, code is what AI models write best,
  there's no artist, no image quota and no character consistency problem, and the topics are evergreen.
- **What to borrow:** the format (one surprising idea per Short, a hook in the first second, a visual
  "aha"), the clean dark neon look, short punchy titles.
- **What not to copy:** their exact videos, scripts or signature look one for one. Puzzles are public
  knowledge; the way we show them must be our own.

### 3.2 Puddle Art Studio: couple comic animation

- **Link:** https://www.youtube.com/@puddleartstudio/shorts
- **What it is:** short, relatable boyfriend/girlfriend comic animations (cute, simple, emotional, funny).
- **Typical titles and views:**
  - Yes ma'am: 1.9M
  - Peach: 1.1M
  - Clock it in: 1.1M
  - Dinner: 827k
  - Musically talented: 600k
  - Life with you: 594k
  - Expensive meal: 574k
  - Drive?: 554k
  - We're fine: 540k
  - Being bf: 379k
  - Biggest supporter: 344k
  - Said no: 330k
  - My GF: 330k
  - Eppy: 329k
  - Menu: 219k
- **Format:** very short Shorts, one-word titles, hashtags
  `#puddleartstudio #cartoon #content #animation #couple #illustration`.
- **How it's made:** an illustrator/animator draws and animates each short; the value is the drawing,
  the expressions and the relatable situation.
- **Automatable?** **No.** It's the same wall as our animation attempts, and it's our earlier "chubby couple
  + friend" idea (section 8.2). Possible only with a human artist or, later, good AI video.
- **What to borrow:** relatable micro-situations, one-word titles, a consistent couple.

### 3.3 Punkey Doodles: animation to other creators' audio

- **Link:** https://www.youtube.com/@PunkeyDoodles8
- **What it is:** animated fantasy comedy (wizards, vampires, necromancers, fairy tales) drawn over audio
  from voice creators ("Audio by @ZacSpeaksGiant", "Audio by @Vincey", "@Gnome_Anne").
- **Typical views:**
  - Shorts: 1M–12M (several around 5–10M).
  - Compilations: 150k–1.5M ("IT'S WIZARD TIME" Vol. 1–3, "The Necromancer Sisters", "Fractured Fairytales
    & Faulty Folklore", "IT'S VAMPIRE TIME").
- **How it's made:** an animator draws to a voice actor's comedy audio, as a credited collaboration, then
  bundles Shorts into long compilations (a smart way to reuse work).
- **Automatable?** **No.** Hand animation, plus audio that belongs to someone else (needs permission or a
  collaboration).
- **What to borrow:**
  - Turning many Shorts into long compilation videos (works for any channel).
  - Recurring series names ("IT'S WIZARD TIME").
  - Collaborating with voice creators.
  - The fantasy-comedy tone, which matches your NPC Life idea (section 8.3).

### 3.4 Data and map explainers in Hindi (our own idea)

- **Examples:** "India vs China in numbers", "Why Rajasthan has so little water", "Which state earns the
  most", "The population of Mumbai over 100 years".
- **Visuals:** 100% code-generated (animated maps, bar races, comparisons, timelines) from public data such
  as the World Bank, the Census of India, data.gov.in or Wikipedia.
- **Automatable?** **Yes.** No artist needed, consistent quality. Each topic needs new data gathering and
  careful number checking.
- Full plan: section 6.

---

## 4. Comparison and recommendation

| | Lingualin-style maths (Manim) | Hindi data & maps | Puddle Art (couple) | Punkey Doodles |
|---|---|---|---|---|
| Visuals made by | Code (AI-written) | Code + public data | Artist | Animator |
| Fully automatable | **Yes** | **Yes** (data checks) | No | No |
| Proven demand | 0.1–1.9M per Short | Strong in Hindi | 0.2–1.9M | 1–12M |
| Evergreen topics | Yes, hundreds | Mostly (data dates) | Yes | Yes |
| Voice needed | Optional (text + music) | Yes (Hindi narration) | Dialogue | Others' audio |
| Biggest risk | AI code bugs, maths errors | Wrong numbers | Needs an artist | Needs an animator + audio rights |
| Your time per video | ~5 min review | ~10 min (numbers) | Hours (drawing) | Hours (animation) |

**Recommendation**
1. **Start with Plan A, Manim maths/logic Shorts.** It's the most automatable, has proven demand, needs no
   voice quota, and the visuals are language-free (English and Hindi from one render).
2. **Plan B, Hindi data & map explainers,** as the second channel. It reuses the same code-visual approach
   (Manim or Remotion) and fits the original "Hindi" goal.
3. **Keep the Shorts clipping pipeline running,** switched to creators who allow clipping.
4. **Park the animation channels** (history puppets, couple, NPC Life) until you have an animator or AI
   video becomes good and cheap enough.

---

## 5. Plan A (recommended): Manim maths and logic Shorts

### 5.1 Concept

- **One surprising maths or logic idea per Short**, shown so clearly that the viewer "gets it" by watching:
  paradoxes, puzzles, illusions, famous problems, counter-intuitive probabilities, geometry tricks.
- **Length:** 30–60 s Shorts first. Later, 5–10 min long videos made by stitching related Shorts (the
  Punkey Doodles compilation idea) or deeper single topics.
- **Language (to decide):**
  - **English:** global audience, higher pay per view, competes with Lingualin and similar channels.
  - **Hindi (Hinglish):** a mostly empty niche ("Hindi 3Blue1Brown for Shorts"), huge audience of
    students.
  - **Both:** the same animation, only the on-screen text or voice changes, so two channels from one render.
- **Voice:** optional. Lingualin often uses text + music. No-voice versions avoid the TTS quota entirely.
  A voiced version can use Gemini/Microsoft voices or your own voice.

### 5.2 Topic bank (examples, to be extended by the AI)

Probability and statistics:
- Birthday paradox
- Monty Hall problem
- The gambler's fallacy
- Simpson's paradox
- Why random shuffles are unique
- The secretary problem (when to stop looking)
- Benford's law (why so many numbers start with 1)

Geometry:
- Why only 3 regular shapes tile a floor
- Why pentagons can't tile (and the 15 special pentagons that can)
- The missing square / cookie puzzle
- Reuleaux triangle (a non-circle that rolls smoothly)
- The sofa problem
- Pick's theorem
- Squaring the circle
- How many pieces can you cut a pizza into
- The shortest path for a spider in a room

Infinity and numbers:
- 0.999… = 1
- Hilbert's hotel
- Different sizes of infinity
- Why √2 is irrational
- Zeno's paradox
- The harmonic series diverges (the book-stacking overhang)
- Collatz conjecture
- Happy numbers
- 6174 (Kaprekar's constant)
- Why 9s are magic in division

Puzzles and games:
- The handshake problem
- The 100 prisoners and boxes puzzle
- Nim (how to always win)
- Tower of Hanoi
- Knight's tour
- The coin-weighing puzzle
- The bridge and torch problem
- Sudoku logic

Real-world maths:
- How compound interest explodes
- Paper folded 42 times reaches the Moon
- Why the shortest flight path looks curved on maps
- Why bubbles are round
- The hexagons of honeycombs
- Golden ratio myths vs truth

Each topic is a Short: hook (question or surprising claim) → setup → the twist → the visual "aha" → payoff line.

### 5.3 Style kit (our own look)

- **Colours:** dark background, one signature neon palette (e.g. electric cyan, warm saffron, magenta,
  lime), decided once and used everywhere.
- **Type:**
  - Titles: a clean sans with one accent colour.
  - Equations: Manim's LaTeX (`MathTex`).
  - Hindi captions: Baloo 2.
- **Format and motion:**
  - 1080×1920 at 60 fps (Shorts).
  - A persistent title line at the top, the visual in the centre, a short punchline at the end.
  - Smooth morphs, highlights, counters, before/after splits.
- **Branding:** a small logo or watermark and a 1-second end sting.
- **Sound:** soft music bed plus subtle clicks and whooshes on transformations. Not on every action
  (lesson from the animation tests).

### 5.4 Pipeline (how it would run in Studio)

1. **Topic:** the AI proposes topics from the bank (and new ones), avoiding repeats. You approve with one
   click.
2. **Storyboard:** the AI writes 5–8 beats with timings: hook, setup, twist, aha, payoff. It also lists
   the maths claims and their correct explanation (for your check).
3. **Code:** the AI writes a Manim scene using our style template and a library of reusable building blocks
   made once:
   - title card
   - neon shape styles
   - number counters
   - highlight / pulse effects
   - split screens
   - probability bars
   - grid and graph helpers
   - end card
4. **Automatic checks and fix loop:**
   - The code must run without errors (errors go back to the AI).
   - Low-resolution preview render.
   - Frames are sampled every ~1 s and Gemini looks at them for overlapping or off-screen text, empty
     frames, unreadable sizes, and visuals that don't match the beat.
   - Problems go back to the AI to fix, up to 3 rounds; then you're notified.
5. **Text / voice:** on-screen text in the chosen language(s); optional voice through the existing TTS layer.
6. **Final render:** 1080×1920, 60 fps, with music, sound effects and loudness at −14 LUFS (existing tools).
7. **Your review (about 5 minutes):** check the maths is right, watch it once, approve.
8. **Publish package:** title, description, tags, a thumbnail frame, exported like the other tools. Later:
   scheduled upload through the YouTube API.

### 5.5 What has to be installed

- **Manim Community** (Python, inside the project `.venv`).
- **System libraries for Cairo and Pango** (text rendering). They may need `apt` on the server.
- **LaTeX** (about 1 GB) for equations. It goes on the server; check the disk space.
- **ffmpeg:** already there.

### 5.6 Risks and how we handle them

- **AI-written Manim code fails or looks messy:**
  - Our own style template and building blocks limit what the AI has to invent.
  - The check-and-fix loop catches most problems.
  - Expect some videos to need a second try or a small manual nudge at first.
- **Maths errors:** the storyboard lists every claim; your 5-minute review is required before publishing.
- **Looking like a copy of Lingualin:** our own palette, fonts, pacing and topics; never remake their
  specific videos.
- **"Inauthentic content" policy:** every video is a new explanation with a new visual, not a template
  with swapped numbers; keep the human review.

### 5.7 First test

1. Install Manim in the project (check system libraries and LaTeX first).
2. You pick one topic, e.g. the handshake problem, why pentagons can't tile, Monty Hall, or folding paper to
   the Moon.
3. Make one Short exactly the way the pipeline would (AI storyboard → AI Manim code → checks → render).
4. You judge the quality. If it's good, build the flow into Studio.

---

## 6. Plan B: Hindi data and map explainers

### 6.1 Concept

Visual stories about India and the world told with numbers: animated maps, bar races, comparisons and
timelines, in Hindi.

### 6.2 Topic examples

- India vs China in numbers
- Why Rajasthan has so little water
- Which state earns the most (GDP per state)
- The population of Mumbai over 100 years
- Where India's electricity comes from
- How big is India's railway network
- Which states have the most young people
- India's cricket wins by decade
- Rupee vs dollar since 1947
- Where the monsoon rain falls
- The longest rivers and who shares them
- How India's cities grew

### 6.3 Data sources

World Bank Open Data, Census of India, data.gov.in, RBI, Ministry of Statistics (MoSPI), IMD (weather),
Natural Earth (map shapes), Wikipedia tables. Each video lists its sources in the description.

### 6.4 Visual building blocks (made once)

- India and world maps with states coloured by value
- Animated bar race
- Line chart over time
- Side-by-side comparison
- Counter
- Map zoom
- Icon arrays (e.g. 1 icon = 1 crore people)
- Timeline

Remotion already does maps (the `temp_demo` Homo sapiens video) and Hindi captions; Manim could be used too.

### 6.5 Pipeline

1. Topic.
2. The AI finds data sources and pulls the numbers (with links).
3. Automatic sanity checks on the numbers (totals, units, years).
4. The AI writes a Hindi script.
5. Voice.
6. Visuals from templates driven by the data.
7. Render.
8. **Your check of the numbers (about 10 min).**
9. Publish.

### 6.6 Risks

- Wrong or outdated numbers: these kill trust, so always show the source and year.
- Political or sensitive topics: avoid communal and election comparisons.
- Map borders: use the official India map borders to avoid problems.

---

## 7. Other automatable ideas from the discussion

### 7.1 Hindi history documentaries with real historical art and animated maps

- **Visuals:**
  - Public-domain Mughal miniatures, old paintings, maps, coins and photos from museum open-access APIs:
    the Met, Rijksmuseum, the British Library, Wikimedia Commons.
  - Slow zooms and pans, animated maps and timelines, text cards.
- **Reuse:** about 70% of the Animation tab: AI script with fact list, Hindi voices, captions, music,
  maps/timelines, render and export. Paintings replace the puppets.
- **Your work:** about 10 minutes of fact checking per video.
- **Why it can be good:** real paintings look premium and authentic; few Hindi channels do it well.
- **Limit:** less "funny" than the Historically-style idea; more documentary.

### 7.2 Clipping for creators who allow it

- Your existing Shorts pipeline as it is, pointed at podcasters and streamers who allow clips or pay
  clippers per view ("clipping programs").
- **Your work:** pick the source video, approve the clips.
- **Rule:** only creators who have given permission or run a program.

### 7.3 Ideas to avoid

- Reddit stories over gameplay
- AI slideshow "facts"
- Quiz templates

All are saturated and at risk under the "inauthentic content" policy.

---

## 8. Earlier ideas and their status

### 8.1 Hindi history puppet show (Channel 1): built, rejected on quality

- **Idea:**
  - Funny but accurate Hindi history in cut-out puppet animation, Historically-style (painted still
    backgrounds, simple chibi characters, detailed costumes, graphic gags).
  - 3–5 min videos + 30–40 s Shorts.
- **What exists:**
  - The **Animation tab** in Studio (Script → Images → Voices → Edit & preview → Render → Publish), with the
    AI script writer, copy-paste Gemini image prompts, character cut-out, per-line voices, live preview,
    draft/final renders and export.
  - Code: `src/animation/`, `web/studio/src/animation/`, `web/animator/`.
- **Result:** works end to end, but the animation reads as a slideshow; not the quality you want.
- **Also tried:**
  - The Taj Mahal architect film (`temp_demo/out/taj-architect.mp4`), hand-acted with code rigs.
  - Better motion, still not Historically, and not automatable.
- **Kept for reference:**
  - Style guide (Appendix C) and reference frames in `temp_demo/styles/history-puppet/`.
  - Full plan and discussion log (Appendix A).
- **Could come back if:** an animator joins, or AI video (Veo etc.) becomes consistent and affordable.
  Plan 7.1 (paintings + maps) is the automatable version of this idea.

### 8.2 Chubby couple + friend (Channel 2, English): not built

- **Idea:** short funny gf/bf/friend interactions, chubby art style, solid-colour backgrounds, simple
  reactions (like Puddle Art Studio).
- **Status:** not built; it has the same animation problem.
- Details: Appendix A, "Channel 2".

### 8.3 NPC Life + Magical Customer Support (your idea file)

- **Idea:** comedy in an original fantasy RPG world (Eldoria) where NPCs are the real main characters.
  - Two formats: "NPC Life" and a "Magical Customer Support" desk.
  - Recurring cast: Greg the goblin shopkeeper, Bones the skeleton guard, Merlin the wizard, Luna the elf,
    Spark the baby dragon.
  - Channel name favourite: "Quest & Chaos".
- **Assessment:** a strong, original concept with recurring characters and running gags, close to Punkey
  Doodles' fantasy comedy. But its success depends on animation and character acting, so it has the same
  problem as 8.1 and 8.2. Realistic routes:
  - with an animator (and possibly voice-actor collaborations, like Punkey Doodles), or
  - later with AI video.
  - A cheaper first step: a comic-strip / storyboard style with good voice acting.
- Full file: Appendix B.

---

## 9. Open decisions and next steps

**Decide**
- [ ] Plan A (Manim maths/logic) as the next channel? (recommended)
- [ ] Language for Plan A: English, Hindi/Hinglish, or both
- [ ] Voice or no voice for Plan A
- [ ] First test topic for Plan A
- [ ] Plan B (Hindi data & maps) as the second channel?
- [ ] Switch the Shorts clipping to creators who allow clipping
- [ ] Channel names

**Next steps**
1. [ ] Check and install Manim (system libraries, LaTeX) in the project.
2. [ ] Make one test Short for the chosen topic, the pipeline way.
3. [ ] If the quality is right: build the Manim flow into Studio (topic → storyboard → code → checks →
   render → review → export).
4. [ ] Deploy (commit, push, `scripts/deploy.sh`). The Animation tab and LLM provider layer are built but
   not committed or deployed yet.
5. [ ] Later: scheduled uploads through the YouTube API.

---

## Appendix A: CHANNEL_IDEAS.md (puppet animation plan and discussion log)

*Copied in full from `temp_demo/CHANNEL_IDEAS.md`.*

### New channel ideas: puppet animation pipeline

Status: **discussion / planning**. Nothing built yet.

Two channels, one shared **puppet engine**, produced through an automated pipeline like the shorts
pipeline (script in → finished video out), not hand-animated per video. It is driven from a new
**Animation** tab in Studio.

**Files in `temp_demo/`:**
- this file: the plan and the discussion log
- `styles/history-puppet/STYLE.md`: Channel 1 style guide, with palette and reference frames
- `lemo-opuscar/`: the film-style skill library (inspiration and reusable MIT audio code)
- `pipeline/` + `video/`: the first proof of concept (Homo sapiens explainer)

---

#### Next steps

1. [ ] Decide our own signature for the history style (`styles/history-puppet/STYLE.md` §10).
2. [ ] Style test on the Gemini website: one character, 2–3 expressions + a side/back view, one
       background. Check consistency, watermark and look.
3. [ ] Decide the Channel 2 (couple) look and characters; same test.
4. [ ] Voice test: one Hindi narration line in several Gemini voices; one short couple exchange.
5. [ ] Answer the open questions at the end of this file.
6. [x] Build the puppet engine + Studio Animation tab (Channel 1).
7. [ ] Deploy: commit, push, `scripts/deploy.sh` (installs Chrome for the renderer on the server).
8. [ ] Fill the Channel style page (signature, narrator voice) and add music per mood.
9. [ ] Make the pictures for the Nadir Shah Short and render it as the first pilot.

---

#### Discussion log

What we discussed and decided, in order (2026-11-12 → 2026-11-13).

1. **lemo-opuscar skill reviewed.**
   - It is 99 film styles plus directing/technique guides; the agent writes code for every film.
   - Great quality, but 30–60 minutes and lots of tokens per film.
   - **Decision:** use it for inspiration and reuse its MIT code (sound effects, sampler, loudness,
     checks); don't use it per video.
   - Notes:
     - It needs ffmpeg 7+ (we have 6.1).
     - It is cloned, not installed as a plugin, which keeps it off the full `/home` disk.
     - Its guide texts are non-commercial; films made with it are ours.
2. **First channel idea** (character duo + funny facts in Hindi) proposed: pipeline-made, high-quality
   cut-out puppet animation with AI-drawn assets.
3. **Nano Banana 2.1 API tested.** The model `gemini-nano-banana-2.1` exists, but the free-tier quota is
   **0**; the API needs billing.
4. *(Side task)* `scripts/fetch-exports.sh` now downloads only new shorts (keeps
   `data/server-exports/.fetched-*.txt`; `--mark-seen`, `--all`).
5. **The two real channel ideas** (this file):
   - Hindi history puppet show (3–5 minutes + 30–40 second shorts)
   - English chubby couple + friend
   - **Decision:** one shared puppet engine for both.
6. **Free image generation looked at:**
   - Puter.js is user-pays (not free for us).
   - Cloudflare and Pollinations are limited.
   - A local model is impossible on this laptop.
7. **Decision: images are made by hand on the Gemini website** (Pro plan). The pipeline writes the prompt
   list and you drop the images in. No automation of the website.
8. **Studio UI:** a new Animation tab with steps from script to export. Remotion Player gives a live preview,
   and a scene editor fixes missing animation.
9. **No code per video:** the engine is written once; videos are data (script, voices, images). New code
   only for new kinds of scene or effect.
10. **Voices:** text-to-speech per line, emotion from the script, Whisper check, Rhubarb lip-sync. Start
    with Gemini TTS (free, working).
11. **Art style system:** a style kit per channel (reference images + style prompt + theme settings).
    Restyling swaps images only.
12. **Reference for Channel 1: "Historically"** (https://youtu.be/mtdqhaX44YQ).
    - Detailed but still backgrounds, simple character bodies, costumes giving the character.
    - Very pipeline-friendly.
    - **Decision:** keep the formula, but give it our own signature (not the white blank face).
    - Style guide written: `styles/history-puppet/STYLE.md`.
13. **Built: the Animation tab in Studio (Channel 1)** (2026-11-13).
    - Steps: Script (AI or pasted, fact list) → Images (copy-paste Gemini prompts, auto cut-out) → Voices
      (per line, per character) → Edit & preview (live Remotion preview) → Render (draft/final, checks) →
      Publish (title/description/tags/chapters/thumbnail, export, "make a Short from these scenes").
    - Code: `src/animation/`, `web/studio/src/animation/`, `web/animator/`. Docs: README + `docs/DEPLOY.md`.
    - AI providers are switchable in `configs/llm.yaml` (Gemini, NVIDIA, OpenRouter, Groq, Ollama).
    - Found while testing: Gemini TTS sometimes read its instructions aloud (fixed by prompt format + auto
      retry); the free TTS quota is about 10 lines/day per model (4 models chained + free Microsoft voices
      as backup).
    - Example episode kept: the AI-written Nadir Shah Short (pictures still to make).

---

#### Channel 1: Hindi history puppet show

**What:** high-quality puppet (cut-out) animation in **Hindi** about history:
- important incidents and turning points
- war stories
- "a day in the life of a commoner" in a given era
- other story-driven history

**Formats:**
- Main videos: **3–5 minutes**, detailed, story-driven.
- Shorts: **30–40 seconds**, either cut from the main videos with the existing long → shorts
  pipeline, or written as standalone shorts.

**Look:** in the formula of the "Historically" channel, with our own signature. See
`styles/history-puppet/STYLE.md`.
- Detailed, painted, **still** backgrounds; the camera pans and zooms over them.
- Only the characters move: simple chibi bodies with near-blank faces; detailed period costumes give
  each one their identity.
- Jokes and explanations as graphic inserts: maps, timelines, posters, game screens, speed lines.

**How it's made:**
- **Narrated:** a Hindi narrator tells the story while puppet characters act it out, with occasional
  dialogue. That is far easier to automate than full drama.
- **Characters come from a growing library:**
  - reusable types per era (soldier, farmer, merchant, king, monk… for Maurya, Mughal, British Raj…)
  - specific historical figures, created when an episode needs them
  - new character sheets generated from one style reference and approved once
- **Backgrounds:** illustrated scenes per episode (fort, village, court, battlefield) in one fixed style,
  split into layers that move at different speeds for depth.
- **War scenes:** animated maps (moving arrows, army markers, dates/timeline) for scale, as in
  `temp_demo`; puppet close-ups of a few characters for emotion. No large animated crowds.

**Risks:**
- **Accuracy is the biggest risk.** AI invents facts and history audiences notice. The script step
  must gather sources and list every factual claim for review.
- Render time for 3–5 minute videos: render on the server, not the laptop.
- New art is needed every episode (characters, backgrounds): one short image session per episode on
  the Gemini website (see "Images").

---

#### Channel 2: chubby couple + friend (English)

**What:** short, funny **English** interactions between a girlfriend, a boyfriend and their friend.
- Chubby, cute art style.
- Plain solid-colour backgrounds.
- Very simple animation: reactions, expressions, small movements.

**How it's made:**
- **One-time:** design 3 characters, each with about 20 expressions/poses
  (happy, angry, shy, crying, eye-roll, hug, shocked…) plus mouth shapes and blinks.
- **Per episode, fully automatic:**
  1. AI script: dialogue, with an emotion tag on every line.
  2. One voice per character (see "Voices").
  3. Pick the matching pose for each line, then add lip-sync, bounce/squash, effects
     (hearts, sweat drops, anger marks), captions and sound effects.
  4. Render.
- No new art per episode, so it is almost free to run and could be daily.

**Risks:**
- The genre is crowded; success depends on the **humour and relatability of the scripts**.
  We need a strong channel bible plus human review.
- Characters must be original, not look-alikes of an existing channel's characters.

---

#### Shared puppet engine

Built once in Remotion (already set up in `temp_demo/video`); both channels use it.

##### No code is written per video

**Written once (the engine):**
- the puppet system: poses, lip-sync, blinking, bounce
- scene templates
- the effects library
- captions, sound timing, rendering, checks
- the UI

**Changes per video (data only):**
- the script file (made and edited in the UI)
- the voices (generated)
- the images (from the library, or from a Gemini website session)

New code is needed only when a video needs something the engine can't do yet (e.g. a first naval
battle, rain or fire, a new chart type). It is added once as a template or effect and becomes a UI
option for every later video. This happens often in the first 5–10 episodes, then rarely.

Contrast: the lemo-opuscar skill writes fresh code for every film. Great for one-offs, too slow and
token-expensive for regular uploads.

##### How a character works

Each character is a folder of pictures plus a small settings file:
- **Poses:** about 10–25 full-body images (standing, pointing, shocked, laughing, sitting, walking…)
- **Mouth shapes:** about 6 images, used for lip-sync
- **Eyes:** open/closed, for blinking
- **Settings file:** where the face sits in each pose, scale, and the default voice

Pose names are the same for every character (`happy`, `angry`, `shocked`…), so any character works
in any template, and an art-style change only swaps the pictures (see "Art style").

##### What code animates

- Pose swaps for each line
- Lip-sync to the voice
- Blinking
- Idle breathing and bounce, squash and stretch, head tilts
- Camera: zooms, pans, shakes
- Effects: hearts, sweat drops, anger marks, dust, emojis
- Captions (Hindi in Devanagari or Hinglish; English)
- Music and sound effects timed to events

##### Scene templates

Dialogue, narration, reaction, map (history), title card, end card.

##### Input format

One script file per episode: scenes → lines → speaker, text, emotion/pose, background, effects.

---

#### Art style

Each channel has a **style kit**, defined once:
- **Style reference images:** 1–3 approved images (one character + one background) that show the look.
- **Style prompt:** a fixed paragraph added to every image prompt (line weight, shading, colour
  palette, proportions, background treatment).
- **Theme settings (code):** caption font and colours, effect colours, outline thickness of effects,
  bounce strength, transition style. Changing them needs no new images.

**Choosing a style:**
1. Collect references you like: screenshots, the lemo-opuscar gallery (99 looks) as inspiration, or
   just words ("chubby, pastel, thick outlines"). Describe traits; don't copy a specific channel's
   or artist's characters.
2. Claude writes the style prompt; you make 2–3 test images on the Gemini website.
3. Pick one and iterate until it's right. That image becomes the style reference.

**Changing the style later:**

| Change | What it costs |
|---|---|
| Colours, captions, effects, animation feel | Theme settings only; instant |
| Small art tweak (shading, line weight, palette of characters) | Regenerate images using the old ones as reference |
| Completely new art style | Regenerate the character library and backgrounds (one Gemini session per character). Engine, templates and script files unchanged, because pose names stay the same |

- Old videos keep the old look. Viewers notice a sudden switch, so restyle rarely, or call it a
  "season 2".
- Channel 1 can have small **per-era variants** (e.g. warmer palette for the Mughal era, sepia for the
  British Raj) on top of one base style.

---

#### Images

**Decision: generate images by hand on the Gemini website** (you have the Gemini Pro plan, which
includes Nano Banana image generation at no extra cost).

- The Pro plan covers the app and website only, **not the API**. The API test of
  `gemini-nano-banana-2.1` returned a quota error (free-tier limit 0), so API image generation would
  need separate billing.
- **Do not automate the website** (a bot clicking through it breaks Google's terms and risks the
  account).

**The hand step:**
1. The pipeline writes an image list for the episode: prompt to paste, reference image to upload
   with it, and the file name to save as.
2. You generate the images on the Gemini website and drop them into the episode folder, or into the
   UI. About 10–20 minutes for a history episode.
3. The pipeline checks for missing or wrong files, cuts characters out of their plain background,
   and continues.

| Channel | Hand image work |
|---|---|
| Channel 2 | **Once**, for the character set. Fully automatic afterwards. |
| Channel 1 | One short session per episode, shrinking as the character and background library grows. |

**Tips:**
- Always upload the approved character image as a reference ("same character, same style, now angry").
- Ask for a plain flat background so the character can be cut out cleanly.
- Check for a visible watermark in the corner; if there is one, the pipeline crops or covers it.
- The Pro plan has a daily image limit; a full character set may take a day or two.

**Free alternatives looked at (not chosen):**
- **Puter.js:** "free" only for website owners. Each visitor pays through their own Puter account
  (e.g. about $0.03/image for FLUX.2 dev). For us it isn't free, and it's built for browsers, not
  pipelines.
- **Cloudflare Workers AI** (FLUX schnell): small free daily quota; weak character consistency.
  Possible later for Channel 1 backgrounds.
- **Pollinations.ai:** free but rate-limited and best-effort. Prototyping only.
- **Running a model locally:** not possible on this laptop (integrated AMD Vega graphics, 5 GB RAM).

---

#### Voices

Text-to-speech: an AI model turns the script text into spoken audio. `pipeline/gen_voice.py` already
does it with Gemini TTS (`gemini-3.8-flash-tts`).

**What we send:** the text, a voice name (about 30 ready-made voices), and a plain-language style
instruction (e.g. "narrate like a gripping documentary").

**For the new channels: one audio file per line**, not one long narration:
- **Each character has its own voice.** For example, a bright female voice for the girlfriend, a
  relaxed male voice for the boyfriend, an energetic voice for the friend, and a deep, warm voice for
  the Hindi narrator.
- **Each line's emotion tag becomes the style instruction** ("annoyed", "whispering", "laughing"),
  so the dialogue sounds acted.
- **Edits are cheap:** changing one line regenerates only that line.

**After each line:**
1. **Check:** Whisper transcribes it back; if a word is wrong, the line is regenerated.
2. **Timings:** word timings for captions and cuts.
3. **Lip-sync:** Rhubarb picks the mouth shape for each moment.
4. **Placement:** lines go onto the timeline with natural pauses (or overlaps for interruptions).

| Option | Hindi | English | Cost | Notes |
|---|---|---|---|---|
| **Gemini TTS** (start here) | Yes | Yes | Free tier, with a daily limit | Plain-language emotion control; works now |
| Microsoft edge-tts | Yes (Madhur, Swara) | Yes | Free (online) | Clear, less expressive |
| Kokoro | Weak | Good | Free, offline | English only, in practice |
| Sarvam AI | Very good | Indian English | Paid, with free credits | Most natural Hindi |
| ElevenLabs | Good | Excellent | Paid | Most expressive; voice cloning |
| Your own recording | Yes | Yes | Free | Already supported (`gen_voice.py --own`) |

**Next step:** a voice test (one Hindi narration line in a few voices; one short couple exchange)
to pick the voices by ear.

---

#### Studio UI: the Animation tab

A new tab in the existing Studio (FastAPI + React + shared job queue). The **Remotion Player** shows
the animation live in the browser while editing, with no render needed until Export.

1. **Idea / script:**
   - pick the channel and the format (short or 3–5 minutes)
   - paste a script, or type an idea for Gemini to draft
   - history: facts listed for checking
2. **Script editor:** a table of lines (speaker, text, emotion); add, delete, reorder, rewrite.
3. **Voices:** generate, play each line, regenerate, change voice/speed, or upload your own recording.
4. **Images:**
   - checklist of needed images, each with its prompt and a copy button
   - drag-and-drop upload, automatic background removal
   - library items reused
5. **Scene editor** (for fixing missing animation):
   - timeline with live preview
   - click a line to change pose, entry animation, effects, background, camera move, caption style
   - add sound effects or music changes
   - adjust timing
6. **Render:** joins the shared job queue; automatic checks; watch the result.
7. **Export:** MP4 plus title, description and tags; later, scheduled upload.

**Build order:**
1. Pipeline and templates working end to end (command line).
2. UI steps 1–3 and 6–7.
3. The image checklist (step 4).
4. A simple scene editor (dropdowns per line).
5. Timeline dragging (advanced).

---

#### Pipeline

1. **Topic bank:** AI-generated ideas plus trending topics, ranked.
2. **Script:** an AI script writer guided by the channel bible (history: sources and claims listed).
   It outputs the structured script file.
3. **Review (you):** about 2 minutes for Channel 2; longer for Channel 1 (fact check).
4. **Voices:** one file per line, with a speech-to-text check (see "Voices").
5. **Timings and lip-sync:** Whisper word timings (`pipeline/align.py`) plus Rhubarb mouth shapes.
6. **Art:**
   - Channel 2: none (uses the character library).
   - Channel 1: the hand step on the Gemini website (see "Images"); results cached and reused.
7. **Assembly:** Remotion builds the video from the script file and templates.
8. **Music and sound effects:** library and synthesised sounds picked by mood and event. The MIT code
   in `lemo-opuscar/core/audio` (sfx, sampler) can be reused.
9. **Render:** 1080×1920 for shorts; 1920×1080 or 1080×1920 for long videos; on the server.
10. **Automatic checks:**
    - loudness about −14 LUFS
    - no black frames
    - captions on screen long enough to read
    - voice check (speech-to-text)
    - a contact sheet of frames for a quick look
11. **Metadata:** title, description, tags and thumbnail.
12. **Upload:** scheduled, via the YouTube API (after your approval at first).

---

#### Tools and services

| Need | Choice | Status |
|---|---|---|
| Character and background art | Nano Banana on the **Gemini website** (Pro plan), by hand, with reference images | Available. API needs separate billing (free-tier limit 0) |
| Hindi voice | Gemini TTS; alternatives: edge-tts, Sarvam AI, ElevenLabs | Gemini TTS tested in `temp_demo` |
| English voices | Gemini TTS or Kokoro | Not tested yet |
| Word timings | faster-whisper (`pipeline/align.py`) | Working |
| Lip-sync | Rhubarb Lip Sync | Not tested yet |
| Background removal | Generate on a flat colour and key it out, or rembg | Not tested yet |
| Animation, preview and render | Remotion (+ Remotion Player in Studio) | Render working in `temp_demo/video` |
| UI | Studio (FastAPI + React), new Animation tab | Studio exists; tab not built |
| Music and sound effects | Synthesised plus free sample libraries (lemo-opuscar `core/audio`, MIT) | Partly working (`pipeline/gen_sound.py`) |
| Hindi font | Baloo 2 / Mukta (open licence) | Not set up yet |

---

#### Suggested order

1. **Puppet engine + Channel 2:** 3 characters, dialogue template, lip-sync, effects, one finished
   episode.
2. **Channel 1 pilot:** one 3–5 minute episode, one era, about 5 characters, map scenes for the war.
3. **Channel 1 shorts:** through the existing long → shorts pipeline.
4. **Studio Animation tab** (in the build order above).
5. **Automation:** batching, server rendering, scheduled uploads for both channels.

#### What you do yourself

- Choose the art style and approve each character design (once per character).
- Generate images on the Gemini website (once for Channel 2; per episode for Channel 1).
- Review scripts (and check facts for history).
- Pick the voices.
- Watch final videos before publishing, at least at the start.

---

#### Open questions

- [ ] Is starting with Channel 2 OK?
- [ ] Channel 2 characters: personalities, names and looks (or should Claude propose a few?)
- [x] Channel 1 reference style: "Historically" (`styles/history-puppet/STYLE.md`)
- [ ] Channel 1 signature: skin-tone faces, expressive eyes, head shape, Indian palette, mascot?
- [ ] Channel 2 art style: reference images or words describing the look
- [ ] Test on the Gemini website: one character plus 2–3 expressions (consistency, watermark, style)
- [ ] Voice test: pick voices by ear
- [ ] Channel 1: which era or story for the first episode?
- [ ] Hindi captions: Devanagari or Hinglish (Roman)?
- [ ] Channel names
- [ ] Upload frequency for each channel

---

## Appendix B: npc_life_magical_customer_support_channel_idea.md

*Copied in full from `temp_demo/npc_life_magical_customer_support_channel_idea.md`.*

### YouTube Channel Concept: NPC Life + Magical Customer Support

> A fictional fantasy RPG world where NPCs have their own lives, problems, friendships, jobs, and opinions about the heroes who keep disrupting everything.

#### 1. Core idea

Create a comedy-focused YouTube channel set inside an original fantasy video-game universe.

The world looks like a familiar medieval RPG: villages, kingdoms, wizards, dragons, goblins, adventurers, quests, shops, and dungeons.

But the twist is that the **NPCs are the real main characters**. They have lives outside the player's quests. They gossip, work, complain, fall in love, get annoyed, and try to survive the chaos caused by adventurers.

The central joke:

**The hero thinks they're the main character. The NPCs know they have their own lives.**

The concept combines two recurring formats:

- **NPC Life:** Funny glimpses into the everyday lives of game-world NPCs.
- **Magical Customer Support:** A customer-support desk that handles absurd problems caused by quests, spells, bugs, items, and players.

The two formats share one universe, so characters and locations can appear across multiple episodes.

#### 2. The fictional world

Working world name: **Eldoria**

A fantasy RPG setting with reusable locations:

- **Goldleaf Village** — shops, homes, guards, villagers, and everyday NPC drama.
- **Magic Academy** — wizards, failed experiments, magical accidents, and cursed objects.
- **Dragon Mountains** — dragons, caves, delivery routes, and flying problems.
- **Adventurer's Guild** — quest assignments, hero complaints, rankings, and party drama.
- **The Dungeon** — monsters, traps, treasure chests, and enemies who are tired of being defeated.

These are working names. The world and channel name can be finalized later.

#### 3. The two main video formats

##### A. NPC Life

Short comedy episodes about what happens when the player is not around—or when the player behaves terribly.

Possible episodes:

1. NPCs When the Hero Enters Their Shop
2. A Shopkeeper's Worst Customer
3. Guarding the Same Gate for 300 Years
4. The Hero Keeps Stealing From My House
5. What NPCs Do After the Player Logs Out
6. A Guard's Dream of Becoming an Adventurer
7. NPCs Gossip About the Main Character
8. The Village Holds a Meeting About the Hero
9. Monsters Who Are Tired of Being Farmed for XP
10. NPCs React to the Game Getting an Update

##### B. Magical Customer Support

A magical help desk where NPCs report ridiculous game-world issues.

Possible episodes:

1. “My potion doesn't work.”
2. “My dragon refuses to fly.”
3. “The hero killed me 47 times today.”
4. “My inventory is full.”
5. “The teleportation spell left me inside a wall.”
6. “I lost my sword after respawning.”
7. “The hero keeps skipping my dialogue.”
8. “The quest giver assigned me my own rescue quest.”
9. “A wizard accidentally deleted an NPC.”
10. “The final boss won't stop asking for a rematch.”

#### 4. Main characters

These are starter concepts, not final designs.

| Character | Role | Personality and recurring joke |
|---|---|---|
| **Greg** | Goblin shopkeeper | Greedy but lovable. Hates adventurers who steal items and complain about prices. |
| **Bones** | Skeleton guard | Has guarded the same gate for 300 years. Dreams of adventuring but cannot leave his assigned post. |
| **Merlin** | Wizard / customer-support operator | Incompetent, overconfident, and always blames “magical interference” when something goes wrong. |
| **Luna** | Elf adventurer | Wants to save the kingdom but gets increasingly frustrated by the strange NPCs around her. She can connect the NPC world to the player's perspective. |
| **Spark** | Baby dragon | Thinks he is terrifying but cannot breathe fire reliably. Often causes trouble while trying to help. |

#### 5. Example Short: “NPCs When the Hero Enters Their Shop”

**Estimated length:** 35–40 seconds

##### Scene 1 — A peaceful day

Greg is behind his shop counter, drinking coffee.

**Greg:** “Finally! A peaceful day. No adventurers, no chaos, no stolen merchandise.”

##### Scene 2 — The hero arrives

The shop door opens. The hero walks in.

**Greg:** “Oh no. Not this guy again.”

**Hero:** “Greetings, merchant! Show me your finest weapons.”

##### Scene 3 — The chaos

The hero starts opening chests and taking potions.

**Greg:** “HEY! That's private property!”

**Hero:** “I need these for my quest.”

**Greg:** “YOU HAVE 400 POTIONS! WHAT ARE YOU PREPARING FOR?”

##### Scene 4 — The aftermath

The hero walks out without paying.

Greg stares at the empty shelves.

**Greg:** “One day... I'll become the final boss.”

**On-screen caption:** “POV: You're an NPC.”

#### 6. More comedy angles

Use familiar video-game mechanics as sources of jokes:

- Players stealing from houses while the owners stand nearby.
- NPCs repeating the same dialogue thousands of times.
- Heroes accepting every quest but never completing them.
- Shopkeepers dealing with players who sell hundreds of stolen items.
- Guards being unable to chase a player because of invisible boundaries.
- Monsters discussing how often they get defeated and respawn.
- NPCs panicking when the game gets a patch or update.
- Characters noticing that the player has paused the game.
- Quest givers arguing over who has to explain the same quest again.
- NPCs reacting to speedrunners who skip every conversation.

#### 7. Visual and animation approach

Keep production simple enough to make consistently as a solo creator.

A practical starting approach:

- Simple 2D cartoon characters with recognizable silhouettes.
- Reusable character poses and facial expressions.
- Mostly static backgrounds, with small movements for emphasis.
- Quick cuts, expressive reactions, dialogue, captions, and sound effects.
- A consistent visual style across both video formats.

Build a small reusable asset library rather than drawing every scene from scratch.

##### Starter asset list

For the first 10 Shorts, aim to create:

- 5 main character designs.
- 5 reusable backgrounds.
- 5–6 facial expressions per character.
- 4–5 basic poses per character.
- 15–20 reusable sound effects.
- 3–4 background music tracks.

##### Repeatable production workflow

1. Write a short script with one clear joke or situation.
2. Break it into 3–5 scenes.
3. Reuse character designs, poses, expressions, and backgrounds.
4. Add dialogue or narration.
5. Add sound effects, music, and captions.
6. Edit tightly so the joke lands quickly.

#### 8. Possible channel names

- Quest & Chaos
- NPC Chronicles
- The NPC Club
- Behind the Quest
- Eldoria Tales
- Pixel Problems
- Respawn Required
- SideQuest Stories

**Current favorite:** Quest & Chaos

The channel name and world name do not need to be the same.

#### 9. What makes the concept distinctive?

The channel is not just a collection of unrelated gaming jokes. It has a shared fictional world and recurring characters.

Viewers can return to see what Greg, Bones, Merlin, Luna, and Spark are doing next. A joke introduced in one Short can become a running gag in another.

The strongest hook is the contrast between:

- What the player thinks is happening.
- What the NPC experiences.
- What is actually happening behind the scenes in the game world.

#### 10. Decisions to make later

These choices are intentionally left open:

- [ ] **Channel name:** Quest & Chaos / NPC Chronicles / The NPC Club / Behind the Quest / Eldoria Tales / Pixel Problems
- [ ] **Art style:** Cute colorful hand-drawn 2D / black-and-white doodles / 2D fantasy game art / pixel art
- [ ] **Humor:** Funny and sarcastic / absurd and chaotic / wholesome and cute / dark humor
- [ ] **Game awareness:** All NPCs know they live in a game / only a few know / nobody knows

#### 11. First milestone

Do not try to build the entire fantasy world immediately.

Start with:

1. Finalize the basic art style and character designs.
2. Create the five main characters and a few reusable backgrounds.
3. Make the first three Shorts:
   - NPCs When the Hero Enters Their Shop
   - Guarding the Same Gate for 300 Years
   - My Dragon Won't Fly
4. Publish, observe which jokes and characters viewers respond to, and use that feedback to shape the next episodes.

---

**One-line pitch:**  
*A comedy series set in a fantasy RPG where the NPCs have their own lives—and the players are their biggest problem.*

---

## Appendix C: styles/history-puppet/STYLE.md

*Copied in full from `temp_demo/styles/history-puppet/STYLE.md`.*

### Style guide: History Puppet (Channel 1)

Status: **draft**. Built from a reference channel; our own signature not decided yet (see the end).

- **Reference:** "Historically", *That Time PIRATES started a COUNTRY… [No AI]*
  (https://youtu.be/mtdqhaX44YQ, 18 min, about 2.3M views).
- **Files:**
  - `styles/history-puppet/reference/`: 24 frames from that video, named by what they show
  - `reference-board.jpg`: all 24 frames on one sheet
  - `palette.png`: the colour swatches below
- **Reference frames are for our study only.** Don't publish them or put them in a public repo. They are
  the channel's copyrighted artwork.

![reference board](styles/history-puppet/reference-board.jpg)

---

#### Why this style suits our pipeline

- **Backgrounds are detailed but still.** One painted image per scene; the camera pans and zooms over it.
  No background animation needed.
- **Only the characters move**, and they have **simple body shapes**. Costumes (hats, coats, wigs, beards,
  bandanas) give each one their identity. Simple shapes mean:
  - easy to keep consistent across images
  - easy to cut out
  - easy to animate as puppets
- **Faces are almost blank:** dot or line eyes and a big mouth. Expressions and lip-sync are just small
  mouth and eye swaps.
- **Jokes and explanations are graphic inserts** (posters, maps, timelines, game screens). Each one is a
  code template we build once and reuse.

---

#### 1. Characters

| Trait | Rule |
|---|---|
| Head | Large, round, plain **white** face (`#FEFEFD`), no nose, no ears showing. Head is about 1/3 of body height (chibi proportions, 3–3.5 heads tall) |
| Eyes | Small black dots, or short curved lines (happy/closed/smug). Eyebrows are rare; emotion comes from eye shape + mouth |
| Mouth | The main expressive feature. Big, open shapes for shouting and laughing (dark inside, a hint of tongue), small line for neutral |
| Face shading | One soft cool-grey shadow (`#B09EA0`) on the side of the face away from the light |
| Body | Simple, soft, slightly rounded tube shapes; small mitten-like white hands; short legs |
| Costume | **This is the character.** Detailed period clothing: tricorne hats, coats with buttons, wigs, long hair, beards, bandanas, belts, boots. Costume carries texture and fold lines, unlike the face |
| Outline | Thick, dark warm-black (`#201C18`), slightly varied width like a brush; thicker on the outer silhouette |
| Shading | Flat colour + one shadow tone (cel shading). No gradients on characters |
| Angles needed | Front, ¾ left/right, side, **back view** (often used over-the-shoulder), close-up and full body |

**Expressions seen most:** big laugh (eyes closed arcs), smug half-closed eyes, shocked (open mouth, dot
eyes), angry (gritted teeth), facepalm, crying, unimpressed side-eye.

#### 2. Backgrounds

| Trait | Rule |
|---|---|
| Detail | **Much more detailed than the characters**: painted textures on wood grain, stone blocks, sand, water |
| Sky | Very saturated cyan (`#49FEFB`) fading to pale haze at the horizon, with big puffy cartoon clouds |
| Water | Turquoise to deep teal, lighter bands near shore, painted foam lines |
| Lighting | Bright daylight by default; mood scenes switch the whole palette (sunset gold/orange, burning-town orange, dim brown interiors) |
| Outlines | Background objects also have dark outlines, but thinner and softer than characters |
| Depth | Simple layers: foreground (railing, crates, cannon), middle (characters), back (sea, fort, palms) |
| Composition | Wide shots with tiny characters for scale; medium and close shots with simpler, blurred backgrounds |
| Motion | **Still.** Only camera moves (pan, push in, pull out) and occasional small loops (flag, fire, waves) |

**Running gag device:** the *same background* reused with a small change to show time passing
(`05` → `06`: the stones spell `1684`, then `1701` with the same character now old).

#### 3. Colour palette (measured from the frames)

![palette](styles/history-puppet/palette.png)

| Group | Colours |
|---|---|
| Sky & sea | sky cyan `#49FEFB` · sky light `#7AFCF6` · horizon haze `#CCFCFB` · cloud `#FFFFFF` · sea mid `#42897D` · sea deep `#1F5753` · shallows `#ABEDE5` |
| Land | sand `#C3BB87` · earth `#7F6D51` · grass `#4E5922` · palm green `#61703B` · stone light `#B1ACA6` · stone mid `#776D67` · stone dark `#4A3E3B` |
| Wood & interiors | wood light `#896A5D` · wood mid `#5B4338` · wood dark `#3C281D` · interior shadow `#1C0E09` · parchment `#E2D6A5` · map sea `#408C91` |
| Characters | face white `#FEFEFD` · face shadow `#B09EA0` · outline `#201C18` · costume brown `#413529` · costume tan `#776250` · costume black `#17100F` |
| Accents & mood | bandana red `#883B33` · coat magenta `#B46BA2` · coat blue `#6982A6` · sunset orange `#F07B20` · sunset gold `#EFE162` · sunset haze `#A09159` |

**Colour logic:**
- The world is **earthy and muted** (browns, tans, olive, grey stone), set against a **very bright cyan
  sky and sea**. That contrast is the look.
- Characters wear mostly browns and blacks. **One or two characters get a strong accent colour**
  (e.g. magenta coat, red bandana) so the viewer can find them instantly.
- Mood scenes recolour everything at once (sunset, fire, night), not just the sky.

#### 4. Camera and framing

- **Slow pans** across wide painted backgrounds, ending on a character.
- **Push-ins** onto a character's face for punchlines.
- **Over-the-shoulder back views** (character in foreground, looking at ships or a fort).
- **Top-down shots** for islands and maps (`07`).
- Close-ups fill the frame with a head (`01`, `13`); wide shots make characters tiny (`05`).
- Cuts are mostly hard cuts on the narration beat; whooshes/zooms for comedic transitions.

#### 5. Animation

- **Limited puppet animation:**
  - pose swaps
  - mouth swaps (lip-sync)
  - blinks
  - small squash and bounce on reactions
  - head tilts
  - arm gestures (pointing, waving, shrugging, facepalm)
- Idle characters breathe or bob slightly, so they are never frozen.
- Entrances: pop in, slide in, or a dust puff (`17`).
- Comedic timing comes from **holds**: the reaction face held 1–2 seconds before the next line.

#### 6. Effects and graphic devices (each one becomes a template)

| Device | Example frame | Use |
|---|---|---|
| Radial speed lines / burst | `16` | Punchlines, shock, chaos |
| Dust puff / impact cloud | `17` | Landings, someone thrown, entrances |
| Wanted posters / documents as props | `18` | Introducing several people at once |
| Parchment map | `19` | Where we are; routes and territory |
| Map on wall, character points at it | `20` | Planning, explaining strategy |
| Timeline infographic (white bg, character icons, years) | `21` | Summaries, "who ruled when" |
| Video-game stat screen | `22` | Comedic character intro (stats, health bar, "perks") |
| Silhouette reveal (black on white) | `23` | Introducing a famous figure dramatically |
| Same background, time passed | `05`, `06` | Years going by |
| Mood recolour (sunset, fire) | `14`, `15` | Endings, disasters, emotional beats |

#### 7. Typography

- **Chunky, hand-lettered capitals** with a thick dark outline (titles, "LINK IN THE DESCRIPTION",
  infographic labels).
- In-world text is drawn on objects: signs, posters, stones on sand.
- **For our Hindi channel:** a rounded heavy Devanagari face (e.g. **Baloo 2 ExtraBold**), white or cream
  fill with a thick `#201C18` outline, to match the outline weight of the art.

---

#### 8. Image prompts (drafts for the Gemini website)

**Style block** (pasted at the end of every prompt):

> 2D cartoon illustration for an animated history comedy. Characters: chibi proportions, about 3 heads
> tall, large round plain face with tiny dot eyes and a big expressive mouth, no nose, small mitten-like
> hands, detailed period costume with folds and texture. Thick warm-black brush outlines, flat cel
> shading with one shadow tone, no gradients on characters. Painted, textured, detailed background,
> muted earthy browns and olive against a very bright cyan sky with puffy cartoon clouds. Clean, bright
> daylight, high clarity. No text, no watermark.

**Character sheet** (one per character, on a flat background so we can cut it out):

> [Character description, e.g. "a Mughal soldier in a red-and-gold angarkha, turban, curved sword at the
> waist"], full body, standing, front view, on a plain flat light-grey background, nothing else in the
> image. + style block

**Pose / expression from the reference** (upload the approved character image with it):

> Same character, same costume, same style, now [laughing with eyes closed / shocked / pointing to the
> right / seen from behind], full body, plain flat light-grey background.

**Background:**

> [Scene, e.g. "a Mughal fort courtyard with red sandstone arches, a fountain, a few potted plants, morning
> light"], wide shot, no characters, 16:9. + style block (background part)

#### 9. Asset spec for the pipeline

| Asset | Format |
|---|---|
| Character pose | PNG, transparent background after cutout, character about 1500 px tall, feet at the bottom edge |
| Mouth shapes | Generated as expression variants first; separate mouth layers later if lip-sync needs them |
| Background | PNG/JPG, at least 2560×1440 (so the camera can pan and zoom without blur); 1080×1920 framing must also work for shorts |
| Naming | `characters/<id>/<pose>.png` (`happy`, `angry`, `shocked`, `laugh`, `point-right`, `back`…); `backgrounds/<era>/<place>.png` |

---

#### 10. Our own signature (to decide)

We keep the **formula** (simple face, detailed costume, painted still background, graphic gags), but
must not look like a copy:
- The white blank face is the reference channel's trademark.
- Their audience values "No AI" hand-made work and would call out a look-alike.

Options (pick one or combine):
- [ ] **Warm skin-tone faces** (wheat, brown) instead of white; fits Indian history.
- [ ] **Expressive eyes:** bigger round eyes with eyebrows (more readable emotions for Hindi comedy).
- [ ] **Different head shape:** oval or egg-shaped instead of round.
- [ ] **Indian palette:** saffron, terracotta, indigo, dusty gold sky instead of Caribbean cyan
      (the measured palette above is the reference; ours would shift it).
- [ ] **A recurring mascot / narrator** (e.g. a chai-wallah or a scribe) who appears in every video.

#### Frame index

| # | File | Shows |
|---|---|---|
| 01 | `01-closeup-big-laugh` | Big laugh, eyes closed, mouth wide, wig and coat detail |
| 02 | `02-closeup-two-characters` | Two characters in close-up; bandanas, beard; signs in background |
| 03 | `03-fullbody-period-costume` | Full body, coat and hat, fields and fence background |
| 04 | `04-back-view-over-sea` | Back/¾ view in the foreground, fleet in the background |
| 05 | `05-wide-still-bg-tiny-character` | Wide fort wall, tiny character, year spelled in stones |
| 06 | `06-same-bg-time-passing-gag` | Same background, years later, character now old |
| 07 | `07-top-down-island` | Top-down island with ships |
| 08 | `08-ship-deck-group` | Group on a ship deck, torch, tied-up crew |
| 09 | `09-interior-map-table-group` | Group around a map table, accent magenta coat |
| 10 | `10-interior-two-shot` | Two characters at a table, interior wood |
| 11 | `11-jail-interior` | Jail bars, stone interior |
| 12 | `12-palm-beach-medium` | Medium shot on a palm beach, worried expression |
| 13 | `13-villain-closeup` | Blackbeard close-up, lit fuse, beard detail |
| 14 | `14-sunset-mood` | Sunset palette, shipwreck, narrating gesture |
| 15 | `15-burning-town-mood` | Burning town across the water, back view |
| 16 | `16-speed-lines-reaction` | Radial speed lines, angry group reaction |
| 17 | `17-dust-puff-impact` | Dust-puff impact effect |
| 18 | `18-wanted-posters-insert` | Wanted posters introducing several characters |
| 19 | `19-parchment-map` | Parchment-style map |
| 20 | `20-map-on-wall-pointing` | Character pointing at a wall map |
| 21 | `21-timeline-infographic` | Timeline with character icons and years |
| 22 | `22-game-stat-screen-gag` | Video-game stat screen gag |
| 23 | `23-silhouette-reveal` | Black silhouette reveal |
| 24 | `24-tied-up-group` | Group shot, tied-up character, costume variety |
