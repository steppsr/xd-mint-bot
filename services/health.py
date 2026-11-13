"""Connectivity checks shared by ``--check`` and ``/nftstatus``."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import Settings
from services.chia_minter import ChiaMinter
from services.filebase_uploader import FilebaseUploader
from utils.errors import MintBotError
from utils.format import format_xch

log = logging.getLogger(__name__)


@dataclass
class Check:
    """One setup check and the sentence shown to the operator."""

    name: str
    ok: bool
    detail: str


@dataclass
class HealthReport:
    """The full pre-flight report."""

    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.checks)


def build_health_report(settings: Settings, minter: ChiaMinter, uploader: FilebaseUploader) -> HealthReport:
    """Probe Chia and Filebase. Individual failures become report lines."""
    checks = [
        _run("Chia transport", minter.ensure_transport, ok_detail="Wallet RPC transport is available."),
        _run("Chia sync", lambda: _sync_line(minter)),
        _run("Chia wallets", lambda: _wallet_line(settings, minter)),
        _run("NFT wallet", lambda: _nft_count_line(settings, minter)),
        _run("Fee balance", lambda: _balance_line(settings, minter)),
        _run("Filebase", uploader.check_bucket),
    ]
    return HealthReport(checks=checks)


def _run(name: str, fn, ok_detail: str | None = None) -> Check:
    try:
        detail = fn()
    except Exception as exc:
        log.warning("Health check %s failed", name, exc_info=True)
        if isinstance(exc, MintBotError):
            message = str(exc)
        else:
            message = f"{exc.__class__.__name__}: {exc}"
        return Check(name, False, message[:400])
    if detail is None:
        detail = ok_detail or "ok"
    return Check(name, True, str(detail))


def _sync_line(minter: ChiaMinter) -> str:
    status = minter.rpc("get_sync_status", {})
    synced = bool(status.get("synced"))
    syncing = bool(status.get("syncing"))
    if not synced:
        raise MintBotError(f"Wallet synced={synced} syncing={syncing}. Wait for sync before minting.")
    return f"Wallet is synced (syncing={syncing})."


def _wallet_line(settings: Settings, minter: ChiaMinter) -> str:
    data = minter.rpc("get_wallets", {})
    wallets = data.get("wallets")
    if not isinstance(wallets, list):
        raise MintBotError("The wallet list response did not include wallets.")
    ids = {int(wallet["id"]) for wallet in wallets if isinstance(wallet, dict) and "id" in wallet}
    missing = [
        label
        for label, wallet_id in (
            ("NFT wallet", settings.chia_nft_wallet_id),
            ("fee wallet", settings.chia_fee_wallet_id),
        )
        if wallet_id not in ids
    ]
    if missing:
        raise MintBotError(f"Missing {', '.join(missing)}. Wallet ids on this key: {sorted(ids)}.")
    return f"NFT wallet {settings.chia_nft_wallet_id} and fee wallet {settings.chia_fee_wallet_id} exist."


def _nft_count_line(settings: Settings, minter: ChiaMinter) -> str:
    data = minter.rpc("nft_count_nfts", {"wallet_id": settings.chia_nft_wallet_id})
    count = data.get("count")
    if count is None:
        return f"NFT wallet {settings.chia_nft_wallet_id} answered nft_count_nfts."
    return f"NFT wallet {settings.chia_nft_wallet_id} holds {count} NFT(s)."


def _balance_line(settings: Settings, minter: ChiaMinter) -> str:
    balance = minter.spendable_mojos(settings.chia_fee_wallet_id)
    fee = format_xch(settings.chia_fee_mojos)
    spendable = format_xch(balance)
    if balance < settings.chia_fee_mojos and not settings.dry_run:
        raise MintBotError(f"Spendable balance is {spendable} XCH and the mint fee is {fee} XCH.")
    if balance < settings.chia_fee_mojos:
        return f"Spendable balance is {spendable} XCH. Dry run is on, so the {fee} XCH fee is not spent."
    return f"Spendable balance is {spendable} XCH. The mint fee is {fee} XCH."
