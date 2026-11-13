"""Create a Chia NFT sale offer and post it to Dexie.

The NFT coin has to be in the wallet before ``create_offer_for_ids`` can lock it.
A mint often returns the NFT id while the coin is still in the mempool. This waits
until ``nft_get_info`` shows the NFT and ``pending_transaction`` is not set, then
creates the offer once. The wait does not hold the wallet spend lock.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from config import Settings
from services.chia_minter import ChiaMinter
from services.dexie_client import DexieClient, offer_page_url
from utils.bech32m import Bech32Error, nft_id_to_launcher_hex
from utils.errors import ChiaMintError, DexieError, MintBotError

log = logging.getLogger(__name__)

# Wallet text that means the coin is probably still confirming.
_NOT_READY = (
    "not found",
    "unknown nft",
    "unknown",
    "pending",
    "unconfirmed",
    "not available",
    "not spendable",
    "no coins",
    "does not exist",
)

_POLL_SECONDS = 10.0

_NOT_READY_MESSAGE = (
    "The NFT was not ready to offer before the wait ended. "
    "Use /sell once it is in the NFT wallet. "
    "Check the wallet offers first so the same NFT is not listed twice."
)


@dataclass
class SaleResult:
    """Outcome of one offer attempt. ``message`` is safe to show in Discord."""

    ok: bool
    message: str
    offer_id: str | None = None
    offer_url: str | None = None
    offer_path: str | None = None


def offer_error_is_temporary(message: str) -> bool:
    """True when the wallet likely does not have a spendable NFT yet.

    A blank ``Error creating offer:`` (empty, ``None``, or ``null``) is the
    error Chia returns for an NFT that is not in the wallet yet. Timeouts and
    insufficient funds are final. Other ``Error creating offer`` details are
    final too.
    """
    lower = " ".join(message.lower().split())
    if any(phrase in lower for phrase in ("timed out", "timeout", "insufficient", "not enough")):
        return False
    if any(phrase in lower for phrase in _NOT_READY):
        return True
    marker = "error creating offer:"
    index = lower.rfind(marker)
    if index < 0:
        return False
    detail = lower[index + len(marker) :].strip(" .:'\"")
    return detail in {"", "none", "null"}


def _fatal_sale_error(message: str) -> bool:
    """Stop on timeouts, a short balance, or a wallet the bot cannot reach."""
    lower = message.lower()
    return any(
        phrase in lower
        for phrase in (
            "timed out",
            "timeout",
            "insufficient",
            "not enough",
            "connection refused",
            "failed to establish",
            "could not reach",
            "certificate",
            "ssl",
        )
    )


def nft_info_is_spendable(info: object) -> bool:
    """True when ``nft_get_info`` returned an NFT that is not mid-spend."""
    if not isinstance(info, dict):
        return False
    return info.get("pending_transaction") is not True


async def publish_sale_async(
    settings: Settings,
    minter: ChiaMinter,
    dexie: DexieClient,
    spend_lock: asyncio.Lock,
    *,
    nft_id: str,
    price_mojos: int,
) -> SaleResult:
    """Wait until the NFT is spendable, create one offer, and post it to Dexie.

    ``spend_lock`` is held only around the offer creation. Polling the wallet
    and the Dexie post stay outside that lock. A failure leaves any mint intact.
    """
    try:
        launcher_hex = nft_id_to_launcher_hex(nft_id)
    except Bech32Error as exc:
        return SaleResult(ok=False, message=str(exc))

    deadline = time.monotonic() + settings.offer_wait_seconds
    while True:
        # A failed info call that is not "still confirming" still tries to create
        # the offer. /sell uses this path, and a ready NFT should not wait out
        # the whole timeout because nft_get_info phrased its error differently.
        info_failed = False
        try:
            info = await asyncio.to_thread(minter.nft_get_info, nft_id)
        except ChiaMintError as exc:
            text = str(exc)
            if not offer_error_is_temporary(text):
                if _fatal_sale_error(text) or time.monotonic() >= deadline:
                    return SaleResult(ok=False, message=text)
                log.warning("nft_get_info for %s failed: %s", nft_id, text)
                info_failed = True
                info = None
            else:
                log.info("NFT %s could not be read yet: %s", nft_id, text)
                info = None

        if nft_info_is_spendable(info) or info_failed:
            created = await _create_offer(
                settings,
                minter,
                dexie,
                spend_lock,
                nft_id=nft_id,
                launcher_hex=launcher_hex,
                price_mojos=price_mojos,
                deadline=deadline,
            )
            if created is not None:
                return created
        else:
            log.info("NFT %s is not ready to offer yet.", nft_id)

        if time.monotonic() >= deadline:
            return SaleResult(ok=False, message=_NOT_READY_MESSAGE)
        remaining = deadline - time.monotonic()
        await asyncio.sleep(min(_POLL_SECONDS, max(0.0, remaining)))


async def _create_offer(
    settings: Settings,
    minter: ChiaMinter,
    dexie: DexieClient,
    spend_lock: asyncio.Lock,
    *,
    nft_id: str,
    launcher_hex: str,
    price_mojos: int,
    deadline: float,
) -> SaleResult | None:
    """Create and post one offer. Return None to keep waiting.

    The spend lock covers the existing-offer check and ``create_offer_for_ids``
    only. The offer file and the Dexie post run after the lock is released.
    """
    async with spend_lock:
        try:
            already = await asyncio.to_thread(minter.find_open_offer_launcher, launcher_hex)
        except ChiaMintError as exc:
            log.warning("Could not list wallet offers before creating one: %s", exc)
            already = False
        if already:
            return SaleResult(
                ok=False,
                message=(
                    "This NFT already has an open offer in the wallet, so another one was not created. "
                    "Check the wallet offers before listing it again."
                ),
            )
        try:
            offer = await asyncio.to_thread(
                minter.create_sale_offer,
                launcher_hex=launcher_hex,
                price_mojos=price_mojos,
            )
        except ChiaMintError as exc:
            if not offer_error_is_temporary(str(exc)) or time.monotonic() >= deadline:
                return SaleResult(ok=False, message=str(exc))
            log.info("NFT %s is not ready to offer yet. Waiting.", nft_id)
            return None

    return await asyncio.to_thread(_publish_offer_text, settings, dexie, nft_id, offer)


def _publish_offer_text(settings: Settings, dexie: DexieClient, nft_id: str, offer: str) -> SaleResult:
    """Write the offer file, then post it. The file remains if Dexie rejects it."""
    path = _write_offer(settings.offers_dir, nft_id, offer)
    try:
        offer_id = dexie.post_offer(offer)
    except DexieError as exc:
        return SaleResult(
            ok=False,
            message=f"{exc} Saved the offer at {path}.",
            offer_path=str(path),
        )
    except MintBotError as exc:
        return SaleResult(ok=False, message=str(exc), offer_path=str(path))
    return SaleResult(
        ok=True,
        message="The sale offer is on Dexie.",
        offer_id=offer_id,
        offer_url=offer_page_url(settings.dexie_offer_url, offer_id),
        offer_path=str(path),
    )


def _write_offer(directory: Path, nft_id: str, offer: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9]+", "", nft_id)[:80] or "offer"
    path = directory / f"{stem}.offer"
    path.write_text(offer, encoding="utf-8")
    log.info("Wrote offer file %s", path)
    return path
