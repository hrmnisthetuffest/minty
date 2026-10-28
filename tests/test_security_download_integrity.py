"""The voice models are large files from the internet that Minty loads into its own process: each is checked against a pinned
SHA-256 before it is kept, and a copy already on disk that does not match is replaced, not trusted."""
import contextlib
import hashlib

import pytest

from minty_core.onboard import env

GOOD = b"model-bytes-" * 1000
NAME = "kokoro-v1.0.onnx"


class FakeResponse:
    def __init__(self, data):
        self.data, self.headers = data, {"content-length": str(len(data))}

    def raise_for_status(self):
        pass

    def iter_bytes(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i:i + n]


@pytest.fixture
def served(monkeypatch):
    """httpx.stream answering with whatever `served.data` holds; counts the downloads."""
    import httpx

    state = {"data": GOOD, "calls": 0}

    @contextlib.contextmanager
    def stream(method, url, **kw):
        state["calls"] += 1
        yield FakeResponse(state["data"])

    monkeypatch.setattr(httpx, "stream", stream)
    monkeypatch.setitem(env.VOICE_SHA256, NAME, hashlib.sha256(GOOD).hexdigest())
    return state


def test_every_voice_file_has_a_pinned_digest():
    wanted = {name for files in env.VOICE_FILES.values() for _url, name in files}
    assert wanted <= set(env.VOICE_SHA256)
    assert all(len(v) == 64 and int(v, 16) >= 0 for v in env.VOICE_SHA256.values())


def test_a_good_download_is_kept(tmp_path, served):
    ok, msg = env.download("https://example.com/" + NAME, tmp_path / NAME)
    assert ok and (tmp_path / NAME).read_bytes() == GOOD


def test_a_download_that_does_not_match_is_thrown_away(tmp_path, served):
    served["data"] = GOOD[:-1] + b"X"
    ok, msg = env.download("https://example.com/" + NAME, tmp_path / NAME)
    assert not ok and "checksum" in msg
    assert list(tmp_path.iterdir()) == []  # neither the file nor a .part is left behind


def test_a_copy_on_disk_that_does_not_match_is_replaced(tmp_path, served):
    (tmp_path / NAME).write_bytes(b"tampered")
    ok, msg = env.download("https://example.com/" + NAME, tmp_path / NAME)
    assert ok and (tmp_path / NAME).read_bytes() == GOOD and served["calls"] == 1


def test_a_matching_copy_is_not_downloaded_again(tmp_path, served):
    (tmp_path / NAME).write_bytes(GOOD)
    ok, msg = env.download("https://example.com/" + NAME, tmp_path / NAME)
    assert ok and "already there" in msg and served["calls"] == 0


def test_a_file_with_no_pinned_digest_is_still_downloaded_as_before(tmp_path, served):
    ok, _ = env.download("https://example.com/other.bin", tmp_path / "other.bin")
    assert ok and (tmp_path / "other.bin").read_bytes() == GOOD
