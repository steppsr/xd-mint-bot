"""Watch for the seedling reaction and open a mint session.

``on_raw_reaction_add`` plus ``fetch_message`` works for messages that are
not in the bot's cache, including older Midjourney posts.
"""

from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord.ext import commands

from handlers.metadata_flow import PromptView, build_start_embed
from handlers.sessions import MintDraft, MintSession, remove_temp_file, session_key
from utils.errors import ValidationError
from utils.format import format_bytes, safe_filename
from utils.hash_utils import sha256_bytes
from utils.prompts import DEFAULT_DESCRIPTION, default_nft_description, extract_midjourney_prompt

log = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}


class ReactionCog(commands.Cog):
    """Seedling reaction listener."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        try:
            await self._on_reaction(payload)
        except Exception:
            log.exception("Unhandled error while handling a reaction")

    async def _on_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        bot: Any = self.bot
        if not _should_consider(bot, payload):
            return
        if payload.guild_id is None:
            return

        settings = bot.settings
        active = bot.collections.active()
        if active is None:
            await _brief_reply(
                bot,
                None,
                payload.channel_id,
                payload.message_id,
                "No collection is active. Use /collection create, then react with the seedling again.",
            )
            return
        key = session_key(payload.channel_id, payload.message_id)
        session = MintSession(
            key=key,
            user_id=payload.user_id,
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            source_message_id=payload.message_id,
            filename="image",
            content_type="application/octet-stream",
            source_image_url="",
            defaults=MintDraft(
                name="Untitled NFT",
                description=DEFAULT_DESCRIPTION,
                collection_id=active.id,
                collection_name=active.name,
                royalty_hundredths=active.royalty_hundredths,
            ),
        )
        # Reserve before the first await so two reactions cannot both pass.
        reason = bot.sessions.reserve(session, cooldown=settings.reaction_cooldown_seconds)
        if reason:
            await _brief_reply(bot, None, payload.channel_id, payload.message_id, reason)
            return

        reserved = True
        try:
            await self._prepare(bot, session, payload)
            reserved = False
        except ValidationError as exc:
            await _brief_reply(bot, None, payload.channel_id, payload.message_id, str(exc))
        except discord.Forbidden:
            log.error(
                "Missing permission to read or reply in channel %s. The bot needs View Channel, "
                "Read Message History, Send Messages, Embed Links, and Add Reactions.",
                payload.channel_id,
            )
        except discord.NotFound:
            log.info("Message %s in channel %s disappeared before the mint could start", payload.message_id, payload.channel_id)
        except Exception:
            log.exception("Failed to prepare a mint for message %s", payload.message_id)
            await _brief_reply(
                bot,
                None,
                payload.channel_id,
                payload.message_id,
                "Something went wrong while preparing that mint. The details are in the bot log.",
            )
        finally:
            if reserved:
                dropped = bot.sessions.pop(key)
                if dropped is not None:
                    _stop(dropped.active_view)
                    remove_temp_file(dropped.image_path)

    async def _prepare(self, bot: Any, session: MintSession, payload: discord.RawReactionActionEvent) -> None:
        channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        images = [attachment for attachment in message.attachments if _is_image(attachment)]
        if not images:
            raise ValidationError("That message has no PNG, JPG, or WEBP image.")
        if bot.settings.require_midjourney and not _looks_like_midjourney(message, bot.settings.midjourney_bot_ids):
            raise ValidationError(
                "That message does not look like a Midjourney image. "
                "REQUIRE_MIDJOURNEY is on. Set MIDJOURNEY_BOT_IDS, or turn that check off."
            )

        attachment = images[0]
        data = await _read_attachment(bot, attachment)
        filename = safe_filename(attachment.filename or "image.png")
        bot.settings.temp_dir.mkdir(parents=True, exist_ok=True)
        path = bot.settings.temp_dir / f"{uuid.uuid4().hex}_{filename}"
        session.image_path = path
        await asyncio.to_thread(path.write_bytes, data)

        prompt = extract_midjourney_prompt(message.content or "")
        session.filename = attachment.filename or filename
        session.content_type = _content_type(attachment)
        session.source_image_url = attachment.url
        session.image_hash = sha256_bytes(data)
        session.image_bytes = len(data)
        session.extra_image_count = max(0, len(images) - 1)
        session.content_was_empty = not (message.content or "").strip()
        active = bot.collections.active()
        if active is None:
            raise ValidationError("No collection is active. Use /collection create, then react with the seedling again.")
        session.defaults = MintDraft(
            name=str(active.next_number),
            description=default_nft_description(prompt),
            collection_id=active.id,
            collection_name=active.name,
            royalty_hundredths=active.royalty_hundredths,
            automatic_name=True,
        )

        view = PromptView(bot, session.key)
        session.active_view = view
        prompt_message = await message.reply(
            content=f"<@{session.user_id}> started a Chia mint for this image.",
            embed=build_start_embed(bot, session),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        session.prompt_message_id = prompt_message.id
        session.created_at = time.monotonic()
        session.state = "awaiting_metadata"
        log.info(
            "Opened mint %s for user %s file=%s bytes=%s hash=%s",
            session.key,
            session.user_id,
            session.filename,
            session.image_bytes,
            session.image_hash,
        )


def _should_consider(bot: Any, payload: discord.RawReactionActionEvent) -> bool:
    """Ignore the bot, other emoji, other servers, and unauthorized users."""
    user = bot.user
    if user is not None and payload.user_id == user.id:
        return False
    if payload.guild_id is None:
        return False
    if not _emoji_matches(payload.emoji, bot.settings.seedling_emoji):
        return False
    allowed = bot.settings.allowed_guild_ids
    if allowed and payload.guild_id not in allowed:
        log.debug("Ignoring seedling reaction in guild %s", payload.guild_id)
        return False
    if payload.user_id not in bot.settings.authorized_user_ids:
        log.debug("Ignoring seedling reaction from unauthorized user %s", payload.user_id)
        return False
    return True


def _emoji_matches(emoji: discord.PartialEmoji, expected: str) -> bool:
    wanted = unicodedata.normalize("NFC", expected.strip())
    name = unicodedata.normalize("NFC", emoji.name or "")
    if name == wanted or str(emoji) == wanted:
        return True
    if wanted.startswith(":") and wanted.endswith(":") and name == wanted.strip(":"):
        return True
    return False


def _is_image(attachment: discord.Attachment) -> bool:
    suffix = Path(attachment.filename or "").suffix.lower()
    content_type = (attachment.content_type or "").split(";", 1)[0].strip().lower()
    return suffix in _IMAGE_EXTENSIONS or content_type in _IMAGE_TYPES


def _looks_like_midjourney(message: discord.Message, bot_ids: frozenset[int]) -> bool:
    if message.author.id in bot_ids:
        return True
    content = message.content or ""
    return any(marker in content for marker in ("Upscaled", "Image #", "**"))


def _content_type(attachment: discord.Attachment) -> str:
    suffix = Path(attachment.filename or "").suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    raw = (attachment.content_type or "").split(";", 1)[0].strip().lower()
    if raw == "image/jpg":
        return "image/jpeg"
    return raw or "application/octet-stream"


async def _read_attachment(bot: Any, attachment: discord.Attachment) -> bytes:
    limit = bot.settings.max_image_bytes
    if attachment.size and attachment.size > limit:
        raise ValidationError(
            f"That image is {format_bytes(attachment.size)}. The limit is {format_bytes(limit)}."
        )
    try:
        data = await attachment.read()
    except discord.HTTPException as exc:
        log.warning("Discord attachment read failed, trying the CDN URL: %s", exc)
        data = await _download_url(bot, attachment.url, limit)
    if not data:
        raise ValidationError("The image download was empty.")
    if len(data) > limit:
        raise ValidationError(f"That image is larger than {format_bytes(limit)}.")
    return data


async def _download_url(bot: Any, url: str, limit: int) -> bytes:
    session = bot.http_session
    if session is None:
        raise ValidationError("The bot is still starting. Try the reaction again in a moment.")
    try:
        async with session.get(url) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ValidationError(f"That image is larger than {format_bytes(limit)}.")
                chunks.append(chunk)
            return b"".join(chunks)
    except ValidationError:
        raise
    except aiohttp.ClientError as exc:
        raise ValidationError("The image could not be downloaded from Discord.") from exc


async def _brief_reply(
    bot: Any,
    message: discord.Message | None,
    channel_id: int,
    message_id: int,
    text: str,
) -> None:
    """Reply to the source message and delete the note after a few seconds."""
    try:
        if message is None:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
        note = await message.reply(text, mention_author=False)
    except discord.HTTPException:
        log.warning("Could not send a mint notice in channel %s", channel_id, exc_info=True)
        return

    async def _delete_later() -> None:
        await asyncio.sleep(12)
        try:
            await note.delete()
        except discord.HTTPException:
            pass

    asyncio.create_task(_delete_later())


def _stop(view: object | None) -> None:
    if view is not None and hasattr(view, "stop"):
        view.stop()
