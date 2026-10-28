import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def import_app(monkeypatch, tmp_path):
    """Import application code without reading credentials or opening MongoDB."""
    session = ModuleType("app.database.session")
    session.db = MagicMock()
    session.client = MagicMock()
    monkeypatch.setitem(sys.modules, "app.database.session", session)
    training = importlib.import_module("app.services.ghosty_training")
    monkeypatch.setattr(training, "TRAINING_DATA_DIRECTORY", tmp_path / "ghosty")
    return importlib.import_module
