import asyncio
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from app.services.ai_service import AIService, FALLBACK_CELEBRATION, hit_reaction_fallback


def test_ollama_generates_only_flavour_text_without_sending_channel_history():
    async def check():
        received = []

        async def handle(request):
            received.append(await request.json())
            return web.json_response({"message": {"content": "Hurrah! Ghosty says you're welcome! 🎉"}})

        app = web.Application()
        app.router.add_post("/api/chat", handle)
        async with TestServer(app) as server:
            service = AIService(model_url=str(server.make_url("/api")), model_name="test-model", num_gpu=0)
            assert await service.celebrate_hit(net_result=100) == "Hurrah! Ghosty says you're welcome! 🎉"
        assert received[0]["model"] == "test-model" and received[0]["stream"] is False
        assert [message["role"] for message in received[0]["messages"]] == ["system", "user"]
        assert "channel_id" not in str(received[0])
        assert received[0]["options"]["num_predict"] <= 100
        assert received[0]["options"]["num_gpu"] == 0

    asyncio.run(check())


@pytest.mark.parametrize("payload,status", [
    ({"error": "model missing"}, 404),
    ({"message": {"content": ""}}, 200),
    ({"message": {"content": "You won 999999 Goos!"}}, 200),
    ({"message": {"content": "@everyone <@7>"}}, 200),
    ({"message": {"content": None}}, 200),
    ({"response": "wrong endpoint shape"}, 200),
])
@pytest.mark.parametrize("net_result", [-7, 0, 100])
def test_unavailable_or_malformed_model_keeps_a_celebration(payload, status, net_result):
    async def check():
        async def handle(request):
            return web.json_response(payload, status=status)

        app = web.Application()
        app.router.add_post("/api/chat", handle)
        async with TestServer(app) as server:
            service = AIService(model_url=str(server.make_url("/api/chat")))
            assert await service.celebrate_hit(net_result=net_result) == hit_reaction_fallback(net_result)

    asyncio.run(check())


def test_slow_model_times_out_and_does_not_cancel_the_hit_notification():
    async def check():
        async def handle(request):
            await asyncio.sleep(0.1)
            return web.json_response({"message": {"content": "Hurrah!"}})

        app = web.Application()
        app.router.add_post("/api/chat", handle)
        async with TestServer(app) as server:
            service = AIService(model_url=str(server.make_url("/")))
            service.timeout = 0.01
            assert await service.celebrate_hit(net_result=100) == FALLBACK_CELEBRATION

    asyncio.run(check())


def test_blank_environment_settings_use_the_local_model_defaults():
    service = AIService.from_settings(SimpleNamespace(ai_model_url=" ", ai_model_name="", ai_model_timeout=30))
    assert service.chat_url == "http://127.0.0.1:11434/api/chat"
    assert service.model_name == "kitsu_mod"


def test_simultaneous_hits_share_one_gpu_generation_at_a_time():
    async def check():
        active = 0
        maximum_active = 0
        received = []

        async def handle(request):
            nonlocal active, maximum_active
            received.append(await request.json())
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                await asyncio.sleep(0.02)
                return web.json_response({"message": {"content": "Ghosty picked up the tab! 🎉"}})
            finally:
                active -= 1

        app = web.Application()
        app.router.add_post("/api/chat", handle)
        async with TestServer(app) as server:
            service = AIService.from_settings(SimpleNamespace(
                ai_model_url=str(server.make_url("/")), ai_model_name="test-model",
                ai_model_timeout=30, ai_model_num_gpu=12,
            ))
            replies = await asyncio.gather(*(service.celebrate_hit(net_result=10) for _ in range(3)))
        assert replies == ["Ghosty picked up the tab! 🎉"] * 3
        assert maximum_active == 1
        assert len(received) == 3
        for request in received:
            assert request["options"]["num_gpu"] == 12
            assert request["options"]["num_batch"] == 64
            assert request["options"]["num_ctx"] == 1024
            assert request["keep_alive"] == "5m"

    asyncio.run(check())


def test_gpu_allocation_failure_uses_fallback_without_retrying_generation():
    async def check():
        received = []

        async def handle(request):
            received.append(await request.json())
            return web.json_response({"error": "CUDA out of memory"}, status=500)

        app = web.Application()
        app.router.add_post("/api/chat", handle)
        async with TestServer(app) as server:
            service = AIService(model_url=str(server.make_url("/")), num_gpu=12)
            assert await service.celebrate_hit(net_result=-7) == hit_reaction_fallback(-7)
        assert len(received) == 1

    asyncio.run(check())


@pytest.mark.parametrize("net_result, reply, accepted", [
    (-7, "Ghosty calls that generosity; the net loss politely disagrees. 🙃", True),
    (-7, "Hurrah! Ghosty absolutely crushed it—you're welcome! 🎉", False),
    (-7, "A wonderful payout, even if the streak ended at a loss!", False),
    (-7, "What a win, even with a net loss.", False),
    (-7, "You idiot, the streak ended at a loss.", False),
    (-7, "Ghosty kept the receipt. 🙃", False),
    (0, "All that suspense just to break even—classic Ghosty. 😅", True),
    (0, "Hurrah, what a victory! 🎉", False),
    (1389, "A welcome net win—Ghosty finally picked up the tab! 🎉", True),
    (1389, "The streak ended at a loss. 🙃", False),
])
def test_model_reactions_match_net_outcome_without_sharing_amounts(net_result, reply, accepted):
    async def check():
        received = []

        async def handle(request):
            received.append(await request.json())
            return web.json_response({"message": {"content": reply}})

        app = web.Application()
        app.router.add_post("/api/chat", handle)
        async with TestServer(app) as server:
            service = AIService(model_url=str(server.make_url("/")))
            reaction = await service.celebrate_hit(net_result=net_result)
            assert reaction == (reply if accepted else hit_reaction_fallback(net_result))
        messages = received[0]["messages"]
        assert str(net_result) not in " ".join(message["content"] for message in messages)
        assert ("NET LOSS" if net_result < 0 else "BREAK-EVEN" if net_result == 0 else "NET WIN") in messages[1]["content"]

    asyncio.run(check())
