# Speed, low-end hardware and a more human conversation: research and plan

Written 2026-10-20 for `1.0.0-alpha.13`. Part research (what the Linux kernel community, conversation science and the speech-AI
field do), part measurement (this repository, this machine, real traces), part plan (what to build, in what order, and how to know
it worked). Numbers marked **measured** were taken from this code; **published** numbers come from the sources at the end and are
other people's measurements on other hardware; **estimate** is arithmetic from the two.

## 0. The short version

1. **The whole loop is slow before any model runs.** Over seven days of real use (31 voice turns) the median time from the end of
   a request to the first spoken word was **2.45 s** and the 95th percentile **12 s**. People expect about **0.2 s** between turns
   and notice trouble past **0.8 s** (§2.2). A cascade of recogniser, model and voice cannot reach 0.2 s, but 0.8 s is reachable.
2. **A third of the median is a fixed wait.** The recogniser waits for **900 ms of silence** (`audio.silence_ms`) before it even
   starts. Humans leave about 200 ms. Ending the wait early *and being ready to take it back* (speculation, §2.2) is the cheapest big
   win and also the biggest change to how the conversation feels.
3. **The model is asked to think too much.** 68% of the output tokens of those seven days (4,881 of 7,152) were hidden reasoning. For
   a one-sentence spoken answer that is seconds of waiting for nothing.
4. **Every request carries about 2,500 tokens** (3,441 characters of rules and 15 tool schemas, even for "what time is it"), and
   only **2%** of input tokens were served from a provider's prompt cache, so the prefix is not stable. On a fast cloud model this is
   a few hundred milliseconds; on a small local model on an old laptop it is minutes.
5. **The idle machine is not idle.** In the default word-wake mode a speech recogniser runs on every stretch of speech it hears,
   and live captions re-transcribe the growing sentence once a second. Both are fine on a 5600X and punishing on a 2005 laptop.
6. **The latency data was broken until now.** Every first-token latency in every trace was stored as `"…"`: the trace writer
   treated any field with "token" in its name as a credential (fixed in this release, with a test). The first job of the plan is to
   measure properly.
7. **Same look, same features, on every computer.** Minty does not turn things off on a weaker machine. Speed comes from doing the
   same work more cheaply (less work per frame, caching, overlapping stages, smaller prompts) and from adapting *how often* work
   happens to what the machine can afford (the display spaces out its redraws when a frame is expensive, captions wait for the
   recogniser). `minty tune --lite` exists as a choice for a computer that still struggles; it is never a default and never applied
   for you. A 4 GB, 20-year-old laptop should run the same Minty, slower.

## 1. Targets

Three machine tiers (detected, §5-A) and what each should achieve. "First audio" is end of the user's speech to the first sound
of the reply with a cloud brain.

| | **T0: very old** | **T1: ordinary laptop** | **T2: modern** |
|---|---|---|---|
| Example | 2005 to 2012, 2 cores, 4 GB, no AVX2, hard disk | 2013 to 2019, 4 cores, 8 GB, AVX2, maybe a small GPU | 6+ cores, 16 GB+, maybe a discrete GPU |
| First audio, p50 | 1.5 s | 1.0 s | 0.7 s |
| First audio, p95 | 4 s | 2.5 s | 1.5 s |
| Idle CPU (display + listening) | 8% of one core | 5% | 3% |
| Resident memory, no local models | 250 MB | 250 MB | 250 MB |
| With local recogniser + voice | 700 MB | 1.2 GB | 2 GB |
| Start-up to listening | 6 s | 3 s | 1.5 s |
| Local language model | none | optional 1B | optional 3B to 8B |

Measured on this PC (Ryzen 5 5600X): importing the app 0.30 to 0.39 s and 58 MB (+12 MB for the context); building the plugin context
0.17 to 0.41 s; terminal frame 5.9 ms; idle display 3.5% of one core at 6 frames/s; speech-detector frame 0.27 ms. The author's 2012
laptop (i5-3320M, no AVX2, hard disk) measured earlier: the Kokoro voice needed 9 s for a 5 s sentence (slower than real time), Piper 0.7 s
warm; one Claude Code call 5 to 8 s against 0.7 s for Groq. Old hardware is 5 to 10 times slower on every CPU stage, so each stage's
budget has to shrink by that factor or move off the machine.

## 2. What the research says

### 2.1 How the Linux kernel gets fast, and what carries over

The kernel is the most heavily optimised C program there is, and its culture is consistent. The parts that matter here:

- **Measure the real workload, not a flattering one.** Linus Torvalds cautions that code made larger by an "optimisation" looks great
  when everything is hot in the CPU caches and loses in real use; he asks for numbers on cold caches and macro-benchmarks, not tight
  loops (a recurring theme in his mailing-list replies; secondary summary linked below). In the 2024 path-walking series he profiled
  first, found the bulk of the cost was **cache misses on a hash table**, and fixed that one thing, saying plainly what the series did
  not touch ([thread](https://lkml.iu.edu/hypermail/linux/kernel/2406.2/05594.html)).
- **The best optimisation is not doing the work.** Widely attributed to him and repeated in many threads (secondary source); the kernel's
  fast paths exist to skip work entirely (RCU lookups that take no locks, per-CPU counters that never contend).
- **Data structures and memory layout beat clever code.** "Bad programmers worry about the code. Good programmers worry about data
  structures." Smaller tables fit in cache; the pending work in that series is about hash size and cache-line layout.
- **Profile-guided builds are a measured 6 to 10%.** AutoFDO on the kernel gave a 10.6% latency improvement in a TCP request/response test and
  6.1% throughput; ThinLTO and Propeller add more ([LLVM discourse](https://discourse.llvm.org/t/optimizing-the-linux-kernel-with-autofdo-including-thinlto-and-propeller/79108)).
- **Scheduling for latency, not just fairness.** Since 6.6 the scheduler is EEVDF, which favours tasks that have not had their share
  and gives latency-sensitive tasks short deadlines without hand-tuned heuristics ([Linux Magazine](https://www.linux-magazine.com/index.php/Issues/2025/301/EEVDF)).
- **Compressed memory for small machines.** zram and zswap keep pages in compressed RAM before they go to disk; for 4 GB machines the
  advice is about 50% of RAM as zram and `vm.swappiness` around 5 to 25 ([LinuxBlog](https://linuxblog.io/linux-performance-almost-always-add-swap-part2-zram/)).
- **Real-time audio is a scheduling question.** PipeWire's real-time module asks RTKit for `SCHED_FIFO` for audio threads and sizes
  buffers with a "quantum" ([man page](https://man.archlinux.org/man/libpipewire-module-rt.7.en)).

**Rules for Minty, taken from that culture:** (1) no change without a before and after number from a trace or the harness; (2) find the
fast path and make it skip work: a request that needs no model, no tools or no transcription should do none; (3) fix the data first:
a 2,500-token prompt is a data-structure problem, not a model problem; (4) keep the hot path allocation-light and the cold path lazy
(load a model when first needed, drop it when memory is short); (5) say what a change does *not* fix; (6) never trade correctness or
safety for speed, and keep the old behaviour behind a switch until the new one has proof; (7) **never trade the look or the features for
speed by default**: a slower machine gets the same screen and the same abilities at a lower frame rate or a longer wait.

### 2.2 What makes a conversation feel like one

- **The gap.** Across ten languages the median gap between speakers is about 200 ms, within roughly 250 ms either way (Stivers et al.
  2009, [abstract](https://experts.illinois.edu/en/publications/universals-and-cultural-variation-in-turn-taking-in-conversation/)). People
  plan their reply while the other person is still talking, and start about when the turn ends.
- **What users tolerate.** Practitioner write-ups agree on rough bands: under about 500 ms feels natural, 500 ms to 1 s acceptable, past 1 s
  noticeable, past 2 s people disengage or think it is broken; 800 ms is the usual ceiling for a voice agent
  ([Picovoice](https://picovoice.ai/guide/voice-agents/voice-ux-latency-turn-taking/), [Twig](https://www.twig.so/blog/voice-ai-agents-latency-budget-800ms)).
  One 2025 observational study of nine voice-assistant users measured a mean delay of 1,366 ms.
- **Where the budget goes** (published, typical): cloud recogniser 80 to 200 ms (local 30 to 80), network 20 to 60, model first token
  80 to 400, voice first audio chunk 60 to 120, plus the **end-of-turn wait**; model first-token time is the largest source of variance
  ([Smallest.ai](https://smallest.ai/blog/designing-voice-assistants-stt-llm-tts-tools-and-latency-budget)). Streaming every stage and
  overlapping them (think while the person is finishing, speak the first sentence while the second is written) brings the total toward the
  longest single stage instead of the sum. Streaming voice synthesis alone is reported to save 300 to 600 ms.
- **End-of-turn detection is the lever.** A fixed silence timeout either cuts people off or adds dead air. Production systems layer an
  acoustic detector with a meaning check: LiveKit's 135M-parameter transformer on the transcript runs in about 50 ms on CPU and
  stretches the silence timeout when the sentence sounds unfinished, cutting accidental interruptions by 85% versus a silence detector
  alone ([LiveKit](https://livekit.com/blog/using-a-transformer-to-improve-end-of-turn-detection)); Pipecat's Smart Turn v2 judges the audio itself
  (94.8M parameters; 12 ms on a data-centre GPU, **410 ms on a 16-core CPU** for 8 s of audio) so it is too heavy for old hardware
  ([model card](https://huggingface.co/pipecat-ai/smart-turn-v2)). Minty already has a cheap version (`voice/turnjudge.py`).
- **Speculation.** Start the model's answer when the turn is *probably* over and throw it away if the person goes on. LiveKit calls it
  preemptive generation and Deepgram speculative replies; the cost is extra tokens, worse for people who speak at length
  ([LiveKit](https://docs.livekit.io/agents/build/speech/), [Deepgram](https://developers.deepgram.com/docs/voice-agent-speculative-replies)).
  Only the model runs early; the voice waits for confirmation.
- **Interruption has two layers.** Stop the sound at once, and also cancel the pending model call, voice jobs and unstarted tool calls so
  a stale answer cannot come back (Picovoice, above).
- **Acknowledgement is a patch for a latency hole.** Fillers and short acknowledgements ("mm", "let me check") lower *perceived* delay,
  mostly when the real delay is long, and should be tied to real work, short, never over the person talking, and dropped when the answer
  arrives first. Studies of fillers in voice conversation found they improve perceived response time under high delay
  ([UCF study](https://www.eecs.ucf.edu/isuelab/publications/pubs/maslychmCUI25.pdf)).
- **Full duplex.** Moshi listens and speaks in two parallel streams and manages about 200 ms in practice, with no explicit turns
  ([paper](https://arxiv.org/html/2410.00037v1)). Gemini Live is a cloud service of the same kind: speech in, speech out, barge-in,
  proactive audio ([docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api)). Minty cannot run a full-duplex model on old
  hardware, but Live mode can use one, if its wrapper does not get in the way (§3, finding F9).

### 2.3 Speech and language models that fit old machines

- **Recogniser.** Whisper pads every input to 30 s, so a 2 s request costs the same as a 30 s one. Moonshine's cost scales with the audio:
  its 27M-parameter tiny model is about 5 to 15 times faster, with 34 ms on a MacBook, **69 ms on Linux x86 against 1,141 ms for
  Whisper tiny, 237 ms on a Raspberry Pi 5 against 5,863 ms**, and lower error ([Flavors of Moonshine](https://arxiv.org/html/2509.02523v1)).
  sherpa-onnx's streaming Zipformer models (14M and 20M parameters) are designed for resource-limited and old ARM and x86 CPUs and run far
  faster than real time ([model list](https://github.com/k2-fsa/sherpa/blob/master/docs/source/onnx/pretrained_models/small-online-models.rst)).
  faster-whisper with int8 does the Small model at about 7.6 times real time on an 8-thread i7-12700K ([PyPI](https://pypi.org/project/faster-whisper/)): good on T2,
  too slow on T0.
- **Speech detection.** WebRTC VAD is the cheapest (under 5 ms); Silero is more accurate and costs about 1 ms per 30 ms chunk but is slower to
  notice that speech ended; a hangover of 500 to 800 ms and a 300 to 500 ms pre-roll are the usual settings (secondary:
  [tutorial](https://learn.traeai.com/t/ai-engineering/phases/06-speech-and-audio/14-voice-activity-detection-turn-taking)).
- **Voice.** Piper is a roughly 15M-parameter VITS model that runs in real time on a Raspberry Pi 4, with no GPU. Kokoro sounds better and
  is far heavier on CPU (measured above).
- **Local language model.** A 1B to 2B model at Q4_K_M fits a 4 GB machine; a 3B Q4 model is about 2 GB with no headroom. Llama 3.2 1B gives
  roughly 14 tokens/s on a CPU, and 15 to 30 tokens/s is the range that feels usable ([It's FOSS test](https://itsfoss.com/testing-local-llms-without-gpu)).
  The prompt matters even more than the model: prefill of a 2,500-token prompt at tens of tokens/s on an old CPU is tens of seconds,
  so a local model needs a 300 to 600 token prompt and a cached prefix.
- **First-token time.** Prefill is part of it, so shorter prompts help; prompt caching cuts first-token time by 60 to 80% on cached
  requests, and the research on prompt caches reports 20 to 70 times on CPU inference ([paper](https://arxiv.org/abs/2311.04934),
  [overview](https://flo2.com/blog/reduce-llm-latency)).

### 2.4 Python on small machines

Import cost is paid on every start; Python 3.15 adds explicit lazy imports (PEP 810, accepted 2025-11-03; a CLI start went from 104 ms to
36 ms in one measurement, [post](https://hugovk.dev/blog/2025/lazy-imports/)), and until then the same effect needs imports inside the
functions that use them. Asyncio programs benefit from freezing long-lived objects and raising the garbage collector's threshold
(from 700 to tens of thousands of allocations) once start-up is done ([Close](https://making.close.com/posts/taming-the-python-gc/)); uvloop
and orjson give further speed on Linux at the cost of two dependencies.

## 3. Where Minty stands (measured and read from the code)

**Measured on this PC**

| What | Value |
|---|---|
| Import the app / build the context | 0.30 to 0.39 s, 58 MB / 0.17 to 0.41 s, +12 MB, 757 modules |
| Import hot spots (cumulative) | config 120 ms (48 ms building the settings classes), context 104, httpx 52, numpy 43, speech detector 45 |
| Terminal frame / idle display CPU | 5.9 ms / 3.5% of one core at 6 frames/s |
| Per-frame costs | wake check 8 µs, endpointer 10 µs, speech detector 269 µs |
| Plain request prompt (defaults) | 3,441 characters of rules + 15 tools (6,518 characters) ≈ **2,489 tokens** |
| Harness budgets exceeded | prompt tokens 2,509 (budget 1,500), tool characters with everything on 58,706 (24,000), browser follow-up 7,247 (6,000) |

**Measured from seven days of traces (31 voice turns, 48 model calls)**

| What | Value |
|---|---|
| Time to first spoken word | **p50 2.45 s, p95 12.0 s**; slowest turns 10 to 20 s |
| Model calls per turn | 1.55 (a tool turn is two calls) |
| Who answered | Groq 22, Claude 9, Gemini 6, OpenRouter 3; 8 of 48 calls (17%) failed over |
| Input / output tokens | 209,499 in (≈ 4,400 per call), 7,152 out, of which **4,881 were reasoning (68%)** |
| Prompt-cache hits | **2%** of input tokens |
| Tool error rate | 22%, from a single tool that failed six times in a row |

**Findings from reading the code**

- **F1. Fixed silence window.** `audio.silence_ms = 900`; recording ends only after it, then batch transcription starts (`pipeline._record`,
  `_one_turn_inner`). Roughly 900 of the 2,450 ms.
- **F2. Transcription happens only after the end.** Local recognition and Groq both get the whole utterance at the end; the optional streaming
  recogniser (sherpa) is used for captions only, not for the final text.
- **F3. First audio waits for a whole sentence of text, then a whole sentence of speech.** `_produce` synthesises each model sentence in one
  call; the next sentence's synthesis overlaps the first's playback (good), but the first one does not start speaking until its text is
  complete and its audio finished synthesising.
- **F4. Hidden reasoning on the fast tier.** 68% of output tokens. A setting exists (`supports_reasoning_effort`, frugal mode) but the
  data says it is not low enough for spoken answers.
- **F5. The prompt is large and unstable.** 2,500 tokens for any request; the tool list changes with the request (so the cache prefix
  changes), and cache hits were 2%.
- **F6. Word wake mode transcribes ambient speech.** `TranscriptWakeDetector` collects every stretch of speech and runs the recogniser on
  it to look for the wake word (cost is zero in silence, large in a room with a TV on or a family talking).
- **F7. Live captions re-transcribe repeatedly.** Every second, up to 4 s of the growing utterance is transcribed again (`_CAPTION_EVERY`).
  On a slow CPU this competes with the real transcription.
- **F8. Local recogniser settings are fixed.** `cpu_threads=4`, `base.en`, int8, beam 1: reasonable for T2, wrong for a 2-core T0 (oversubscribed
  threads) and for machines with a GPU (never used).
- **F9. Live mode mutes the microphone while Gemini speaks** (`live_echo_guard`): frames are zeroed until the sound stops, so a person on
  speakers cannot interrupt. That is the opposite of natural conversation. It is correct on speakers without echo cancellation and
  unnecessary with headphones or echo cancellation.
- **F10. The wait notice is late.** "One moment." comes after 3 s of silence (`turn.notice_after_s`); the research says 0.8 to 1 s.
- **F11. Failover is slow.** A failed provider costs up to the first-token deadline (6 s) before the next is tried unless hedging is
  on; failures were 17% of calls.
- **F12. A broken tool is retried every time.** One tool errored six times; each cost a model round trip.
- **F13. Claude Code as the brain costs seconds per call** (a process start each time), correct as a free-tier-saving fallback, wrong as the
  fast path.
- **F14. The trace could not show any of this** (fixed).
- **F15. What is already good** and should not be redone: hedged requests, 60 s keep-alive connections, answer cache, fast paths,
  toolkit routing, memory compaction, brownout handling, a performance harness with budgets (`scripts/perf.py`), smart end-of-turn word
  rules and a small-model judge, barge-in watcher, streaming sentence pipeline.

## 4. Workstreams

Effort: S under a day, M a few days, L a week or more. Risk: how likely it is to hurt something that works. "Gain" is the expected
effect on first audio or idle cost, to be confirmed by the measurement in §6.

### A. Know the machine (hardware tiers and `minty tune`)
| ID | Change | Gain | Effort | Risk |
|---|---|---|---|---|
| A1 | `hwprobe`: CPU flags (AVX, AVX2, AVX-512, NEON), cores, RAM, swap, disk type, GPU (NVIDIA, Intel/AMD iGPU through DRM, Vulkan, OpenCL), battery. One function, one dataclass, no dependencies | foundation | S | none |
| A2 | Tier choice; `minty tune` describes the computer and how Minty adapts to it, changes nothing by default; an opt-in **lite** profile (`--lite`, undone by `--full`) for a machine that still struggles; the setup's first step only informs | T0 informed, never degraded | S | none |
| A3 | **Adapt, do not remove:** the display spaces out its redraws by how long a frame took; live captions wait for the recogniser (never back to back); later: batched redraws, cached static panels, cheaper orb frames, streaming recogniser, prompt sizes chosen by speed not by tier | idle CPU 3 to 10 times lower on T0 with the same look | M | low |
| A4 | GPU use where it exists: faster-whisper on CUDA (float16/int8), onnxruntime providers (CUDA, ROCm, OpenVINO, DirectML) for voice and VAD, llama.cpp with Vulkan for the optional local model; fall back to CPU when a provider fails | T2 recogniser 5 to 20 times faster | M | medium (driver mismatches; keep the fallback) |
| A5 | RAM-aware model loading: load on first use, unload after N idle minutes when free memory is low, never load two large voices; budget from `MemAvailable` | 4 GB machine stays out of swap | M | medium |

### B. Idle cost
| ID | Change | Gain | Effort | Risk |
|---|---|---|---|---|
| B1 | A cheap wake path that gives the same behaviour: only transcribe a stretch of speech after a small acoustic wake model or a quick first-word check agrees; keep word mode as the behaviour | removes most ambient-speech transcription with no change in what the user does | M | medium (must not miss the wake word) |
| B2 | **Done**: live captions refresh no faster than half the recogniser's time per caption (`caption_every`), so a slow machine still shows captions, more slowly, instead of running the recogniser back to back; later, a streaming recogniser for them | frees the CPU during the user's turn, same captions | S | low |
| B3 | **Done (frame rate)**: the display's redraw delay follows the measured frame cost (a third of a core at most while something moves, a twentieth while waiting; this PC unchanged); later: cache the clock and status line per second, cheaper orb when idle, re-send a sixel picture only when its pixels change | idle display 3.5% → under 1% on slow machines, same look | S | low |
| B4 | `gc.freeze()` after start-up and a higher collector threshold; `MALLOC_ARENA_MAX=2` in the service unit | fewer pauses, less memory | S | low |
| B5 | Lazy imports of the heavy and rare (`audio.vad` 45 ms, `numpy` only where used, `rich` only for the display, the whole web panel) | start-up 100 ms here, about 1 s on T0 | M | low |

### C. The turn (end of speech to first audio)
| ID | Change | Gain (est.) | Effort | Risk |
|---|---|---|---|---|
| C1 | **Done in part (alpha.18):** `audio.early_finish_ms` ends a plainly finished sentence after 600 ms instead of 900; the lengthening side already existed (turn hold and judge). Needs a real voice to tune. Original plan: **Adaptive end-of-speech wait**: 350 to 450 ms when the words look finished (punctuation, a complete clause, an answer to a question just asked), up to 1.2 s when they trail off; today's rules and judge already decide that, so use them to *shorten* the wait as well as to extend it | 400 to 550 ms | M | medium (cutting people off; ship behind a setting, on by default only after the A/B) |
| C2 | **Done in part (alpha.18):** the recognition is speculative (`audio.speculative_stt`); starting the model early is still to do. Original plan: **Speculative turn**: at about 350 ms of silence start transcription of what was said so far and, when the words look complete, start the model; if speech resumes, cancel both. Only the model runs early, the voice waits | 300 to 500 ms more | L | medium |
| C3 | Streaming recogniser for the final text where a small streaming model is installed (sherpa Zipformer 20M or Moonshine), so the transcript is ready at the end of the speech | 100 to 600 ms (T0: seconds) | M | medium |
| C4 | **Done in part (alpha.18):** `turn.first_clause_words` (streaming audio out of Piper is still to do). Original plan: **First-chunk voice**: cut the first sentence at the first clause break once it has about 6 to 10 words, and synthesise that at once; stream audio chunks from Piper as they are made | 200 to 600 ms | M | low |
| C5 | **Done as an opt-in (alpha.18):** `router.fast_reasoning`, off until measured. Original plan: Reasoning off or minimal on the fast tier when the reply is a sentence or two (cap reasoning tokens, set effort low); keep it for thinking mode | 0.5 to 3 s on reasoning models | S | low (A/B on correctness) |
| C6 | Move the wait notice from 3 s to about 0.9 s and make it varied and short ("mm, one sec", "checking"); never when the first words are already arriving | perceived delay | S | low |
| C7 | **Done (alpha.17)**: fast paths first: time, date, maths, conversions, facts, system facts, timers, reminders, volume and media never reach the model; [docs/instant-answers.md](instant-answers.md) lists them. Later: notes, and more spoken commands as they are measured | 2.4 s to under 1 ms on those turns | M | low |

### D. The model call
| ID | Change | Gain | Effort | Risk |
|---|---|---|---|---|
| D1 | **Prompt diet**: three prompt sizes (full, lite, tiny) chosen by tier and request; tiny is about 400 tokens (rules in one paragraph, 3 to 5 tools); drop tool descriptions the model never uses; target the harness budget of 1,500 for full | first token 100 to 300 ms (cloud); minutes saved on a local model | M | medium (answer quality; A/B with the prompt harness) |
| D2 | **Stable prefix**: rules, then a fixed tool core, then the changing parts (time, memory, matched tools) last, so providers that cache prefixes can; measure the hit rate (2% today) | cached first-token time | M | low |
| D3 | Provider order from measured first-token time per provider, per time of day, not only from limits; skip a provider that failed twice in the last minute | removes most of the 17% failovers' delay | S | low |
| D4 | Circuit breaker for tools: after N identical failures in a session, stop offering that tool and tell the user once | removes round trips | S | low |
| D5 | Local model for the lowest tier: a 1B model (Q4_K_M) with a cached 400-token prefix as the offline and "no keys" brain, behind `local_llm` (exists, experimental) | works without a cloud key | L | medium |
| D6 | Claude Code only where its quality is needed (thinking mode, explicit request); the fast path is a free fast provider | seconds on those turns | S | low (the ladder is a setting) |

### E. Conversation feel (both modes)
| ID | Change | Notes | Effort | Risk |
|---|---|---|---|---|
| E1 | **Classic mode**: C1 to C4 and C6 together are the feel change; add a short *back-channel* ("mm-hm") only while the person speaks for long and pauses (never mid-word), off by default | measure interruption rate | L | medium |
| E2 | **Classic mode**: two-layer barge-in (exists in part): on interruption stop the sound, cancel the model, the voice job and unstarted tool calls, and keep what was not said for "go on" | already half built | M | low |
| E3 | **Live mode**: replace the mute-while-speaking guard with echo-aware barge-in: on headphones or with echo cancellation on (`audio.aec`) send the microphone as is so the user can interrupt; on bare speakers keep the guard but let a loud, sustained voice cut through (threshold above the room's echo level) | fixes F9 | M | medium (echo loops on speakers: keep the guard as the default there) |
| E4 | Live mode: raise the idle timeout while a conversation is active and show it in the display ("listening on"), so it does not hang up mid-thought | feel | S | low |
| E5 | Match the person: reply length and pace follow how they speak (exists as options); make the short, one-sentence default stronger for questions and let "tell me more" open it up | feel | S | low |
| E6 | Say less before the answer: no "Sure!", no restating the question (the prompt rules say so; verify with a trace metric: words before the first fact) | feel | S | low |
| E7 | Follow-up window with a visible cue and a short earcon option so people know the microphone is still open | feel | S | low |
| E8 | A **conversation bench** (§6) that plays recorded turns against the pipeline with fake speech and fake models, to measure the gap and the interruption behaviour without a person in the room | foundation | M | none |

### F. Operating system settings (a guide, plus a checker)
`minty tune --system` prints, never applies without asking: zram at 50% of RAM and `vm.swappiness=10` for 4 GB machines;
`MALLOC_ARENA_MAX=2`; the CPU governor and a note on turbo; a systemd user slice with `CPUWeight` and `MemoryHigh` so a heavy
browser job cannot starve the audio thread; `Nice=-5` or a PipeWire real-time rule for the audio thread (RTKit) and a smaller quantum;
disabling compositor effects on very old GPUs; moving the model cache to the faster disk. Each line says what it changes and how
to undo it.

### G. Build-level (optional, later)
Python 3.12+ everywhere, `PYTHONOPTIMIZE` has no effect worth the risk; PGO builds of the interpreter are 5 to 10% (the kernel numbers
above are the same technique) but are a distribution matter, not a code one; uvloop and orjson once the dependency cost is accepted.

## 4a. Should the project be rewritten in Rust?

**No, not as a whole, and not for speed.** The numbers:

- Size: 425 Python files, about 55,000 lines, 380 test files. A rewrite is months of work, and every plugin, the web panel, the Telegram
  bridge, Playwright browsing and the test suite would have to be redone or bridged.
- Where the time goes (§3): the 2.45 s median is a 0.9 s silence wait, a network model call, a speech recogniser and a voice. The
  recogniser, the voice, the speech detector and the local model already run in native code (ONNX Runtime, CTranslate2, llama.cpp,
  numpy). The Python code between them costs **16 µs per 80 ms frame while listening (0.02% of one core)** and **1.8 ms to prepare a
  request**, against 2,450 ms. A perfect rewrite would improve the median by about 0.1%.
- What Rust *would* improve: start-up (0.3 s to about 0.02 s), idle memory (58 MB to roughly 10 to 15 MB for the same features), the
  terminal frame (5.9 ms of Rich layout to well under 1 ms) and a single binary to distribute. Those are real, and they are small next
  to the models' own memory (hundreds of MB) and the 4 GB target; the display cost can be cut 5 to 10 times in Python by caching, which
  needs no rewrite.
- What it would cost: contributors (this is an open-source project; most people who would help read Python), the ecosystem (pydantic,
  FastAPI, Rich, Playwright, MCP, the speech and language libraries are Python-first), and months in which nothing new ships.

**What to do instead**, in this order: (1) fix the big costs, which are not language costs (§4 C, D); (2) use native libraries that already
exist for the heavy work, and the hardware (GPU providers, int8); (3) cache and batch the Python that runs often (display, prompt
building); (4) only where a profile of a real run shows Python itself above about 5% of a core, move *that one function* into a small
native extension (PyO3 for Rust, or Cython and mypyc) and keep everything else; (5) later, Python 3.15's lazy imports for start-up. A
Rust core becomes worth discussing only if the goal changes to a tiny, always-on, no-Python appliance, and even then the voice and the
models would stay as they are.

## 5. Order of work

**Status (2026-10-20):** done: instant answers and commands (C7), recognition during the pause, early finish, first-clause speech (C1, C2 and C4 in part), the per-stage trace report (M1 in part), the trace fix, this plan, `minty tune` (describes, adapts nothing visible, `--lite` and `--full` as
opt-in choices; A1, A2), the display and the captions adapting their speed to the machine (A3 in part, B2, B3 in part), the start-up
GC tuning (B4), the memory-arena setting (F) and the tool circuit breaker (D4). Everything else below is still to do.

1. **Release 13 (this one).** Fix the trace masking bug; make the trace report robust; this plan; remove the colour strip.
2. **Milestone M1, measure.** `minty bench` (software overhead, fake models) and a per-turn latency breakdown in the trace
   (`silence_wait`, `stt`, `prompt_prep`, `llm_first_token`, `tts_first_audio`, `play_start`), reported by `trace_report` with p50/p95
   by mode; the tier simulator (§6). Without these nothing after this can be proven.
3. **Milestone M2, the cheap wins (A1 to A3, B1 to B4, C5, C6, D3, D4).** Each is small and switchable; together they should cut the
   median first-audio time by roughly a quarter on this PC and make T0 usable.
4. **Milestone M3, the turn (C1, C3, C4, C7, D1, D2).** The first real latency changes; A/B each against M1's numbers.
5. **Milestone M4, conversation feel (C2, E1 to E7).** Speculation and the Live-mode barge-in fix, then the feel changes that need a
   person to judge. Real-voice testing is mandatory here; nothing in this plan was tried with a real voice.
6. **Milestone M5, the lowest tier (A4, A5, D5, F).** GPU use, memory-aware loading, the local fallback model.

## 6. How to know it worked

- **A trace for every stage** with real numbers, reported as p50 and p95 per mode and tier. Gate: a change that makes p50 or p95 worse
  by more than 5% needs a reason in the change log.
- **The performance harness** (`scripts/perf.py`) gains the new budgets: start-up time and memory per tier, idle CPU with the display
  and the wake method, the prompt size per prompt size, and the conversation bench's gap. The three budgets already over (prompt
  tokens, tool characters, browser follow-up) are fixed or consciously raised.
- **Tier simulation on one machine**: `taskset -c 0,1` to two cores, `systemd-run --user --scope -p MemoryMax=3G` to 3 GB, the
  `cpupower frequency-set` cap or `cpulimit` to roughly a fifth of a modern core, a disk read limit with `systemd-run -p IOReadBandwidthMax`,
  and `--init` with the AVX2 flag hidden (`GLIBC_TUNABLES=glibc.cpu.hwcaps=-AVX2_Usable` for libraries that honour it, or an onnxruntime
  build flag) to approximate the old laptop. It is an approximation, not a substitute for running on the real laptop.
- **A/B for anything that changes what is said or when**: the same recorded turns with the old and new setting, compared on the gap, on
  accidental cut-offs (the user continued within 2 s of the answer starting), and on correctness. The Wave-16 prompt harness method
  (paired items, sign test, a blind judge for wording) applies unchanged.
- **People.** The conversation-feel items end with a person saying whether it feels better. Keep a short list of ten scripted
  exchanges (a question, a command, a correction, an interruption, a thinking pause, a long request, a joke, a follow-up, silence, noise)
  and run it by hand before and after.

## 7. Risks and what is not known

- The 0.8 s ceiling and the 200 ms gap are for human-to-human and well-built cloud agents; a free-tier cascade will sit near 1 s at best.
- Cutting the silence window early will interrupt some people. That is why C1 ships behind a setting and why the judge and the word rules stay.
- Speculation spends tokens (and free-tier quota). Count them; turn it off in privacy mode and under a brownout.
- Smaller prompts can lower answer quality; every size is A/B-tested before it is a default.
- GPU paths depend on drivers (a version mismatch between the kernel module and the libraries made kitty fail to start on this PC today). Every GPU
  path needs a CPU fallback that is tried automatically.
- Published speech-model speeds are on other hardware; the T0 numbers above are estimates until the models run on a machine like it.
- The Linus Torvalds quotes marked secondary were read from summaries, not from the original messages; the path-walking thread was read directly.
- Nothing here has been tried with a real voice or on a 4 GB machine yet. The plan's first milestone exists so that this can change.

## Sources

- Linus Torvalds, path walking optimisations, LKML 2024: <https://lkml.iu.edu/hypermail/linux/kernel/2406.2/05594.html>
- Kernel AutoFDO, ThinLTO and Propeller: <https://discourse.llvm.org/t/optimizing-the-linux-kernel-with-autofdo-including-thinlto-and-propeller/79108>
- EEVDF scheduler in Linux 6.6: <https://www.linux-magazine.com/index.php/Issues/2025/301/EEVDF>
- zram and swappiness for small machines: <https://linuxblog.io/linux-performance-almost-always-add-swap-part2-zram/>
- PipeWire real-time module: <https://man.archlinux.org/man/libpipewire-module-rt.7.en>
- Stivers et al., universals in turn-taking: <https://experts.illinois.edu/en/publications/universals-and-cultural-variation-in-turn-taking-in-conversation/>
- Picovoice, voice UX, latency, turn-taking and barge-in: <https://picovoice.ai/guide/voice-agents/voice-ux-latency-turn-taking/>
- Twig, the 800 ms rule: <https://www.twig.so/blog/voice-ai-agents-latency-budget-800ms>
- Smallest.ai, voice assistant latency budget: <https://smallest.ai/blog/designing-voice-assistants-stt-llm-tts-tools-and-latency-budget>
- LiveKit, end-of-turn transformer: <https://livekit.com/blog/using-a-transformer-to-improve-end-of-turn-detection>
- Pipecat Smart Turn v2: <https://huggingface.co/pipecat-ai/smart-turn-v2>
- LiveKit preemptive generation: <https://docs.livekit.io/agents/build/speech/>; Deepgram speculative replies: <https://developers.deepgram.com/docs/voice-agent-speculative-replies>
- Fillers and perceived latency in voice conversation: <https://www.eecs.ucf.edu/isuelab/publications/pubs/maslychmCUI25.pdf>
- Moshi, full-duplex dialogue: <https://arxiv.org/html/2410.00037v1>
- Gemini Live API: <https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api>
- Moonshine: <https://arxiv.org/html/2509.02523v1>; sherpa-onnx small streaming models: <https://github.com/k2-fsa/sherpa/blob/master/docs/source/onnx/pretrained_models/small-online-models.rst>
- faster-whisper: <https://pypi.org/project/faster-whisper/>
- Voice activity detection and end-of-speech tuning (secondary): <https://learn.traeai.com/t/ai-engineering/phases/06-speech-and-audio/14-voice-activity-detection-turn-taking>
- Running small language models on CPUs: <https://itsfoss.com/testing-local-llms-without-gpu>
- Prompt caching: <https://arxiv.org/abs/2311.04934>; reducing LLM latency: <https://flo2.com/blog/reduce-llm-latency>
- PEP 810, explicit lazy imports: <https://peps.python.org/pep-0810>; measurements: <https://hugovk.dev/blog/2025/lazy-imports/>
- Taming the Python garbage collector: <https://making.close.com/posts/taming-the-python-gc/>
