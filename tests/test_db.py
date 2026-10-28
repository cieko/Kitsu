import asyncio
import threading
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    "module_name,collection_name,operation,args",
    [
        ("prefix_repository", "prefix_collection", "get_prefix", (1,)),
        ("prefix_repository", "prefix_collection", "set_prefix", (1, "!")),
        ("prefix_repository", "prefix_collection", "reset_prefix", (1,)),
        ("channel_repository", "channel_configs", "get_channel_config", (1, 2)),
        ("channel_repository", "channel_configs", "set_channel_config", (1, 2, "custom")),
        ("channel_repository", "channel_configs", "reset_channel_config", (1, 2)),
    ],
)
def test_database_operations_leave_the_event_loop(
    import_app, monkeypatch, module_name, collection_name, operation, args
):
    repository = import_app(f"app.database.repositories.{module_name}")
    collection = Mock()
    event_loop_thread = threading.get_ident()
    calls = []

    def database_call(*args, **kwargs):
        calls.append(threading.get_ident())
        assert calls[-1] != event_loop_thread
        return {"prefix": "!"}

    for method in ("find_one", "update_one", "delete_one"):
        getattr(collection, method).side_effect = database_call
    monkeypatch.setattr(repository, collection_name, collection)

    asyncio.run(getattr(repository, operation)(*args))
    assert len(calls) == 1


def test_blank_stored_prefix_falls_back_to_default(import_app, monkeypatch):
    repository = import_app("app.database.repositories.prefix_repository")
    collection = Mock()
    collection.find_one.return_value = {"prefix": "   "}
    monkeypatch.setattr(repository, "prefix_collection", collection)
    assert asyncio.run(repository.get_prefix(1)) == repository.DEFAULT_PREFIX
