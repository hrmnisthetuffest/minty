import { action, api, checkbox, field, h, numberInput, select, textInput } from "../lib.js";

// [section, key, label, kind, help]
const FIELDS = [
  ["audio", "voice_mode", "Voice mode", "text", "live = Gemini voice-to-voice (needs gemini key); classic = local pipeline"],
  ["audio", "live_voice", "Live voice", "text", "Gemini voice, e.g. Charon, Puck, Kore, Fenrir, Aoede"],
  ["audio", "wake_mode", "Wake mode", "text", "words = say \"Minty\"; model = openWakeWord phrase"],
  ["audio", "wake_word", "Wake word model", "text", "openWakeWord model, e.g. hey_jarvis"],
  ["audio", "wake_threshold", "Wake sensitivity threshold", "number:0.05", "Lower triggers more easily (0 to 1)"],
  ["audio", "speech_rms_threshold", "Speech volume threshold", "number:10", "Raise it in a noisy room"],
  ["audio", "silence_ms", "Silence that ends your sentence (ms)", "number:50", ""],
  ["audio", "max_wait_ms", "Give up if you say nothing (ms)", "number:100", ""],
  ["audio", "max_record_s", "Longest single request (seconds)", "number:1", ""],
  ["audio", "barge_in", "Wake word interrupts Minty while he talks", "bool", "Use a headset, or his own voice can trigger it"],
  ["audio", "input_device", "Microphone (name or number, blank = default)", "nullable", ""],
  ["audio", "output_device", "Speaker (name or number, blank = default)", "nullable", ""],
  ["tts", "engine", "Voice engine", "select:kokoro,piper,fish", "kokoro (natural), piper (lighter), fish (Fish Audio cloud voices; needs the fish key, falls back to the local voice, never used in private mode)"],
  ["tts", "fish_voice", "Fish Audio voice id", "text", "the part after /m/ in a voice's fish.audio link; blank = Fish default"],
  ["tts", "fish_model", "Fish Audio model", "text", "s2.1-pro (default), s2-pro, s1"],
  ["tts", "fish_latency", "Fish Audio latency", "select:low,balanced,normal", "balanced is a good voice-assistant default"],
  ["tts", "kokoro_voice", "Kokoro voice", "text", "am_michael, am_fenrir, bm_george, bm_fable, af_heart, af_bella"],
  ["tts", "speed", "Speaking speed", "number:0.05", "1.0 is normal"],
  ["tts", "voice_model", "Piper voice file (.onnx path, fallback)", "text", ""],
  ["tts", "minty_filter", "Deep robotic Minty voice effect", "bool", "Turn off if the CPU struggles"],
  ["stt", "provider", "Speech-to-text", "select:groq,local", "groq = cloud (fast), local = on this machine"],
  ["stt", "local_only", "Never send my voice to the cloud", "bool", ""],
  ["stt", "local_model", "Local Whisper model", "text", "tiny.en is fastest on an old CPU, base.en is better"],
  ["stt", "groq_model", "Groq Whisper model", "text", ""],
  ["safety", "mode", "Safety: ask me first", "select:careful,commits,trusted",
   "careful = before any change; commits = before anything others see or that is hard to undo; trusted = only money, logins, deleting, and your data going somewhere new"],
  ["safety", "confirm_risky", "Ask before risky actions", "bool", "Off = never ask, except money, logins, deleting and your data going somewhere new"],
  ["safety", "audit", "Keep a log of risky actions (audit/, one year)", "bool", ""],
  ["update", "repo", "GitHub repository to update from (owner/name)", "text", "blank until the project is published; then `minty update` and the daily check use it"],
  ["", "terminal_mute_key", "Mute key on the terminal display", "text", "one character, or the word space; works when nothing is being typed"],
  ["", "terminal_listen_key", "Start-listening key on the terminal display", "text", "one character, or the word space; the same as saying \"Minty\""],
];

function build(cfg, [section, key, label, kind, help]) {
  const value = (section ? cfg[section] : cfg)[key];
  if (kind === "bool") {
    const box = checkbox(value);
    return { read: () => box.checked, el: h("label", { class: "field check" }, box, h("span", {}, label), help ? h("small", {}, help) : null) };
  }
  if (kind.startsWith("select:")) {
    const input = select(kind.slice(7).split(","), value);
    return { read: () => input.value, el: field(label, input, help) };
  }
  if (kind.startsWith("number:")) {
    const input = numberInput(value, { step: kind.slice(7) });
    return {
      read: () => {
        const raw = String(input.value).trim();
        const number = Number(raw);
        // Number("") is 0: a blank field must not silently become a zero threshold.
        if (raw === "" || !Number.isFinite(number)) throw new Error(`${label} must be a number.`);
        return number;
      },
      el: field(label, input, help),
    };
  }
  const input = textInput(value ?? "");
  return { read: () => (kind === "nullable" && !input.value.trim() ? null : input.value.trim()), el: field(label, input, help) };
}

export default async function audio(main) {
  const cfg = await api("config");
  const rows = FIELDS.map((f) => ({ f, ...build(cfg, f) }));
  main.replaceChildren(
    h("h2", {}, "Voice, audio, safety & updates"),
    h("div", { class: "card" }, rows.map((r) => r.el)),
    h("div", { class: "row" },
      action("Save", async () => {
        const values = rows.map((r) => r.read());
        // Merge into the live config so settings changed elsewhere since this page loaded are kept.
        const fresh = await api("config");
        rows.forEach(({ f }, i) => { (f[0] ? fresh[f[0]] : fresh)[f[1]] = values[i]; });
        await api("config", { method: "PUT", body: fresh });
        return "Saved. Wake word and microphone changes reload the audio pipeline.";
      }, "primary"),
      action("Test voice", async () => { await api("say", { method: "POST", body: { text: "Minty online." } }); })),
  );
}
