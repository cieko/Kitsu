"""Local LLM reactions to a completed Ghosty streak's net result."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp


logger = logging.getLogger(__name__)
DEFAULT_MODEL_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL_NAME = "kitsu_mod"
FALLBACK_CELEBRATION = "Hurrah! A little Ghosty magic landed — Ghosty says you're welcome! 🎉"
FALLBACK_LOSS = "Ghosty says you're welcome. Apparently that doesn't include covering the whole tab—this streak still ended at a loss. 😅"
FALLBACK_BREAK_EVEN = "Ghosty says you're welcome—after all that suspense, we're exactly where we started. 😅"
CELEBRATION_PROMPT = (
    "You are Kitsu, a playful Discord companion reacting to a completed Ghosty streak. "
    "A payout does not necessarily mean the streak made a profit. Match the NET result supplied by the user. "
    "Write one short English reaction, at most 35 words. You may play with the idea "
    "that Ghosty says 'you're welcome'. Return only the reaction, with at most one appropriate emoji. "
    "Do not include names, mentions, numbers, amounts, odds, links, headings, or explanations. "
    "The bot adds the verified net total separately. Never insult, blame, or shame the player. "
    "Never promise future wins, encourage chasing losses, or ask anyone to gamble again."
)
OUTCOME_PROMPTS = {
    "win": "The streak ended with a NET WIN. Be briefly cheerful about the actual overall profit.",
    "loss": (
        "The streak ended with a NET LOSS despite Ghosty's payout. Acknowledge that it is still a loss overall. "
        "Use gentle, dry sarcasm aimed at Ghosty's supposed generosity, with warmth toward the player. "
        "Do not celebrate, congratulate, cheer, say hurrah, claim victory, or use party emojis. "
        "An understated 😅 or 🙃 is fine."
    ),
    "break_even": (
        "The streak ended exactly BREAK-EVEN: no net profit and no net loss. "
        "Use a neutral, lightly wry reaction to ending back where we started. Do not celebrate a win or claim a loss."
    ),
}
CHEERFUL_RE = re.compile(
    r"\b(?:hurrah|hooray|hurray|congrats|congratulations|kudos|jackpot|victory|celebrat\w*|"
    r"crushed\s+it|well\s+done|amazing|awesome|fantastic|wonderful|won|win(?:ning)?|profit\w*)\b|[🎉🎊🥳🏆🥂🍾]",
    re.IGNORECASE,
)


def hit_outcome(net_result: int) -> str:
    return "loss" if net_result < 0 else "win" if net_result > 0 else "break_even"


def hit_reaction_fallback(net_result: int) -> str:
    return {"win": FALLBACK_CELEBRATION, "loss": FALLBACK_LOSS, "break_even": FALLBACK_BREAK_EVEN}[hit_outcome(net_result)]


class AIService:
    def __init__(
        self, *, model_url: str = DEFAULT_MODEL_URL,
        model_name: str = DEFAULT_MODEL_NAME, timeout: float = 30, num_gpu: int | None = None,
    ) -> None:
        base = (model_url or "").strip().rstrip("/") or DEFAULT_MODEL_URL
        self.chat_url = base if base.endswith("/api/chat") else f"{base.removesuffix('/api')}/api/chat"
        self.model_name = (model_name or DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME
        self.timeout = max(1.0, float(timeout))
        self.num_gpu = num_gpu
        self._generation_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, config: Any = None) -> "AIService":
        if config is None:
            from app.config import settings
            config = settings
        return cls(
            model_url=config.ai_model_url, model_name=config.ai_model_name,
            timeout=config.ai_model_timeout,
            num_gpu=getattr(config, "ai_model_num_gpu", None),
        )

    async def celebrate_hit(self, *, net_result: int) -> str:
        """Match the streak's net outcome; the payout alone is not a win."""
        try:
            # Bound waiting for the local model as well as the HTTP request.
            return await asyncio.wait_for(self._generate_celebration(hit_outcome(net_result)), timeout=self.timeout)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
            logger.warning("Ghosty reaction unavailable or unsuitable; using the outcome-specific fallback.")
            return hit_reaction_fallback(net_result)

    async def _generate_celebration(self, outcome: str) -> str:
        # A small prompt batch keeps GPU scratch allocations modest alongside
        # the explicitly configured layer count. Requests stay serialized.
        options = {"temperature": 0.85, "num_predict": 80, "num_ctx": 1024, "num_batch": 64}
        if self.num_gpu is not None:
            options["num_gpu"] = self.num_gpu
        async with self._generation_lock:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                async with session.post(self.chat_url, json={
                    "model": self.model_name,
                    "messages": [
                        {"role": "system", "content": CELEBRATION_PROMPT},
                        {"role": "user", "content": OUTCOME_PROMPTS[outcome]},
                    ],
                    "stream": False, "keep_alive": "5m",
                    "options": options,
                }) as response:
                    response.raise_for_status()
                    payload = await response.json()
        content = payload["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Missing model text")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = " ".join(content.split()).strip('"')
        if not content or len(content) > 350 or re.search(r"\d|https?://|<@|@everyone|@here", content):
            raise ValueError("Unusable celebration text")
        if re.search(r"\b(?:idiot|stupid|fool|moron|loser)\b", content, re.IGNORECASE):
            raise ValueError("Keep sarcasm kind toward the player")
        if outcome == "win" and re.search(r"\b(?:loss|lost|break[- ]?even|broke even)\b", content, re.IGNORECASE):
            raise ValueError("Loss or break-even wording does not match a net win")
        if outcome != "win" and CHEERFUL_RE.search(content):
            raise ValueError("Celebratory wording does not match the net result")
        if outcome == "loss" and not re.search(r"\b(?:loss|lost|down|red|short|deficit)\b", content, re.IGNORECASE):
            raise ValueError("The reaction must acknowledge the net loss")
        if outcome == "break_even" and not re.search(r"\b(?:break[- ]?even|broke even|even|square|where we started)\b", content, re.IGNORECASE):
            raise ValueError("The reaction must acknowledge breaking even")
        return content
