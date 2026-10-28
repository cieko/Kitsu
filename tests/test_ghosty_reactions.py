import json
from unittest.mock import MagicMock

import pytest

from app.services import ghosty_reaction_service as reactions


@pytest.fixture(autouse=True)
def clear_message_cache():
    reactions.load_hit_reactions.cache_clear()
    yield
    reactions.load_hit_reactions.cache_clear()


def test_message_bank_has_one_hundred_unique_win_and_loss_messages():
    data = json.loads(reactions.REACTIONS_FILE.read_text(encoding="utf-8"))
    for outcome in ("win", "loss"):
        assert len(data[outcome]) == len(set(data[outcome])) == 100
    assert data["break_even"]
    for messages in data.values():
        for message in messages:
            assert isinstance(message, str) and message.strip() == message
            assert 0 < len(message) <= 350
            assert not any(text in message for text in ("<@", "@everyone", "@here", "http"))


@pytest.mark.parametrize("net_result, outcome", [(9, "win"), (-7, "loss"), (0, "break_even")])
def test_random_choice_uses_only_the_actual_net_outcome(monkeypatch, net_result, outcome):
    bank = reactions.load_hit_reactions()
    choose = MagicMock(side_effect=lambda messages: messages[-1])
    monkeypatch.setattr(reactions.random, "choice", choose)
    assert reactions.get_hit_reaction(net_result=net_result) == bank[outcome][-1]
    choose.assert_called_once_with(bank[outcome])


def test_repeated_hits_read_the_json_file_only_once(monkeypatch):
    load = MagicMock(wraps=json.load)
    monkeypatch.setattr(reactions.json, "load", load)
    for net_result in (9, -7, 0) * 100:
        assert reactions.get_hit_reaction(net_result=net_result)
    load.assert_called_once()


@pytest.mark.parametrize("content", ["invalid json", "[]", '{"win": []}', '{"win": [null]}'])
def test_invalid_message_file_uses_outcome_specific_local_defaults(monkeypatch, tmp_path, content):
    bank_file = tmp_path / "reactions.json"
    bank_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(reactions, "REACTIONS_FILE", bank_file)
    assert reactions.get_hit_reaction(net_result=9) in reactions.FALLBACK_REACTIONS["win"]
    assert reactions.get_hit_reaction(net_result=-7) in reactions.FALLBACK_REACTIONS["loss"]
    assert reactions.get_hit_reaction(net_result=0) in reactions.FALLBACK_REACTIONS["break_even"]
