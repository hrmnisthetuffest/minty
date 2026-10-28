"""What the studio accepts as media is decided by the bytes in the file, never by its name, and ffmpeg may read only plain files.

A playlist, a concat list or a document named `clip.mp4` is not footage. Left in the library it would reach ffmpeg's playlist
demuxers (which can name other files and addresses) and, worse, could be posted: so it never gets in.
"""
import shutil
import subprocess

import pytest

from minty_core.studio import fetch, media
from minty_core.studio.assets import AssetError, AssetLibrary

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")

HLS = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:5.0,\nfile:///etc/hostname\n#EXT-X-ENDLIST\n"
CONCAT = b"ffconcat version 1.0\nfile '/etc/hostname'\n"


def gen(path, *args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, str(path)], check=True)
    return path


@pytest.fixture
def made(tmp_path):
    return {
        ".mp4": gen(tmp_path / "a.mp4", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=1", "-pix_fmt", "yuv420p"),
        ".wav": gen(tmp_path / "a.wav", "-f", "lavfi", "-i", "sine=frequency=300:duration=1"),
        ".mp3": gen(tmp_path / "a.mp3", "-f", "lavfi", "-i", "sine=frequency=300:duration=1"),
        ".jpg": gen(tmp_path / "a.jpg", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1:duration=1", "-frames:v", "1"),
        ".png": gen(tmp_path / "a.png", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1:duration=1", "-frames:v", "1"),
        ".mkv": gen(tmp_path / "a.mkv", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5:duration=1", "-pix_fmt", "yuv420p"),
        ".ogg": gen(tmp_path / "a.ogg", "-f", "lavfi", "-i", "sine=frequency=300:duration=1"),
    }


def test_real_media_is_recognised_by_its_bytes_whatever_it_is_called(made, tmp_path):
    for ext, path in made.items():
        disguised = tmp_path / f"called-this.m3u8"
        disguised.write_bytes(path.read_bytes())
        assert media.sniff(path) == ext, ext
        assert media.sniff(disguised) == ext, ext


@pytest.mark.parametrize("data", [HLS, CONCAT, b"[playlist]\nFile1=http://x/y\n", b"<html>hi</html>", b"%PDF-1.7 ....", b"", b"\x00" * 64,
                                  b"secret = 'hunter2'\n", b"#EXTM3U"])
def test_playlists_documents_and_junk_are_not_media(tmp_path, data):
    f = tmp_path / "clip.mp4"
    f.write_bytes(data)
    assert media.sniff(f) is None


async def test_probe_refuses_a_playlist_named_like_a_video(tmp_path):
    for name in ("clip.mp4", "clip.m3u8", "clip.ffconcat"):
        f = tmp_path / name
        f.write_bytes(HLS if name != "clip.ffconcat" else CONCAT)
        with pytest.raises(media.MediaError, match="not a media file"):
            await media.probe(f)


async def test_every_ffmpeg_input_may_only_be_a_plain_file(made, monkeypatch):
    seen = []

    async def spy(argv, timeout=300.0, cwd=None):
        seen.append(list(argv))
        return '{"streams": [], "format": {}}' if argv[0] == "ffprobe" else ""

    monkeypatch.setattr(media, "_run", spy)
    path = made[".mp4"]
    try:
        await media.probe(path)
    except media.MediaError:
        pass  # the spy returns no streams; only the command line matters here
    await media.mean_volume(path)
    await media.brightest_frame(path)
    with pytest.raises(media.MediaError):
        await media.frame_hash(path, 0.5)
    assert len(seen) >= 4
    for argv in seen:
        for i, word in enumerate(argv):
            if word == "-i" or (argv[0] == "ffprobe" and i == len(argv) - 1):
                before = argv[:i]
                assert "-protocol_whitelist" in before and before[before.index("-protocol_whitelist") + 1] == "file", argv


@pytest.mark.parametrize("data", [HLS, CONCAT, b"just text, not a video"])
def test_the_library_refuses_non_media_and_copies_nothing(tmp_path, data):
    lib = AssetLibrary(tmp_path / "lib")
    f = tmp_path / "notes.mp4"
    f.write_bytes(data)
    with pytest.raises(AssetError, match="not a video, music or picture file"):
        lib.add(f, "video", "owned", source="mine")
    assert not [p for p in (tmp_path / "lib").rglob("*") if p.is_file() and p.suffix != ".sqlite" and "sqlite" not in p.name]
    assert lib.list() == []


def test_a_kept_file_gets_the_extension_its_bytes_deserve(made, tmp_path):
    lib = AssetLibrary(tmp_path / "lib")
    odd = tmp_path / "my clip (final)!.m3u8"
    odd.write_bytes(made[".mp4"].read_bytes())
    asset = lib.add(odd, "video", "owned", source="mine")
    stored = tmp_path / "lib" / "video" / asset.path.rsplit("/", 1)[-1]
    assert asset.path.endswith(".mp4") and "m3u8" not in asset.path and " " not in asset.path and "!" not in asset.path
    assert stored.is_file() and media.sniff(stored) == ".mp4"


def test_a_picture_is_not_accepted_as_footage_or_the_other_way_round(made, tmp_path):
    lib = AssetLibrary(tmp_path / "lib")
    with pytest.raises(AssetError, match="a picture"):
        lib.add(made[".jpg"], "video", "owned", source="mine")
    with pytest.raises(AssetError, match="not a picture"):
        lib.add(made[".mp4"], "image", "owned", source="mine")
    with pytest.raises(AssetError, match="not music"):
        lib.add(made[".png"], "music", "owned", source="mine")


async def test_a_download_that_is_a_playlist_is_refused_before_it_is_kept(tmp_path, monkeypatch):
    lib = AssetLibrary(tmp_path / "lib")

    async def fake_download(url, expect):
        return HLS

    monkeypatch.setattr(fetch, "_download", fake_download)
    with pytest.raises(AssetError):
        await fetch.fetch_url(lib, "https://example.com/stream.m3u8", "video", "cc0", source="x")
    assert lib.list() == []
