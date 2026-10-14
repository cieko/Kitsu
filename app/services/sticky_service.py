import re

import discord


DIRECTIVE_PATTERN = re.compile(
    r"\{(embed|content|color|title|description|footer|thumbnail|image)"
    r"\s*(?::\s*|\})",
    re.IGNORECASE,
)


class StickyService:
    """Parses and renders sticky-message scripts."""

    def render(
        self,
        script: str,
        guild: discord.Guild,
    ) -> tuple[str, discord.Embed | None]:
        matches = list(DIRECTIVE_PATTERN.finditer(script))

        if not matches:
            content = self._resolve(script.strip(), guild) or ""
            self._validate_content(content)
            return content, None

        content_parts: list[str] = []
        fields: dict[str, str] = {}
        embed_requested = False

        prefix = script[:matches[0].start()].strip()
        if prefix:
            content_parts.append(prefix)

        for index, match in enumerate(matches):
            key = match.group(1).lower()

            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(script)
            )

            value = script[match.end():end].strip()

            if value.endswith("}"):
                value = value[:-1].rstrip()

            if key == "embed":
                embed_requested = True
            elif key == "content":
                if value:
                    content_parts.append(value)
            else:
                fields[key] = value

        content = self._resolve(
            "\n".join(content_parts).strip(),
            guild,
        ) or ""

        self._validate_content(content)

        embed_fields = {
            "color",
            "title",
            "description",
            "footer",
            "thumbnail",
            "image",
        }

        embed = None

        if embed_requested or any(
            key in fields for key in embed_fields
        ):
            embed = discord.Embed()

            if fields.get("color"):
                try:
                    embed.color = discord.Color.from_str(
                        fields["color"]
                    )
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        "Invalid embed color. Use a color such as #73052B."
                    ) from exc

            title = self._resolve(fields.get("title"), guild)
            description = self._resolve(
                fields.get("description"),
                guild,
            )

            if title:
                embed.title = title

            if description:
                embed.description = description

            if fields.get("footer"):
                self._set_footer(
                    embed,
                    fields["footer"],
                    guild,
                )

            thumbnail = self._resolve(
                fields.get("thumbnail"),
                guild,
            )
            image = self._resolve(fields.get("image"), guild)

            if thumbnail:
                embed.set_thumbnail(url=thumbnail)

            if image:
                embed.set_image(url=image)

            self._validate_embed(embed)

        return content, embed

    @staticmethod
    def _resolve(
        value: str | None,
        guild: discord.Guild,
    ) -> str | None:
        if value is None:
            return None

        icon_url = guild.icon.url if guild.icon else ""

        return (
            value.replace("{guild.name}", guild.name)
            .replace("{guild.icon}", icon_url)
        )

    def _set_footer(
        self,
        embed: discord.Embed,
        footer: str,
        guild: discord.Guild,
    ):
        has_guild_icon = "{guild.icon}" in footer

        footer_text = footer.replace("{guild.icon}", "")
        footer_text = footer_text.replace(
            "{guild.name}",
            guild.name,
        )
        footer_text = re.sub(
            r"\s+",
            " ",
            footer_text,
        ).strip()

        embed.set_footer(
            text=footer_text or None,
            icon_url=(
                guild.icon.url
                if has_guild_icon and guild.icon
                else None
            ),
        )

    @staticmethod
    def _validate_content(content: str):
        if len(content) > 2000:
            raise ValueError(
                "Message content cannot exceed 2000 characters."
            )

    @staticmethod
    def _validate_embed(embed: discord.Embed):
        if embed.title and len(embed.title) > 256:
            raise ValueError(
                "Embed title cannot exceed 256 characters."
            )

        if embed.description and len(embed.description) > 4096:
            raise ValueError(
                "Embed description cannot exceed 4096 characters."
            )

        if embed.footer.text and len(embed.footer.text) > 2048:
            raise ValueError(
                "Embed footer cannot exceed 2048 characters."
            )

    async def post(
        self,
        channel: discord.TextChannel,
        script: str,
    ) -> discord.Message:
        content, embed = self.render(script, channel.guild)

        if not content and embed is None:
            raise ValueError(
                "The sticky script must contain text or an embed."
            )

        return await channel.send(
            content=content or None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )