from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.services.ghosty_prediction import GhostyPredictor

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


NOW = BASE + timedelta(days=30)


def snowflake(stamp):
    return (int(stamp.timestamp() * 1000) - 1420070400000) << 22


CHANNEL = snowflake(BASE - timedelta(days=30))


def history(outcomes):
    return [
        {
            "channel_id": str(CHANNEL),
            "created_at": BASE + timedelta(minutes=i),
            "result_message_id": str(i + 1),
            "outcome": outcome,
        }
        for i, outcome in enumerate(outcomes)
    ]


@pytest.fixture(scope="module")
def new_channel_model():
    random = np.random.default_rng(42)
    rows = []
    for i in range(1200):
        stamp = BASE + timedelta(minutes=30 * i)
        fresh = i % 2 == 0
        created = stamp - (timedelta(hours=1) if fresh else timedelta(days=30))
        rows.append(
            {
                "channel_id": str(snowflake(created)),
                "created_at": stamp,
                "result_message_id": str(i + 1),
                "outcome": "hit" if random.random() < (0.75 if fresh else 0.05) else "miss",
            }
        )
    return GhostyPredictor(rows, now=NOW)
