"""The core API behind the panel's studio page: the catalogue, the next clips to load ahead, a shuffle, preferences, and the clips and
poster frames themselves (with byte ranges, so a browser can start playing at once and seek). Only clips that are in the catalogue and
inside the library folder can be fetched."""
import random
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.context import build_context
from minty_core.studio import catalog as cat
from minty_core.studio.assets import AssetLibrary

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


def real_clip(path, hue=0, seconds=3):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=360x640:rate=24:duration={seconds}", "-vf", f"hue=h={hue}",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)], check=True)
    return path


@pytest.fixture
def world(tmp_path):
    ctx = build_context(tmp_path / "cfg")
    lib = AssetLibrary(tmp_path / "cfg" / "studio" / "library")
    catalog = cat.Catalog(lib, min_clips=1)
    ids = {}
    for category, hue in (("nature", 0), ("city", 90), ("space", 180)):
        for i in range(2):
            a = lib.add(real_clip(tmp_path / f"{category}{i}.mp4", hue + i * 20), "video", "cc0", source=f"test {category} {i}",
                        attribution="Video: NASA" if category == "space" else "")
            catalog.tag(a, category, provider="test", external_id=f"{category}{i}")
            ids[f"{category}{i}"] = a.id
    voice = tmp_path / "v.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=1", str(voice)], check=True)
    ids["voice"] = lib.add(voice, "voice", "generated", source="test").id
    prefer_calls = []

    def prefer(args):
        prefer_calls.append(args)
        return "Error: nope" if args.get("choice") == "sideways" else f"{args.get('category') or args.get('asset')} is now {args.get('choice')}."

    ctx.services["studio_player"] = SimpleNamespace(catalog=catalog, lib=lib, thumbs=tmp_path / "cfg" / "studio" / "thumbs", prefer=prefer,
                                                    now=lambda: datetime(2026, 11, 2, 9, 0))
    client = TestClient(create_app(ctx))
    return SimpleNamespace(client=client, auth={"Authorization": f"Bearer {ctx.token}"}, ids=ids, catalog=catalog, lib=lib, ctx=ctx,
                           prefer_calls=prefer_calls, tmp=tmp_path)


def test_every_studio_route_needs_the_token(world):
    for method, path in (("get", "/studio/catalog"), ("get", "/studio/next"), ("post", "/studio/shuffle"), ("get", f"/studio/clip/{world.ids['nature0']}"),
                         ("get", f"/studio/thumb/{world.ids['nature0']}")):
        assert getattr(world.client, method)(path).status_code == 401, path


def test_without_the_studio_there_is_nothing_to_show(tmp_path):
    ctx = build_context(tmp_path)
    ctx.services.pop("studio_player", None)  # the studio plugin is on by default; here it is not
    c = TestClient(create_app(ctx))
    assert c.get("/studio/catalog", headers={"Authorization": f"Bearer {ctx.token}"}).status_code == 404


def test_the_catalogue_lists_styles_todays_clip_and_the_clips(world):
    got = world.client.get("/studio/catalog", headers=world.auth).json()
    styles = {s["key"]: s for s in got["styles"]}
    assert styles["nature"]["count"] == 2 and styles["parkour"]["count"] == 0 and styles["parkour"]["own_only"] is True
    assert got["today"]["category"] in ("nature", "city", "space") and isinstance(got["today"]["id"], int)
    assert got["mode"] == "daily" and len(got["clips"]) == 6 and world.ids["voice"] not in {c["id"] for c in got["clips"]}
    space = next(c for c in got["clips"] if c["category"] == "space")
    assert space["credit"] == "Video: NASA" and space["favourite"] is False
    assert "path" not in space and all("/" not in str(v) for v in space.values() if isinstance(v, str) and v != space["credit"])  # no file paths leave the core


def test_looking_does_not_use_a_clip_up(world):
    world.client.get("/studio/catalog", headers=world.auth)
    world.client.get("/studio/next?count=3", headers=world.auth)
    assert all(c.plays == 0 for c in world.catalog.clips())


def test_next_gives_a_few_clips_to_load_ahead_and_never_many(world):
    got = world.client.get("/studio/next?count=3", headers=world.auth).json()
    assert 1 <= len(got) <= 2 and len({g["id"] for g in got}) == len(got)  # two clips per style in this catalogue
    assert len(world.client.get("/studio/next?count=50", headers=world.auth).json()) <= 3
    assert world.client.get("/studio/next?count=zero", headers=world.auth).status_code == 422


def test_shuffle_moves_to_another_style_and_remembers_it(world):
    first = world.client.get("/studio/catalog", headers=world.auth).json()["today"]["category"]
    world.catalog.choose(now=datetime(2026, 11, 2, 9, 0), rng=random.Random(1))  # today's style is in use
    out = world.client.post("/studio/shuffle", headers=world.auth).json()
    assert out["category"] != first and isinstance(out["id"], int)
    assert world.client.get("/studio/catalog", headers=world.auth).json()["today"]["category"] == out["category"]


def test_shuffle_with_nothing_to_show_says_so(tmp_path):
    ctx = build_context(tmp_path)
    lib = AssetLibrary(tmp_path / "lib")
    ctx.services["studio_player"] = SimpleNamespace(catalog=cat.Catalog(lib), lib=lib, thumbs=tmp_path / "t", prefer=lambda a: "", now=datetime.now)
    r = TestClient(create_app(ctx)).post("/studio/shuffle", headers={"Authorization": f"Bearer {ctx.token}"})
    assert r.status_code == 409 and "no clips" in r.json()["detail"].lower()


def test_preferences_go_through_the_same_rules_as_the_tool(world):
    ok = world.client.post("/studio/prefer", headers=world.auth, json={"category": "city", "choice": "off"})
    assert ok.status_code == 200 and "city is now off" in ok.json()["message"]
    bad = world.client.post("/studio/prefer", headers=world.auth, json={"category": "city", "choice": "sideways"})
    assert bad.status_code == 422 and "nope" in bad.json()["detail"]
    assert world.prefer_calls[0] == {"category": "city", "choice": "off"}


def test_a_clip_is_served_whole_and_in_ranges(world):
    cid = world.ids["nature0"]
    whole = world.client.get(f"/studio/clip/{cid}", headers=world.auth)
    assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4" and whole.headers["accept-ranges"] == "bytes"
    assert whole.content[4:8] == b"ftyp"
    part = world.client.get(f"/studio/clip/{cid}", headers={**world.auth, "Range": "bytes=0-99"})
    assert part.status_code == 206 and part.headers["content-range"].startswith("bytes 0-99/") and len(part.content) == 100
    assert part.content == whole.content[:100]


def test_only_clips_in_the_catalogue_can_be_fetched(world):
    for bad in (world.ids["voice"], 99999):
        assert world.client.get(f"/studio/clip/{bad}", headers=world.auth).status_code == 404
        assert world.client.get(f"/studio/thumb/{bad}", headers=world.auth).status_code == 404
    assert world.client.get("/studio/clip/abc", headers=world.auth).status_code == 422
    assert world.client.get("/studio/clip/-1", headers=world.auth).status_code in (404, 422)


def test_a_record_that_points_outside_the_library_is_never_served(world):
    secret = world.tmp / "secret.txt"
    secret.write_text("TOP SECRET")
    a = world.lib.get(world.ids["nature1"])
    with world.lib._lock:
        world.lib._db.execute("UPDATE assets SET path=? WHERE id=?", (str(secret), a.id))
        world.lib._db.commit()
    assert world.client.get(f"/studio/clip/{a.id}", headers=world.auth).status_code == 404
    assert world.client.get(f"/studio/thumb/{a.id}", headers=world.auth).status_code == 404


def test_a_clip_removed_from_the_disk_is_a_404_not_a_crash(world):
    Path(world.lib.get(world.ids["city0"]).path).unlink()
    assert world.client.get(f"/studio/clip/{world.ids['city0']}", headers=world.auth).status_code == 404


def test_a_poster_frame_is_made_once_and_is_a_small_picture(world, monkeypatch):
    from minty_core.studio import media

    made = []
    real = media.poster

    async def counting(src, dst, width=360):
        made.append(dst)
        return await real(src, dst, width)

    monkeypatch.setattr(media, "poster", counting)
    r = world.client.get(f"/studio/thumb/{world.ids['space0']}", headers=world.auth)
    again = world.client.get(f"/studio/thumb/{world.ids['space0']}", headers=world.auth)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff" and len(r.content) < 40_000
    assert again.content == r.content and len(made) == 1
