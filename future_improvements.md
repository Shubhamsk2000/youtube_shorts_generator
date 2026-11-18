What limits it today
It picks clips without seeing the video. Gemini reads the transcript plus loudness and heatmap numbers. In MrBeast-style videos the best moments are often visual: someone falls into a trap, a face reacts, something explodes. A transcript of a hilarious fall might say only "oh no!", so a lot of the funniest material is invisible to the picker.
Its scores barely separate clips. On the trap video all 21 picks scored between 7.0 and 9.7. Meanwhile YouTube's "Most replayed" heat for the same clips ranges from 0.03 to 0.69. Real viewers disagree with the LLM far more than its scores suggest, and the scores are what decide the ranking.
"Funny" only shows up as loudness. The audio signals detect "loud non-speech after a line", which can't tell laughter from an explosion or music. On top of that, min_clip_s: 15 and the HOOK→TENSION→PAYOFF structure suit story clips. A lot of comedy is an 8–15 s setup, punchline and reaction, and those get stretched or dropped.
There's no feedback loop. reviews.json only records "approved", and nothing feeds back which shorts actually got views. Every run starts from zero.
Clips often feel half-baked. Each clip is one continuous stretch, so an incident that plays out over 3–4 minutes can't fit in 50 s without losing its setup or its ending. On the hunger-games video (14 picks): 5 end on a setup line instead of a payoff ("Let the hunt begin."), the approved finale stops just before its emotional ending ("This money is for my family. I grew up homeless."), and 3 run on 7–19 s past the payoff.
What I'd do, in priority order
#	Change	Why	Effort
1	Let Gemini watch the top candidates. Send each candidate as a 1 fps, 360p clip to Gemini (it accepts video) to rescore it and tag it funny, visual gag, reaction or story.	Catches the visual comedy the transcript can't see. About 10k tokens per 40 s clip, which fits the free tier for ~25 candidates.	Medium
2	Use timestamped comments. yt-dlp can pull comments; ones like "8:42 had me dying 💀" point at funny moments.	Real viewers labelling funny moments for free. Pairs well with the heatmap.	Small
3	Add a laughter/scream classifier. A small CPU audio model (YAMNet or PANNs) can tag laughter, screaming, applause and crashes separately.	TESTED 2026-11-18, NOT WORTH IT for live-action videos: on the trap and hunger-games videos, laughter peaked at 0.17 (noise level) and scream/impact peaks didn't follow the heatmap. As a control, the Minecraft video reached 0.87, so the model works: the laughter is just buried in the mix. Might still help on gaming/commentary videos, or after separating the voices from the music (needs PyTorch).	Small–medium
4	Add a comedy clip type. Allow 8–20 s clips with their own prompt: setup, punchline, reaction, ideally looping.	Short, loopable funny clips tend to rewatch well.	Small
5	Rank with real signals. Weight the ranking more on heat, comments and the video pass, and less on the LLM's own scores. Or have Gemini compare clips head to head ("A vs B, which gets more views?").	LLMs compare clips more reliably than they score them in isolation.	Small
6	Build a test set. MrBeast posts his own Shorts cut from his main videos, and those show which moments his team judged best. Run 5 videos through the pipeline and measure how many of those moments it finds.	Gives you a number to improve against instead of guessing.	Small
7	Close the loop. Pull views and retention for your uploaded shorts from the YouTube Analytics API, and put the winners into the prompt as examples.	The pipeline gets better on your channel specifically.	Medium
8	Multi-cut story shorts. Instead of one continuous stretch, build each short from 4–8 pieces of one incident (hook, stakes, 2–3 key moments, payoff, reaction), condensed to 45–58 s.	Makes every pick a complete story with a real ending. A video has 20–30 incidents, so this gives more good picks too. Details below.	Medium
9	Visual track for the whole video. Before picking, mark what happens on screen at each line (event, emotion, intensity) plus visual-only moments between the words, and merge it into the lines the picker reads. Hybrid: local SigLIP + motion tagging on the server for every video, plus short descriptions of the top flagged moments only. Supersedes #1, and do it before #8.	The picker, story mode and the ending checks finally see the silent gags, falls and explosions, instead of only rescoring picks chosen blind.	Medium

Multi-cut story shorts (#8)
The render already supports most of it. A clip decodes its full time range and plays only the kept pieces listed in edits/<clip_id>.json (that's how the dead-air jump cuts work), the captions are timed on that edited timeline, and the cold open already plays a later moment first. So a story short is a clip with a wide range (e.g. 12:00–15:30) and a keep list of the pieces. What's new is choosing the pieces.
Step 1, list the incidents: one Gemini call over the whole transcript lists every self-contained incident (the hostage taking, the night raid, the finale) with its sentence range, which can be several minutes long.
Step 2, condense each incident beat by beat into 45–58 s:
	Hook (0–3 s): the premise line, or the payoff teased first with the existing cold open
	Confirm the click (3–8 s): the line that says what's at stake
	Meat (8–40 s): 2–3 key moments, with walking, logistics and repeats cut out
	Payoff: the resolution plus the reaction after it
	Loop (optional): end on a line that echoes the hook
	Gemini returns the pieces as sentence ranges labelled by beat. Code checks that every cut lands on a sentence boundary with a pause, the total fits, and the pieces stay in the order they happened.
Step 3, editor pass: is the stitched story clear to a cold viewer, and does the payoff answer the hook?
Example: the approved hunger-games finale today must stop at "This is your money, Kevin." As a multi-cut, the fight shrinks to its key moments and there's room for "This money is for my family. I grew up homeless.", the line that makes it feel complete.
Risks and fixes:
	Cutting mid-action on screen: nudge each cut to the nearest camera cut. Reframe already detects them, and MrBeast footage cuts every 1–2 s.
	Music jumps at each cut: a short audio crossfade at each join, like the cold open already uses.
	False stories (lines stitched to suggest something that didn't happen): pieces stay in order and inside one incident, the editor pass checks for it, and a human reviews every short.
	Render cost: the whole range is decoded (3–4 min for a 55 s short). Fine on the server; later, decode only the kept pieces.
	Reframe: it analyses the whole range today; restrict it to the kept pieces.
First step: a script that runs steps 1 and 2 on hunger games and prints each incident's pieces with their text, timestamps and beats, with no pipeline changes. If the stories read as complete, wire it in as a "story" mode next to the current picker and render a few on the server to check the cuts on screen.

Visual track (#9)
Goal: the picker sees what happens on screen, not only what is said. Example of the merged lines:
	[412] (13:42) Nolan don't.   👁 Nolan leaps off the platform swinging; Kevin flinches   😱4
	[413] (13:46) Go back.
	[ v ] (13:47) (no speech)    👁 Kevin's suit bursts red; he falls back laughing         😂5
Spoken lines get a note only when something notable is visible (what is visible, emotion such as shock/fear/funny/triumph/tense/sad, intensity 1–5). Visual-only lines cover what happens between the words.
Hardware: the laptop (Ryzen 3 3250U, 4 threads, 5 GB, no usable GPU) is weaker than the server. The free-tier m7i-flex.large (2 vCPU Sapphire Rapids, 8 GB) is the best box. Its AVX-512/AMX instructions speed up int8 models with OpenVINO or onnxruntime.
What runs locally:
	When something happens (cheap, every video): zero-shot frame tagging with SigLIP/CLIP (~100–200M params, ONNX) against text labels such as "a person falling", "an explosion", "a shocked face with mouth wide open", "people celebrating", "someone covered in slime", "a person laughing". Scores are relative to the video (top few % only), like the YAMNet check. Add frame motion and cut rate, which come almost free from the frames reframe already decodes. About 2 frames per second of video; estimated 5–15 min per 35-min video on 2 vCPUs (not measured). Weakness: each frame is judged alone (it can say "explosion-like frame at 13:47", not why), and editor graphics confuse it.
	What happened (expensive): small vision-language models (SmolVLM2, Moondream2, Qwen2.5-VL-3B int4) run on CPU but describe single images, not events over time. Several seconds to tens of seconds per frame on 2 vCPUs, so hours for a whole video. Use them only on flagged moments, and only as a fallback.
	Local face-emotion models: skip. MrBeast faces look exaggerated all the time, and those models see expressions, not events.
Plan (hybrid):
	1. On the server, every video: SigLIP + motion at ~2 fps -> visual.json, attached to the nearest sentence ids, plus visual-only lines. Free, no rate limits.
	2. Top 20–30 flagged moments only: a short description. Gemini is the better choice (it already accepts video through the media: route in configs/llm.yaml; the anime app's watch.py already builds small proxies and sends them). A local small VLM is the fallback.
	3. Merge into the lines the picker and editor pass read. The ending check gets "did the action resolve on screen?" and story mode (#8) gets visual beats for its pieces.
Gemini-only variant (best quality, if the free tier allows): 360p, 1 fps proxies in ~5-min chunks, each sent with its transcript lines and ids, so Gemini attaches notes to sentence ids instead of its own timestamps, which drift over long videos. Roughly 100 tokens per second of video at low media resolution, so ~200k tokens for a 34-min video in ~7 calls (estimate; check current free-tier limits). Watch for invented events.
First step, a test like the laughter check: run SigLIP + motion on hunger games on the server (the source video is only there), then compare the top visual spikes with the 4 approved clips and moments you remember. Build it only if the spikes land on real moments. The current 3.8 GB server can run SigLIP; the m7i-flex.large is needed only for the local VLM step.
