import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from PIL import Image
import pytest


NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
CHANNEL = 1553844187902771290


def history(lengths=(2, 4, 8, 13, 22, 6), *, trailing_misses=0):
    outcomes = ["hit"]
    for length in lengths:
        outcomes.extend(["miss"] * (length - 1) + ["hit"])
    outcomes.extend(["miss"] * trailing_misses)
    return [{"outcome": outcome, "wager": 100, "payout": 1000 if outcome == "hit" else 0,
             "result_message_id": str(index + 1), "created_at": NOW - timedelta(minutes=len(outcomes) - index)}
            for index, outcome in enumerate(outcomes)]


def snapshot(module, *, empty=False):
    return module.StreakSnapshot(
        now=NOW, stats={"streak": 3, "streak_loss": 366},
        previous_attempts=module.completed_streaks(history()) if not empty else [],
        prediction={"ready": not empty, "hit_at": 4 if not empty else None, "candidate_percent": 9,
                    "hit_candidates": [
                        {"attempt": attempt, "probability": chance, "status": "missed" if attempt == 2 else "pending"}
                        for attempt, chance in ((2, .08), (4, .09), (8, .04), (13, .03), (22, .02))
                    ] if not empty else []},
    )


def test_previous_attempts_include_exact_hit_positions_and_complete_amounts(import_app):
    module = import_app("app.services.ghosty_streak_service")
    rows = history(trailing_misses=9)
    # The first partial streak is unknown and must not become a fake Hit @.
    rows = [{"outcome": "miss", "wager": 9999}] * 4 + rows
    previous = module.completed_streaks(rows)
    assert [row.hit_at for row in previous] == [6, 22, 13, 8, 4]
    assert [row.loss for row in previous] == [500, 2100, 1200, 700, 300]
    assert [row.net for row in previous] == [500, -1100, -200, 300, 700]
    assert all(row.payout == 1000 for row in previous)
    assert module.completed_streaks(history((1,)))[0].hit_at == 1
    assert module.completed_streaks([{"outcome": "hit"}]) == []


def test_history_reads_complete_long_runs_in_this_channel_off_loop_without_writes(import_app, monkeypatch):
    module = import_app("app.database.repositories.ghosty_repository")
    rows = history((2, 4, 650, 8, 13, 22, 6), trailing_misses=1)
    hits = [row for row in rows if row["outcome"] == "hit"][-6:]
    oldest, newest = hits[0], hits[-1]
    # Same-timestamp rows outside the selected message-ID boundaries are excluded.
    outside = [dict(oldest, outcome="miss", result_message_id="0"),
               dict(newest, outcome="miss", result_message_id="99999")]
    markers, results = MagicMock(), MagicMock()
    markers.sort.return_value = markers
    markers.limit.return_value = markers
    markers.__iter__.side_effect = lambda: iter(reversed(hits))
    results.sort.return_value = results
    results.__iter__.side_effect = lambda: iter(reversed(rows + outside))
    collection = MagicMock()
    current_thread = threading.get_ident()
    cursors = iter((markers, results))

    def find(*args):
        assert threading.get_ident() != current_thread
        return next(cursors)

    collection.find.side_effect = find
    monkeypatch.setattr(module.state, "gamble_history", collection)
    data = asyncio.run(module.get_completed_streak_history(1, CHANNEL, now=NOW))
    assert data[0] == oldest and data[-1] == newest and len(data) > 500
    markers.limit.assert_called_once_with(6)
    first, second = collection.find.call_args_list
    assert first.args[0] == {"guild_id": "1", "channel_id": str(CHANNEL), "outcome": "hit",
                             "created_at": {"$lte": NOW, "$type": "date"}}
    assert second.args[0]["channel_id"] == str(CHANNEL)
    assert second.args[0]["created_at"] == {"$gte": oldest["created_at"], "$lte": newest["created_at"]}
    assert "user_id" not in first.args[1]
    results.limit.assert_not_called()
    collection.insert_one.assert_not_called()
    collection.update_one.assert_not_called()


@pytest.mark.parametrize("hits", [0, 1])
def test_incomplete_history_never_fabricates_a_completed_streak(import_app, monkeypatch, hits):
    module = import_app("app.database.repositories.ghosty_repository")
    cursor = MagicMock()
    cursor.sort.return_value = cursor.limit.return_value = cursor
    cursor.__iter__.return_value = iter(history((1,))[:hits])
    collection = MagicMock()
    collection.find.return_value = cursor
    monkeypatch.setattr(module.state, "gamble_history", collection)
    assert asyncio.run(module.get_completed_streak_history(1, CHANNEL, now=NOW)) == []
    collection.find.assert_called_once()


def test_loader_reuses_channel_stats_and_the_saved_prediction(import_app, monkeypatch):
    module = import_app("app.services.ghosty_streak_service")
    load = AsyncMock(return_value=history())
    monkeypatch.setattr(module.repository, "get_completed_streak_history", load)
    monkeypatch.setattr(module.repository, "get_config", AsyncMock(return_value=75))
    stats = {"streak": 3, "streak_loss": 366}
    prediction = {"ready": False}
    tracker = SimpleNamespace(get_stats=AsyncMock(return_value=stats),
                              get_prediction_data=AsyncMock(return_value=prediction))
    data = asyncio.run(module.GhostyStreakService(tracker).load(1, CHANNEL))
    assert data.prediction is prediction and data.stats is stats
    assert [row.hit_at for row in data.previous_attempts] == [6, 22, 13, 8, 4]
    assert load.call_args.args == (1, CHANNEL) and load.call_args.kwargs["limit"] == 5
    tracker.get_prediction_data.assert_awaited_once_with(1, CHANNEL, minimum_samples=75, stats=stats)


@pytest.mark.parametrize("empty", [True, False])
def test_candidate_graphs_are_real_pngs_with_up_to_five_saved_outcomes(import_app, empty):
    module = import_app("app.services.ghosty_streak_service")
    data = snapshot(module, empty=empty)
    before = list(data.prediction["hit_candidates"])
    candidates = module.chart_candidates(data)
    assert len(candidates) == (0 if empty else 5)
    with Image.open(BytesIO(module.render_streak_chart(data))) as image:
        assert image.format == "PNG" and image.size == (1200, 480)
        assert any(high - low > 100 for low, high in image.convert("RGB").getextrema())
    assert data.prediction["hit_candidates"] == before


def test_graph_filters_invalid_outcomes_and_keeps_the_five_highest_chances(import_app):
    module = import_app("app.services.ghosty_streak_service")
    data = snapshot(module)
    data.prediction["hit_candidates"] += [
        {"attempt": 99, "probability": .2}, {"attempt": 100, "probability": float("nan")},
    ]
    assert [candidate["attempt"] for candidate in module.chart_candidates(data)] == [2, 4, 8, 13, 99]


def interaction(*, user_id=7, guild_id=1, channel_id=CHANNEL):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id), guild=SimpleNamespace(id=guild_id), channel_id=channel_id,
        data={}, response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()),
    )


def make_view(module, data, service=None, **kwargs):
    view = module.GhostyStreakView(
        service=service or SimpleNamespace(load=AsyncMock(return_value=data)), snapshot=data,
        owner_id=7, guild_id=1, channel_id=CHANNEL, prefix=",k ", **kwargs,
    )
    view.on_error = AsyncMock()
    return view


def find(view, custom_id):
    return next(item for item in view.walk_children() if getattr(item, "custom_id", None) == custom_id)


async def choose(view, mode):
    current = interaction()
    current.data = {"values": [mode]}
    await view._scheduled_task(find(view, "ghosty:streak:view"), current)
    return current


def test_two_views_swap_attachments_and_refresh_keeps_the_current_view(import_app, monkeypatch):
    module = import_app("app.services.ghosty_streak_ui")
    analytics = import_app("app.services.ghosty_streak_service")
    render = MagicMock(return_value=b"png bytes")
    monkeypatch.setattr(module, "render_streak_chart", render)

    async def check():
        service = SimpleNamespace(load=AsyncMock(return_value=snapshot(analytics, empty=True)))
        view = make_view(module, snapshot(analytics), service)
        assert view.to_components()[0]["type"] == 17
        assert [(option.label, option.value) for option in find(view, "ghosty:streak:view").options] == [
            ("Previous Attempts", "previous"), ("Current Streak", "current"),
        ]
        assert await view.chart_file() is None
        render.assert_not_called()
        text = "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))
        assert "**Last 5 hits @** `6` · `22` · `13` · `8` · `4`" in text
        assert "-1,100" in text and "Received" in text
        assert all(word not in text for word in ("Bayesian", "95%", "Server", "Controls expire", "Channel created"))
        current = await choose(view, "current")
        assert view.mode == "current" and len(current.edit_original_response.call_args.kwargs["attachments"]) == 1
        assert any(isinstance(item, discord.ui.MediaGallery) for item in view.walk_children())
        assert "**Estimated hit @** `4`" in view._current()
        previous = await choose(view, "previous")
        assert previous.edit_original_response.call_args.kwargs["attachments"] == []
        assert not any(isinstance(item, discord.ui.MediaGallery) for item in view.walk_children())
        await choose(view, "current")
        assert render.call_count == 1
        await view._scheduled_task(find(view, "ghosty:streak:refresh"), interaction())
        assert view.mode == "current" and "Still learning" in view._current()
        assert render.call_count == 2
        service.load.assert_awaited_once_with(1, CHANNEL)
        view.on_error.assert_not_awaited()

    asyncio.run(check())


@pytest.mark.parametrize("change", ["owner", "guild", "channel"])
def test_other_users_and_channels_cannot_control_the_panel(import_app, change):
    module = import_app("app.services.ghosty_streak_ui")
    analytics = import_app("app.services.ghosty_streak_service")

    async def check():
        view = make_view(module, snapshot(analytics))
        parameters = {"owner": {"user_id": 8}, "guild": {"guild_id": 2}, "channel": {"channel_id": 1}}[change]
        current = interaction(**parameters)
        await view._scheduled_task(find(view, "ghosty:streak:refresh"), current)
        current.response.send_message.assert_awaited_once()
        assert current.response.send_message.call_args.kwargs["ephemeral"]
        current.edit_original_response.assert_not_awaited()
        assert view.mode == "previous"

    asyncio.run(check())


def test_graph_permissions_do_not_block_previous_attempts(import_app):
    module = import_app("app.services.ghosty_streak_ui")
    analytics = import_app("app.services.ghosty_streak_service")

    async def check():
        view = make_view(module, snapshot(analytics), can_attach_files=False)
        current = await choose(view, "current")
        current.followup.send.assert_awaited_once()
        current.edit_original_response.assert_not_awaited()
        assert view.mode == "previous"

    asyncio.run(check())


def test_refresh_failure_rolls_back_and_timeout_disables_controls(import_app):
    module = import_app("app.services.ghosty_streak_ui")
    analytics = import_app("app.services.ghosty_streak_service")

    async def check():
        data = snapshot(analytics)
        view = make_view(module, data, SimpleNamespace(load=AsyncMock(side_effect=RuntimeError("database offline"))))
        current = interaction()
        await view._scheduled_task(find(view, "ghosty:streak:refresh"), current)
        assert view.snapshot is data and view.mode == "previous"
        current.followup.send.assert_awaited_once()
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        assert all(item.disabled for item in view.walk_children() if isinstance(item, (discord.ui.Select, discord.ui.Button)))
        view.message.edit.assert_awaited_once_with(view=view)
        assert not await view.interaction_check(interaction())

    asyncio.run(check())


@pytest.mark.parametrize("case", ["public", "no_files", "dm"])
def test_prefix_streak_command_uses_the_invoked_channel_and_saved_prefix(import_app, monkeypatch, case):
    module = import_app("app.cogs.ghosty")
    analytics = import_app("app.services.ghosty_streak_service")

    async def check():
        cog = module.GhostyCog(SimpleNamespace(user=SimpleNamespace(id=900)))
        cog.streak_service.load = AsyncMock(return_value=snapshot(analytics))
        channel = SimpleNamespace(id=CHANNEL, permissions_for=lambda member: SimpleNamespace(attach_files=case != "no_files"))
        context = SimpleNamespace(
            guild=None if case == "dm" else SimpleNamespace(id=1),
            author=SimpleNamespace(id=7), channel=channel, me=SimpleNamespace(id=900), clean_prefix="custom ",
            send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())), typing=MagicMock(),
        )
        await cog.ghosty_streak_text.callback(cog, context)
        context.send.assert_awaited_once()
        assert "file" not in context.send.call_args.kwargs
        if case != "dm":
            cog.streak_service.load.assert_awaited_once_with(1, CHANNEL)
            view = context.send.call_args.kwargs["view"]
            assert view.prefix == "custom " and view.owner_id == 7 and view.channel_id == CHANNEL
            assert view.can_attach_files == (case != "no_files")
            assert cog.ghosty_text.get_command("streak") is not None and not cog.ghosty_streak_text.checks
        else:
            cog.streak_service.load.assert_not_awaited()

    asyncio.run(check())
