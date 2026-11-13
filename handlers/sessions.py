"""In-memory mint sessions.

One bot process owns this dictionary. A second process would not see these
sessions and could mint the same image again, so run a single instance.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from utils.format import discord_message_url

log = logging.getLogger(__name__)

# Form states block another seedling. Queued, uploading, and minting stay
# reserved on this image and still let the user start a different one.
ACTIVE_STATES = frozenset({"preparing", "awaiting_metadata", "confirming"})
EXPIRABLE_STATES = frozenset({"awaiting_metadata", "confirming"})


@dataclass(frozen=True)
class MintDraft:
    """Metadata the user can still edit before the upload starts."""

    name: str
    description: str
    collection_id: str
    collection_name: str
    royalty_hundredths: int
    attributes: tuple[tuple[str, str], ...] = ()
    sale_price_mojos: int | None = None
    automatic_name: bool = False


@dataclass
class MintSession:
    """One seedling reaction, from download through mint or cancel."""

    key: str
    user_id: int
    guild_id: int
    channel_id: int
    source_message_id: int
    filename: str
    content_type: str
    source_image_url: str
    defaults: MintDraft
    state: str = "preparing"
    image_path: Path | None = None
    image_hash: str = ""
    image_bytes: int = 0
    extra_image_count: int = 0
    prompt_message_id: int | None = None
    draft: MintDraft | None = None
    active_view: object | None = None
    content_was_empty: bool = False
    created_at: float = field(default_factory=time.monotonic)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)

    @property
    def lock(self) -> asyncio.Lock:
        """Serialize button clicks for this mint. Created on first use inside the running loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def current_draft(self) -> MintDraft:
        """Return the latest valid draft, or the prefilled defaults."""
        return self.draft or self.defaults


def session_key(channel_id: int, message_id: int) -> str:
    """Identify the source message that started a mint."""
    return f"{channel_id}:{message_id}"


class SessionStore:
    """Track open mints and the per-user reaction cooldown."""

    def __init__(self) -> None:
        self._items: dict[str, MintSession] = {}
        self._cooldowns: dict[int, float] = {}

    def get(self, key: str) -> MintSession | None:
        return self._items.get(key)

    def pop(self, key: str) -> MintSession | None:
        return self._items.pop(key, None)

    def drain(self) -> list[MintSession]:
        """Remove every session. Used when the bot shuts down."""
        items = list(self._items.values())
        self._items.clear()
        return items

    def reserve(self, session: MintSession, *, cooldown: float, now: float | None = None) -> str | None:
        """Insert a preparing session, or return a user-facing rejection.

        Call this with no ``await`` between the authorization check and this
        method. Asyncio only switches tasks at ``await``, so the check and
        insert stay atomic.
        """
        moment = time.monotonic() if now is None else now
        existing = self._items.get(session.key)
        if existing is not None:
            if existing.prompt_message_id is not None:
                url = discord_message_url(
                    existing.guild_id,
                    existing.channel_id,
                    existing.prompt_message_id,
                )
                return f"A mint is already open for this image: {url}"
            return "A mint is already open for this image."
        for open_session in self._items.values():
            if open_session.user_id == session.user_id and open_session.state in ACTIVE_STATES:
                return "You already have a mint open. Finish or cancel it before starting another."
        if cooldown > 0:
            last = self._cooldowns.get(session.user_id)
            if last is not None:
                remaining = cooldown - (moment - last)
                if remaining > 0:
                    return f"Please wait {math.ceil(remaining)} seconds before starting another mint."
        self._items[session.key] = session
        self._cooldowns[session.user_id] = moment
        return None

    def expired(self, max_age: float, now: float | None = None) -> list[MintSession]:
        """Return confirmations that have sat longer than the session timeout."""
        moment = time.monotonic() if now is None else now
        return [
            session
            for session in list(self._items.values())
            if session.state in EXPIRABLE_STATES and moment - session.created_at >= max_age
        ]


def remove_temp_file(path: Path | None) -> None:
    """Delete a downloaded image. Missing files are ignored."""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not delete temporary file %s: %s", path, exc)


def sweep_old_temp_files(temp_dir: Path, max_age_seconds: int = 86_400) -> None:
    """Remove leftovers from a previous process that stopped mid-mint."""
    if not temp_dir.exists():
        return
    now = time.time()
    for path in temp_dir.iterdir():
        if not path.is_file() or path.name == ".gitkeep":
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age >= max_age_seconds:
            remove_temp_file(path)
