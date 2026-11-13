"""Small display helpers shared by the mint flow and the status command."""

from __future__ import annotations

import re
from decimal import Decimal

MOJOS_PER_XCH = 1_000_000_000_000


def format_xch(mojos: int) -> str:
    """Render a mojo amount as a compact XCH string."""
    value = Decimal(mojos) / Decimal(MOJOS_PER_XCH)
    text = f"{value:.12f}".rstrip("0").rstrip(".")
    return text or "0"


def format_percent(hundredths: int) -> str:
    """Render Chia royalty hundredths (300) as a percent string ("3")."""
    value = Decimal(hundredths) / Decimal(100)
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def format_bytes(size: int) -> str:
    """Render a byte count for Discord messages."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def truncate(text: str, limit: int) -> str:
    """Shorten text so it fits a Discord field or modal."""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return cleaned[:limit]
    return cleaned[: limit - 1].rstrip() + "…"


def truncate_words(text: str, limit: int) -> str:
    """Shorten text on a word boundary when one fits."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rsplit(" ", 1)[0].strip()
    return cut or cleaned[:limit].strip()


def discord_message_url(guild_id: int, channel_id: int, message_id: int) -> str:
    """Build the web URL for a guild message."""
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def collection_object_prefix(name: str, collection_id: str) -> str:
    """Return the Filebase folder name for one collection.

    The bucket browser treats the text before the first slash as a folder.
    Slashes and control characters in the collection name are removed so the
    name stays a single folder.
    """
    cleaned = re.sub(r"[\x00-\x1f/\\]+", " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = collection_id
    return cleaned[:100]


def collection_object_key(name: str, collection_id: str, filename: str) -> str:
    """Return the Filebase key for one file inside a collection folder."""
    return f"{collection_object_prefix(name, collection_id)}/{filename}"


def collection_image_key(name: str, collection_id: str, number: int, extension: str) -> str:
    """Return the Filebase key for one minted image, such as ``SampleCollection/images/1.png``."""
    return f"{collection_object_prefix(name, collection_id)}/images/{number}{extension}"


def collection_metadata_key(name: str, collection_id: str, number: int) -> str:
    """Return the Filebase key for one minted metadata file, such as ``SampleCollection/metadata/1.json``."""
    return f"{collection_object_prefix(name, collection_id)}/metadata/{number}.json"


def safe_filename(name: str) -> str:
    """Reduce an attachment name to characters that are safe in a temp path."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return cleaned[:80] or "image"


def mask_secret(value: str) -> str:
    """Keep a short prefix and suffix for logs."""
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}…{value[-4:]}"
