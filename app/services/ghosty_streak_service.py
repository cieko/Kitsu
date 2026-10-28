"""Completed channel streaks and a chart of saved hit possibilities."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import math
import threading
from typing import Any

import numpy as np

from app.database.repositories import ghosty_repository as repository
from app.services.ghosty_prediction import MAX_HIT_CANDIDATES


_PLOT_LOCK = threading.Lock()
BACKGROUND, TEXT, MUTED = "#202127", "#F2F3F5", "#B5BAC1"
SELECTED, OTHER, MISSED = "#7CC6B0", "#B7A6E8", "#737781"


@dataclass(frozen=True)
class CompletedStreak:
    hit_at: int
    loss: int
    payout: int

    @property
    def net(self) -> int:
        return self.payout - self.loss


def completed_streaks(history: list[dict[str, Any]], *, limit: int = 5) -> list[CompletedStreak]:
    """Summarize known hit-to-hit runs, newest first; skip the partial first run."""
    known_start, misses, loss = False, 0, 0
    completed = []
    for row in history:
        if row["outcome"] == "hit":
            if known_start:
                completed.append(CompletedStreak(misses + 1, loss, int(row.get("payout", 0))))
            known_start, misses, loss = True, 0, 0
        elif row["outcome"] == "miss" and known_start:
            misses += 1
            loss += int(row.get("wager", 0))
    return list(reversed(completed[-max(1, min(limit, 5)):]))


@dataclass
class StreakSnapshot:
    now: datetime
    stats: dict[str, Any]
    previous_attempts: list[CompletedStreak]
    prediction: dict[str, Any]


class GhostyStreakService:
    def __init__(self, tracker: Any):
        self.tracker = tracker

    async def load(self, guild_id: int, channel_id: int) -> StreakSnapshot:
        now = datetime.now(timezone.utc)
        history, stats, minimum = await asyncio.gather(
            repository.get_completed_streak_history(guild_id, channel_id, now=now, limit=5),
            self.tracker.get_stats(guild_id, channel_id),
            repository.get_config(guild_id, "minimum_samples", 50),
        )
        prediction = await self.tracker.get_prediction_data(
            guild_id, channel_id, minimum_samples=int(minimum), stats=stats,
        )
        return StreakSnapshot(now, stats, completed_streaks(history), prediction)


def chart_candidates(snapshot: StreakSnapshot) -> list[dict[str, Any]]:
    """Use the same frozen candidates as the tracker, never a new fitted curve."""
    if not snapshot.prediction.get("ready"):
        return []
    candidates = snapshot.prediction.get("hit_candidates", [])
    valid = [candidate for candidate in candidates if (
        type(candidate.get("attempt")) is int and candidate["attempt"] > 0
        and type(candidate.get("probability")) in (int, float)
        and math.isfinite(candidate["probability"]) and 0 < candidate["probability"] <= 1
    )]
    ranked = sorted(valid, key=lambda candidate: (-candidate["probability"], candidate["attempt"]))
    return sorted(ranked[:MAX_HIT_CANDIDATES], key=lambda candidate: candidate["attempt"])


def render_streak_chart(snapshot: StreakSnapshot) -> bytes:
    """Render orbital-style lobes for discrete saved outcomes, off the event loop."""
    with _PLOT_LOCK:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        figure = Figure(figsize=(8, 3.2), dpi=150, facecolor=BACKGROUND)
        FigureCanvasAgg(figure)
        ax = figure.subplots()
        ax.set_facecolor(BACKGROUND)
        ax.tick_params(colors=TEXT, labelsize=18, axis="x", length=0, pad=12)
        ax.yaxis.set_visible(False)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title("Possible hit attempts", color=TEXT, loc="left", fontsize=19, pad=18)
        candidates = chart_candidates(snapshot)
        if not candidates:
            ax.text(0.5, 0.5, "Still learning", ha="center", va="center", color=MUTED,
                    fontsize=18, transform=ax.transAxes)
            ax.set_xticks([])
        else:
            streak = max(0, int(snapshot.stats.get("streak", 0)))
            selected = snapshot.prediction.get("hit_at")
            highest = max(candidate["probability"] for candidate in candidates) * 100
            # Each x position is one labelled outcome, not an interpolated
            # attempt range. Separate lobes keep neighboring attempts readable.
            ax.axhline(0, color="#42454F", linewidth=1)
            for index, candidate in enumerate(candidates):
                attempt, chance = candidate["attempt"], candidate["probability"] * 100
                missed = attempt <= streak or candidate.get("status") == "missed"
                color = MISSED if missed else SELECTED if attempt == selected else OTHER
                x = np.linspace(index - 0.47, index + 0.47, 180)
                height = chance * np.exp(-0.5 * ((x - index) / 0.18) ** 2)
                for scale, alpha in ((1, 0.10), (0.8, 0.12), (0.55, 0.18), (0.25, 0.25)):
                    ax.fill_between(x, -height * scale, height * scale, color=color, alpha=alpha)
                ax.plot(x, height, color=color, linewidth=1.5, alpha=0.8)
                ax.plot(x, -height, color=color, linewidth=1.5, alpha=0.8)
                percent = "<0.1%" if chance < 0.1 else f"{chance:.1f}".rstrip("0").rstrip(".") + "%"
                ax.text(index, chance + highest * 0.1, percent, color=color, ha="center", fontsize=18)
            labels = [f"@ {item['attempt']:,}" + (" ×" if item["attempt"] <= streak or item.get("status") == "missed" else "")
                      for item in candidates]
            ax.set_xticks(range(len(candidates)), labels)
            ax.set_xlim(-0.65, len(candidates) - 0.35)
            ax.set_ylim(-highest * 1.2, highest * 1.4)
        figure.tight_layout(pad=1.8)
        output = BytesIO()
        figure.savefig(output, format="png", facecolor=BACKGROUND)
        figure.clear()
        return output.getvalue()
