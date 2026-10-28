import asyncio
from dataclasses import replace
import json
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from app.cogs.emoji_mood.presentation import result_embed
from app.services.emoji_mood import MoodClassifier, MoodError, MoodResult

from .helpers import asset, image_bytes, payload


@pytest.mark.parametrize("nudity", ["likely", "unlikely", "uncertain"])
@pytest.mark.parametrize("nsfw", ["likely", "unlikely", "uncertain"])
def test_nudity_is_independent_of_mood_and_nsfw_and_displayed_explicitly(nudity, nsfw):
    parsed = MoodResult.parse(payload(nudity=nudity, nsfw=nsfw)["message"]["content"])
    assert parsed.moods == ("happy", "fun")
    assert parsed.nudity == nudity and parsed.nsfw == nsfw
    embed = result_embed(asset(), parsed)
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Nudity"] == nudity.title()
    assert fields["NSFW"] == nsfw.title()
    if "likely" in (nudity, nsfw):
        assert embed.colour.value == 0xED4245
    elif "uncertain" in (nudity, nsfw):
        assert embed.colour.value == 0xFEE75C
    assert embed.thumbnail.url is None


@pytest.mark.parametrize("nudity", [None, True, False, 0, 1, "", "safe", "explicit", [], {}])
def test_invalid_nudity_assessment_is_never_treated_as_safe(nudity):
    with pytest.raises(MoodError):
        MoodResult.parse(payload(nudity=nudity)["message"]["content"])


def test_missing_nudity_assessment_is_not_silently_defaulted_to_safe():
    value = json.loads(payload()["message"]["content"])
    del value["nudity"]
    with pytest.raises(MoodError):
        MoodResult.parse(json.dumps(value))


def test_nudity_is_returned_and_cached_in_the_same_visual_request(monkeypatch):
    async def check():
        requests = []

        async def chat(request):
            body = await request.json()
            requests.append(body)
            schema = body["format"]
            assert "nudity" in schema["required"]
            assert schema["properties"]["nudity"]["enum"] == ["likely", "unlikely", "uncertain"]
            assert body["messages"][1]["images"]
            assert '"happy_cat"' in body["messages"][1]["content"]
            return web.json_response(payload(nudity="likely", nsfw="likely"))

        app = web.Application()
        app.router.add_post("/api/chat", chat)
        async with TestServer(app) as server:
            service = MoodClassifier(model_url=str(server.make_url("/")))
            download = AsyncMock(return_value=image_bytes())
            monkeypatch.setattr(service, "_download", download)
            item = replace(asset(), name="happy_cat")
            first = await service.classify(item)
            assert first.nudity == "likely"
            assert await service.classify(item) == first
            assert len(requests) == 1
            download.assert_awaited_once()

    asyncio.run(check())


def test_incomplete_model_response_is_not_cached_as_a_success(monkeypatch):
    async def check():
        async def chat(request):
            value = payload()
            content = json.loads(value["message"]["content"])
            del content["nudity"]
            value["message"]["content"] = json.dumps(content)
            return web.json_response(value)

        app = web.Application()
        app.router.add_post("/api/chat", chat)
        async with TestServer(app) as server:
            service = MoodClassifier(model_url=str(server.make_url("/")))
            monkeypatch.setattr(service, "_download", AsyncMock(return_value=image_bytes()))
            with pytest.raises(MoodError, match="confidently classify"):
                await service.classify(asset())
            assert not service._cache and service._pending == 0

    asyncio.run(check())
