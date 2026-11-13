"""Mint a Chia NFT through the wallet RPC.

The wallet listens on HTTPS and requires the client certificate in the Chia
config, even on localhost. ``CHIA_RPC_MODE=subprocess`` shells out to
``chia rpc wallet`` when the CLI should load those certificates itself.

A mint is submitted once. A timed-out request may already be in the mempool,
so this client does not send ``nft_mint_nft`` again.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

import requests
import urllib3
from requests import exceptions as request_errors

from config import Settings
from utils.errors import ChiaMintError
from utils.format import format_xch

log = logging.getLogger(__name__)


def friendly_chia_error(message: str) -> str:
    """Turn a wallet error into a short Discord-safe sentence."""
    text = " ".join(message.split())
    lower = text.lower()
    if any(phrase in lower for phrase in ("insufficient", "not enough", "can't spend", "cannot spend")):
        return "The Chia wallet does not have enough spendable XCH to cover the mint fee."
    if "not synced" in lower or "syncing" in lower or "not fully synced" in lower:
        return "The Chia wallet is not synced yet. Wait until it is synced and start the mint again."
    if any(phrase in lower for phrase in ("connection refused", "failed to establish", "certificate", "ssl")):
        return (
            "Could not reach the Chia wallet RPC. Check that the wallet is running "
            "and that the certificate paths are readable."
        )
    if "timed out" in lower or "timeout" in lower:
        return (
            "The Chia wallet RPC timed out. Check the wallet before starting again "
            "so the same image is not minted twice."
        )
    if not text:
        text = "The wallet returned an empty error."
    return f"Chia wallet RPC rejected the mint: {text[:400]}"


def build_mint_payload(
    *,
    wallet_id: int,
    uris: list[str],
    image_hash: str,
    meta_uris: list[str],
    meta_hash: str,
    license_uris: list[str] | None,
    license_hash: str | None,
    royalty_address: str,
    royalty_percentage: int,
    target_address: str,
    did_id: str,
    fee: int,
) -> dict[str, Any]:
    """Build the ``nft_mint_nft`` request body for a 1-of-1 NFT."""
    payload: dict[str, Any] = {
        "wallet_id": wallet_id,
        "uris": uris,
        "hash": image_hash,
        "meta_uris": meta_uris,
        "meta_hash": meta_hash,
        "royalty_address": royalty_address,
        "royalty_percentage": royalty_percentage,
        "target_address": target_address,
        "did_id": did_id,
        "edition_number": 1,
        "edition_total": 1,
        "fee": fee,
    }
    if license_uris and license_hash:
        payload["license_uris"] = license_uris
        payload["license_hash"] = license_hash
    return payload


def extract_nft_id(data: Mapping[str, Any]) -> str | None:
    """Read ``nft_id`` from a successful mint response."""
    nft_id = data.get("nft_id")
    if isinstance(nft_id, str) and nft_id.strip():
        return nft_id.strip()
    return None


def extract_launcher_id(data: Mapping[str, Any]) -> str | None:
    """Read a launcher id when the RPC includes one beside ``nft_id``."""
    launcher = data.get("launcher_id")
    if isinstance(launcher, str) and launcher.strip():
        return launcher.strip()
    return None


def parse_cli_json(stdout: str) -> dict[str, Any]:
    """Parse the JSON object printed by ``chia rpc``."""
    text = stdout.strip()
    if not text:
        raise ChiaMintError("The chia CLI returned no output.")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise ChiaMintError("The chia CLI did not return JSON.")
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            raise ChiaMintError("The chia CLI did not return JSON.") from exc
    if not isinstance(value, dict):
        raise ChiaMintError("The chia CLI returned an unexpected payload.")
    return value


def _sale_offer_message(exc: ChiaMintError) -> str:
    text = str(exc).replace("rejected the mint", "rejected the sale offer", 1)
    if "timed out" in text.lower():
        return (
            "The sale offer request timed out. Check the wallet offers before running /sell, "
            "so the same NFT is not listed twice."
        )
    return text


def _ensure_success(data: Mapping[str, Any]) -> None:
    """Accept a wallet response that reports success or includes an NFT id."""
    if data.get("success") is True:
        return
    if data.get("success") is not False and data.get("nft_id"):
        return
    error = data.get("error") or data.get("message") or "The wallet did not report success."
    raise ChiaMintError(friendly_chia_error(str(error)))


class ChiaMinter:
    """HTTPS or CLI client for the wallet RPC methods this bot uses."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._verified_warning = False

    def ensure_transport(self) -> None:
        """Fail early when the selected RPC transport is not available."""
        if self.settings.chia_rpc_mode == "subprocess":
            executable = self.settings.chia_executable
            if Path(executable).is_file() or shutil.which(executable):
                return
            raise ChiaMintError(
                f"The chia executable was not found ({executable}). Set CHIA_EXECUTABLE to the full path."
            )
        cert = self.settings.chia_ssl_cert
        key = self.settings.chia_ssl_key
        if not cert.is_file() or not key.is_file():
            raise ChiaMintError(
                "Chia wallet certificates were not found. "
                "Set CHIA_SSL_CERT and CHIA_SSL_KEY. "
                "CHIA_RPC_MODE=subprocess uses the chia CLI instead."
            )
        if not os.access(cert, os.R_OK) or not os.access(key, os.R_OK):
            raise ChiaMintError(
                "The bot cannot read the Chia wallet certificate or key. "
                "Run the bot as the chia user, or grant that user read access."
            )

    def assert_ready_to_mint(self, *, extra_fee_mojos: int = 0) -> None:
        """Require a synced wallet and a spendable balance that covers the fees."""
        self.ensure_transport()
        status = self.rpc("get_sync_status", {})
        if not status.get("synced"):
            raise ChiaMintError("The Chia wallet is not synced yet. Wait until it is synced and start the mint again.")
        wallets = self._wallet_ids(self.rpc("get_wallets", {}))
        if self.settings.chia_nft_wallet_id not in wallets:
            raise ChiaMintError(
                f"NFT wallet id {self.settings.chia_nft_wallet_id} was not found. Check CHIA_NFT_WALLET_ID."
            )
        if self.settings.chia_fee_wallet_id not in wallets:
            raise ChiaMintError(
                f"Fee wallet id {self.settings.chia_fee_wallet_id} was not found. Check CHIA_FEE_WALLET_ID."
            )
        self.rpc("nft_count_nfts", {"wallet_id": self.settings.chia_nft_wallet_id})
        needed = self.settings.chia_fee_mojos + max(0, extra_fee_mojos)
        balance = self.spendable_mojos(self.settings.chia_fee_wallet_id)
        if balance < needed:
            raise ChiaMintError(
                f"Spendable balance is {format_xch(balance)} XCH, "
                f"which is below the required {format_xch(needed)} XCH."
            )

    def create_sale_offer(self, *, launcher_hex: str, price_mojos: int) -> str:
        """Create an offer that sells one NFT for ``price_mojos``.

        The positive amount is requested XCH, keyed by the standard wallet id.
        The negative amount offers the NFT launcher. This is submitted once.
        """
        launcher = launcher_hex.lower().removeprefix("0x")
        payload = {
            "offer": {
                str(self.settings.chia_fee_wallet_id): price_mojos,
                launcher: -1,
            },
            "fee": self.settings.offer_fee_mojos,
            "driver_dict": {},
            "validate_only": False,
        }
        log.info(
            "Creating sale offer launcher=%s price_mojos=%s fee=%s",
            launcher,
            price_mojos,
            self.settings.offer_fee_mojos,
        )
        try:
            data = self.rpc("create_offer_for_ids", payload, retry_connect=True)
        except ChiaMintError as exc:
            raise ChiaMintError(_sale_offer_message(exc)) from exc
        if data.get("success") is False:
            try:
                _ensure_success(data)
            except ChiaMintError as exc:
                raise ChiaMintError(_sale_offer_message(exc)) from exc
        offer = data.get("offer")
        if isinstance(offer, str) and offer.startswith("offer"):
            return offer
        try:
            _ensure_success(data)
        except ChiaMintError as exc:
            raise ChiaMintError(_sale_offer_message(exc)) from exc
        raise ChiaMintError("The wallet did not return an offer file.")

    def nft_get_info(self, nft_id: str) -> dict[str, Any] | None:
        """Return ``nft_info`` for an nft1 id, or None when this wallet does not have it yet.

        ``coin_id`` accepts the bech32m nft id. Connection failures and timeouts
        still raise. A missing NFT is None so the caller can wait.
        """
        try:
            data = self.rpc(
                "nft_get_info",
                {
                    "coin_id": nft_id,
                    "wallet_id": self.settings.chia_nft_wallet_id,
                },
            )
        except ChiaMintError as exc:
            if _nft_info_missing(str(exc)):
                return None
            raise
        info = data.get("nft_info")
        if isinstance(info, dict):
            return info
        return None

    def find_open_offer_launcher(self, launcher_hex: str) -> bool:
        """True when an open wallet offer already includes this NFT launcher.

        A failed lookup returns False so a listing can still be attempted.
        Taken and completed offers are excluded by the RPC request.
        """
        launcher = launcher_hex.lower().removeprefix("0x")
        if not launcher:
            return False
        try:
            data = self.rpc(
                "get_all_offers",
                {
                    "start": 0,
                    "end": 50,
                    "exclude_my_offers": False,
                    "exclude_taken_offers": True,
                    "include_completed": False,
                    "file_contents": False,
                },
                retry_connect=True,
            )
        except ChiaMintError as exc:
            log.warning("Could not list wallet offers before creating one: %s", exc)
            return False
        records = data.get("offers")
        if not isinstance(records, list):
            records = data.get("trade_records")
        if not isinstance(records, list):
            return False
        for record in records:
            if _record_offers_launcher(record, launcher):
                return True
        return False

    def spendable_mojos(self, wallet_id: int) -> int:
        data = self.rpc("get_wallet_balance", {"wallet_id": wallet_id})
        wallet = data.get("wallet_balance")
        if not isinstance(wallet, dict) or "spendable_balance" not in wallet:
            raise ChiaMintError("The wallet balance response did not include spendable_balance.")
        return int(wallet["spendable_balance"])

    def mint_nft(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit ``nft_mint_nft`` once and return the parsed response."""
        log.info(
            "Submitting nft_mint_nft wallet=%s fee=%s hash=%s",
            payload.get("wallet_id"),
            payload.get("fee"),
            payload.get("hash"),
        )
        log.debug("nft_mint_nft payload: %s", payload)
        data = self.rpc("nft_mint_nft", payload, retry_connect=True)
        _ensure_success(data)
        log.info(
            "Chia mint response fields: %s nft_id=%s",
            ", ".join(sorted(data)),
            data.get("nft_id") or "",
        )
        return data

    def rpc(self, endpoint: str, payload: dict[str, Any], *, retry_connect: bool = False) -> dict[str, Any]:
        """Call one wallet RPC method."""
        attempts = 3 if retry_connect else 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if self.settings.chia_rpc_mode == "subprocess":
                    return self._rpc_subprocess(endpoint, payload)
                return self._rpc_https(endpoint, payload)
            except ChiaMintError:
                raise
            except request_errors.Timeout as exc:
                raise ChiaMintError(friendly_chia_error("timed out")) from exc
            except request_errors.RequestException as exc:
                last_error = exc
                if attempt == attempts or not _connect_failure(exc):
                    raise ChiaMintError(friendly_chia_error(str(exc))) from exc
                log.warning("Chia RPC connection failed (%s/%s): %s", attempt, attempts, exc.__class__.__name__)
                time.sleep(min(2 * attempt, 5))
        raise ChiaMintError(friendly_chia_error(str(last_error or "connection refused")))

    def _rpc_https(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_transport()
        url = f"https://{self.settings.chia_rpc_host}:{self.settings.chia_wallet_rpc_port}/{endpoint.lstrip('/')}"
        verify: bool | str
        if self.settings.chia_ssl_verify and self.settings.chia_ssl_ca is not None:
            if not self.settings.chia_ssl_ca.is_file():
                raise ChiaMintError(f"CHIA_SSL_CA was not found: {self.settings.chia_ssl_ca}")
            verify = str(self.settings.chia_ssl_ca)
        else:
            verify = False
            if not self._verified_warning:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                self._verified_warning = True
        try:
            response = requests.post(
                url,
                json=payload,
                cert=(str(self.settings.chia_ssl_cert), str(self.settings.chia_ssl_key)),
                verify=verify,
                timeout=(10, self.settings.chia_rpc_timeout),
                headers={"Content-Type": "application/json"},
            )
        except request_errors.Timeout:
            raise
        except request_errors.RequestException:
            raise
        except OSError as exc:
            raise ChiaMintError(friendly_chia_error(str(exc))) from exc
        return self._parse_http(response)

    def _rpc_subprocess(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_transport()
        command = [
            self.settings.chia_executable,
            "rpc",
            "wallet",
            endpoint.lstrip("/"),
            json.dumps(payload, separators=(",", ":")),
        ]
        env = os.environ.copy()
        if self.settings.chia_root is not None:
            env["CHIA_ROOT"] = str(self.settings.chia_root)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.chia_rpc_timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ChiaMintError(friendly_chia_error("timed out")) from exc
        except FileNotFoundError as exc:
            raise ChiaMintError(
                f"The chia executable was not found ({self.settings.chia_executable})."
            ) from exc
        except OSError as exc:
            raise ChiaMintError(friendly_chia_error(str(exc))) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "chia rpc failed").strip()
            log.warning("chia rpc %s exited %s: %s", endpoint, completed.returncode, detail[:2000])
            raise ChiaMintError(friendly_chia_error(detail))
        try:
            data = parse_cli_json(completed.stdout)
        except ChiaMintError:
            log.warning("Unparsed chia stdout: %s", (completed.stdout or "")[:2000])
            raise
        _ensure_success(data)
        return data

    @staticmethod
    def _parse_http(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise ChiaMintError(
                f"The wallet RPC returned HTTP {response.status_code} with a body that was not JSON."
            ) from exc
        if not isinstance(data, dict):
            raise ChiaMintError("The wallet RPC returned an unexpected payload.")
        if response.status_code >= 400 or data.get("success") is False:
            error = data.get("error") or data.get("message") or response.text[:400]
            raise ChiaMintError(friendly_chia_error(str(error)))
        return data

    @staticmethod
    def _wallet_ids(data: Mapping[str, Any]) -> set[int]:
        wallets = data.get("wallets")
        if not isinstance(wallets, list):
            raise ChiaMintError("The wallet list response did not include wallets.")
        ids: set[int] = set()
        for wallet in wallets:
            if isinstance(wallet, dict) and "id" in wallet:
                ids.add(int(wallet["id"]))
        return ids


def _nft_info_missing(message: str) -> bool:
    """True when ``nft_get_info`` is saying this wallet does not have the NFT yet."""
    lower = message.lower()
    if any(phrase in lower for phrase in ("timed out", "timeout", "insufficient", "not enough")):
        return False
    return any(
        phrase in lower
        for phrase in (
            "not found",
            "does not exist",
            "unknown nft",
            "no nft",
            "not available",
        )
    )


def _record_offers_launcher(record: object, launcher: str) -> bool:
    """True when an offer summary mentions this launcher id."""
    if not isinstance(record, dict):
        return False
    summary = record.get("summary")
    if not isinstance(summary, dict):
        return False
    return launcher in json.dumps(summary, default=str).lower()


def _connect_failure(exc: BaseException) -> bool:
    """True when the TCP connection failed before a mint could be accepted."""
    if isinstance(exc, request_errors.ConnectTimeout):
        return True
    if isinstance(exc, request_errors.ConnectionError):
        text = str(exc).lower()
        return any(
            phrase in text
            for phrase in (
                "connection refused",
                "failed to establish",
                "name or service not known",
                "nodename nor servname",
                "network is unreachable",
            )
        )
    return False
