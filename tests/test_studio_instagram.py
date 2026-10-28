"""The Instagram Graph API client, against a pretend server. (Not run against a real account.)"""
import json
from pathlib import Path

import httpx
import pytest

from minty_core.studio.instagram import Instagram, InstagramError

VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000


class Meta:
    """A pretend Instagram: records every request and answers like the documented flow."""

    def __init__(self, statuses=("IN_PROGRESS", "FINISHED"), fail_at=None, publish_status=200):
        self.requests, self.statuses, self.fail_at, self.publish_status = [], list(statuses), fail_at, publish_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, body = str(request.url), request.content
        self.requests.append((request.method, url, dict(request.headers), body))
        path = request.url.path
        if self.fail_at and self.fail_at in path:
            return httpx.Response(400, json={"error": {"message": "Invalid parameter token=SECRET-TOKEN", "code": 100}})
        if request.url.host == "rupload.facebook.com":
            return httpx.Response(200, json={"success": True})
        if request.method == "POST" and path.endswith("/media"):
            return httpx.Response(200, json={"id": f"container{len([r for r in self.requests if r[0] == 'POST' and r[1].endswith('/media')])}"})
        if request.method == "GET" and "status_code" in url:
            return httpx.Response(200, json={"status_code": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]})
        if path.endswith("/media_publish"):
            return httpx.Response(self.publish_status, json={"id": "1790"}) if self.publish_status == 200 else httpx.Response(self.publish_status)
        if request.method == "GET" and "permalink" in url:
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/XYZ/"})
        return httpx.Response(404, json={"error": {"message": "nope"}})


def client(meta):
    return httpx.AsyncClient(transport=httpx.MockTransport(meta.handler))


async def ig(meta, **kw):
    return Instagram("1784", "SECRET-TOKEN", client=client(meta), poll_s=0, **kw)


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "reel.mp4"
    p.write_bytes(VIDEO)
    return p


async def test_a_reel_goes_container_upload_wait_publish_and_confirm(video):
    meta = Meta()
    out = await (await ig(meta)).publish_reel(video, "Parkour #minecraft")
    assert out.media_id == "1790" and out.permalink == "https://www.instagram.com/reel/XYZ/"
    steps = [(m, urlpath) for m, u, h, b in meta.requests for urlpath in [httpx.URL(u).path]]
    kinds = [p.split("/")[-1] for m, p in steps]
    assert kinds[0] == "media" and "container1" in kinds and kinds.index("media_publish") > kinds.index("container1")
    create = json.loads(meta.requests[0][3])
    assert create["media_type"] == "REELS" and create["upload_type"] == "resumable" and create["caption"] == "Parkour #minecraft"
    assert create["is_ai_generated"] is True
    upload = next(r for r in meta.requests if "rupload" in r[1])
    assert upload[2]["offset"] == "0" and upload[2]["file_size"] == str(len(VIDEO)) and upload[3] == VIDEO
    assert upload[2]["authorization"] == "OAuth SECRET-TOKEN" and meta.requests[0][2]["authorization"] == "Bearer SECRET-TOKEN"


async def test_the_ai_label_can_be_left_off_for_real_footage(video):
    meta = Meta()
    await (await ig(meta)).publish_reel(video, "caption", ai_generated=False)
    assert "is_ai_generated" not in json.loads(meta.requests[0][3]) or json.loads(meta.requests[0][3])["is_ai_generated"] is False


async def test_the_token_only_ever_goes_to_meta_hosts():
    with pytest.raises(ValueError, match="host"):
        Instagram("1784", "T", host="evil.example.com")
    Instagram("1784", "T", host="graph.facebook.com")
    Instagram("1784", "T")


async def test_a_container_that_ends_in_error_is_a_failure_before_anything_is_published(video):
    meta = Meta(statuses=("IN_PROGRESS", "ERROR"))
    with pytest.raises(InstagramError) as e:
        await (await ig(meta)).publish_reel(video, "c")
    assert e.value.published is False and "ERROR" in str(e.value)
    assert not any(u.endswith("/media_publish") for _, u, _, _ in meta.requests)


async def test_a_container_that_never_finishes_times_out_without_publishing(video):
    meta = Meta(statuses=("IN_PROGRESS",))
    api = Instagram("1784", "T", client=client(meta), poll_s=0, timeout_s=0.05)
    with pytest.raises(InstagramError, match="did not finish") as e:
        await api.publish_reel(video, "c")
    assert e.value.published is False


async def test_a_refused_request_shows_instagrams_reason_with_the_token_removed(video):
    meta = Meta(fail_at="/1784/media")
    with pytest.raises(InstagramError) as e:
        await (await ig(meta)).publish_reel(video, "c")
    assert "Invalid parameter" in str(e.value) and "SECRET-TOKEN" not in str(e.value) and e.value.published is False


async def test_a_publish_that_may_have_gone_through_is_marked_as_possibly_published(video):
    meta = Meta(publish_status=500)
    with pytest.raises(InstagramError) as e:
        await (await ig(meta)).publish_reel(video, "c")
    assert e.value.published is None  # unknown: the request was made and the answer was an error or never came


async def test_a_dropped_connection_at_publish_is_unknown_not_failed(video):
    meta = Meta()

    def handler(request):
        if request.url.path.endswith("/media_publish"):
            raise httpx.ReadTimeout("slow", request=request)
        return meta.handler(request)

    api = Instagram("1784", "T", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), poll_s=0)
    with pytest.raises(InstagramError) as e:
        await api.publish_reel(video, "c")
    assert e.value.published is None


async def test_a_missing_permalink_does_not_undo_a_publish(video):
    meta = Meta()
    orig = meta.handler

    def handler(request):
        if "permalink" in str(request.url):
            return httpx.Response(500)
        return orig(request)

    api = Instagram("1784", "T", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), poll_s=0)
    out = await api.publish_reel(video, "c")
    assert out.media_id == "1790" and out.permalink == ""


async def test_a_carousel_is_children_then_a_carousel_container_then_publish():
    meta = Meta()
    out = await (await ig(meta)).publish_carousel(["https://pics.example.com/1.jpg", "https://pics.example.com/2.jpg"], "caption")
    posts = [json.loads(b) for m, u, h, b in meta.requests if m == "POST" and u.endswith("/media")]
    assert [p.get("is_carousel_item") for p in posts[:2]] == [True, True] and posts[0]["image_url"].endswith("1.jpg")
    assert posts[2]["media_type"] == "CAROUSEL" and posts[2]["children"] == "container1,container2" and posts[2]["caption"] == "caption"
    assert out.media_id == "1790"
    with pytest.raises(ValueError, match="2 to 20"):
        await (await ig(Meta())).publish_carousel(["https://pics.example.com/1.jpg"], "c")


class Refresh(Meta):
    def handler(self, request):
        if request.url.path.endswith("/refresh_access_token"):
            self.requests.append((request.method, str(request.url), dict(request.headers), request.content))
            return httpx.Response(200, json={"access_token": "NEW-TOKEN", "token_type": "bearer", "expires_in": 5184000})
        return super().handler(request)


async def test_a_long_lived_token_can_be_refreshed_for_another_sixty_days():
    meta = Refresh()
    api = await ig(meta)
    token, seconds = await api.refresh_token()
    assert token == "NEW-TOKEN" and seconds == 5184000
    method, url, headers, _ = meta.requests[0]
    assert method == "GET" and url.startswith("https://graph.instagram.com/refresh_access_token")
    assert "grant_type=ig_refresh_token" in url and "access_token=SECRET-TOKEN" in url


async def test_refreshing_only_works_on_the_instagram_login_host():
    api = Instagram("1784", "T", host="graph.facebook.com", client=client(Meta()))
    with pytest.raises(InstagramError, match="Instagram login"):
        await api.refresh_token()


async def test_a_refused_refresh_never_shows_the_token():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "Token SECRET-TOKEN is too new"}})

    api = Instagram("1784", "SECRET-TOKEN", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(InstagramError) as e:
        await api.refresh_token()
    assert "SECRET-TOKEN" not in str(e.value) and "too new" in str(e.value)


class Insights(Meta):
    def __init__(self, bad=()):
        super().__init__()
        self.bad = set(bad)

    def handler(self, request):
        if request.url.path.endswith("/insights"):
            self.requests.append((request.method, str(request.url), dict(request.headers), request.content))
            metrics = request.url.params.get("metric", "").split(",")
            if self.bad & set(metrics):
                return httpx.Response(400, json={"error": {"message": "metric not supported for this media"}})
            return httpx.Response(200, json={"data": [{"name": m, "values": [{"value": 10 + i}]} for i, m in enumerate(metrics)]})
        return super().handler(request)


async def test_how_a_post_did_is_read_back_as_numbers():
    meta = Insights()
    got = await (await ig(meta)).insights("1790")
    assert got["reach"] == 10 and set(got) >= {"reach", "likes", "comments", "shares", "saved"}
    assert "/1790/insights" in meta.requests[0][1]


async def test_a_metric_that_is_not_available_does_not_lose_the_others():
    meta = Insights(bad={"views"})
    got = await (await ig(meta)).insights("1790")
    assert "views" not in got and got["reach"] >= 10  # asked together first, then one at a time


async def test_a_post_with_no_numbers_yet_is_an_empty_result_not_an_error():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "unsupported"}})

    api = Instagram("1784", "T", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await api.insights("1790") == {}
