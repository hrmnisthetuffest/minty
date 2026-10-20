"""Manual hardware check: record 3 s, play it back, transcribe it, then speak. Prints timings."""
import asyncio
import time

from minty_core.audio.devices import SoundDeviceInput, SoundDevicePlayer
from minty_core.audio.stt import DynamicSTT
from minty_core.audio.tts import ConfiguredTTS
from minty_core.audio.types import FRAME_S, SAMPLE_RATE, Audio
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.paths import config_dir


async def main() -> None:
    store = ConfigStore(config_dir())
    store.load()
    secrets = SecretsStore(config_dir())
    mic, player = SoundDeviceInput(store), SoundDevicePlayer(store)
    print("Recording 3 seconds - say something...")
    await mic.start()
    frames = [await mic.read() for _ in range(int(3 / FRAME_S))]
    await mic.stop()
    pcm = b"".join(frames)
    print("Playing it back...")
    await player.play(Audio(pcm, SAMPLE_RATE))
    print("Transcribing...")
    t0 = time.monotonic()
    text = await DynamicSTT(store, secrets).transcribe(pcm)
    print(f"  {text!r}  ({time.monotonic() - t0:.1f}s)")
    print("Speaking...")
    t0 = time.monotonic()
    audio = await ConfiguredTTS(store).synthesize("Minty online.")
    print(f"  synthesised in {time.monotonic() - t0:.1f}s")
    await player.play(audio)


if __name__ == "__main__":
    asyncio.run(main())
