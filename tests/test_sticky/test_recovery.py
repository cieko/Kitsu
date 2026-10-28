from __future__ import annotations

import asyncio

from .helpers import environment, interaction


def test_slash_commands_enforce_administrator_permissions_at_runtime(import_app, monkeypatch):
    module = import_app("app.cogs.sticky")

    async def check():
        env = environment(module, monkeypatch)
        assert env.cog.app_command.default_permissions.administrator
        assert not await env.cog.add._check_can_run(interaction(env, admin=False))
        assert await env.cog.add._check_can_run(interaction(env))

    asyncio.run(check())
