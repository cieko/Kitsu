import asyncio
import base64
import io
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image
import pytest

from app.services.emoji_mood import MoodClassifier, MoodError

from .helpers import asset, image_bytes, payload, unicode_asset


def test_real_http_payload_contains_visual_frames_and_caches_by_asset_and_image_hash(monkeypatch):
    async def check():
        requests = []

        async def chat(request):
            body = await request.json()
            requests.append(body)
            assert body["model"] == "test-vision"
            assert [item["role"] for item in body["messages"]] == ["system", "user"]
            assert 'Untrusted asset name (hint only): "untrusted_asset_name"' in body["messages"][1]["content"]
            assert body["format"]["properties"]["nsfw"]["enum"] == ["likely", "unlikely", "uncertain"]
            assert body["options"]["temperature"] == 0
            assert body["options"]["num_gpu"] == 12
            assert body["stream"] is False
            with Image.open(io.BytesIO(base64.b64decode(body["messages"][1]["images"][0]))) as image:
                assert image.size == (768, 552)
            return web.json_response(payload())

        app = web.Application()
        app.router.add_post("/api/chat", chat)
        async with TestServer(app) as server:
            service = MoodClassifier(model_url=str(server.make_url("/api")), model_name="test-vision", num_gpu=12)
            download = AsyncMock(return_value=image_bytes(frames=8))
            monkeypatch.setattr(service, "_download", download)
            first, second = await asyncio.gather(service.classify(asset()), service.classify(asset()))
            third = await service.classify(asset(123456789012346))
            assert first == second == third
            assert first.sampled_frames == 6 and first.total_frames == 8
            assert len(requests) == 1
            assert download.await_count == 2

    asyncio.run(check())


def test_unicode_uses_standard_meaning_and_serializes_generation():
    async def check():
        requests, active, peak = [], 0, 0

        async def chat(request):
            nonlocal active, peak
            body = await request.json()
            requests.append(body)
            assert "images" not in body["messages"][1]
            assert "Unicode name:" in body["messages"][1]["content"]
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return web.json_response(payload())

        app = web.Application()
        app.router.add_post("/api/chat", chat)
        async with TestServer(app) as server:
            service = MoodClassifier(model_url=str(server.make_url("/")))
            await asyncio.gather(service.classify(unicode_asset()), service.classify(unicode_asset("😍")))
            await service.classify(unicode_asset())
        assert peak == 1 and len(requests) == 2

    asyncio.run(check())


@pytest.mark.parametrize("body,status", [
    ({"error": "missing model"}, 404), ({"error": "no vision support"}, 400),
    ({"error": "GPU allocation failed"}, 500), ({"error": "busy"}, 429),
    ({"done": True, "message": {"content": "not json"}}, 200),
    ({"done": False, "message": {"content": "{}"}}, 200),
    (dict(payload(), done_reason="length"), 200),
    ({"done": True, "message": {"content": None}}, 200),
])
def test_failures_are_human_readable_and_never_return_a_guessed_mood(body, status):
    async def check():
        async def chat(request):
            return web.json_response(body, status=status)

        app = web.Application()
        app.router.add_post("/api/chat", chat)
        async with TestServer(app) as server:
            service = MoodClassifier(model_url=str(server.make_url("/")))
            with pytest.raises(MoodError) as caught:
                await service.classify(unicode_asset())
            assert not service._cache
            assert "GPU allocation failed" not in str(caught.value)

    asyncio.run(check())


def test_expired_and_evicted_results_are_recomputed():
    async def check():
        count = 0

        async def chat(request):
            nonlocal count
            count += 1
            return web.json_response(payload())

        app = web.Application()
        app.router.add_post("/api/chat", chat)
        async with TestServer(app) as server:
            service = MoodClassifier(model_url=str(server.make_url("/")), cache_size=2)
            for symbol in ("😀", "😍", "😢", "😀"):
                await service.classify(unicode_asset(symbol))
            assert count == 4 and len(service._cache) == 2
            service.cache_ttl = 0
            await service.classify(unicode_asset("😡"))
            await service.classify(unicode_asset("😡"))
            assert count == 6

    asyncio.run(check())


def test_queue_limit_timeout_and_failure_backoff_are_bounded():
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def chat(request):
            entered.set()
            await release.wait()
            return web.json_response(payload())

        app = web.Application()
        app.router.add_post("/api/chat", chat)
        async with TestServer(app) as server:
            service = MoodClassifier(model_url=str(server.make_url("/")), max_pending=1)
            task = asyncio.create_task(service.classify(unicode_asset()))
            await entered.wait()
            with pytest.raises(MoodError, match="busy"):
                await service.classify(unicode_asset("😍"))
            release.set()
            await task
            release.clear()
            service.timeout = 0.02
            with pytest.raises(MoodError, match="too long"):
                await service.classify(unicode_asset("😢"))
            assert service._pending == 0
            with pytest.raises(MoodError, match="temporarily unavailable"):
                await service.classify(unicode_asset("😡"))
            assert await service.classify(unicode_asset())
            release.set()

    asyncio.run(check())


@pytest.mark.parametrize("streamed", [False, True])
def test_download_limit_applies_to_content_length_and_chunked_responses(monkeypatch, streamed):
    import aiohttp
    from app.services.emoji_mood import service as module

    monkeypatch.setattr(module, "MAX_DOWNLOAD_BYTES", 4)

    async def check():
        async def image(request):
            if not streamed:
                return web.Response(body=b"12345678")
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"12345678")
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_get("/image", image)
        async with TestServer(app) as server:
            async with aiohttp.ClientSession() as session:
                with pytest.raises(MoodError, match="too large"):
                    await MoodClassifier()._download(session, str(server.make_url("/image")))

    asyncio.run(check())
