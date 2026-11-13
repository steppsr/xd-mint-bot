"""Load and validate environment settings for xd-mint-bot.

Chia royalty values are hundredths of a percent: 300 means 3%. The modal asks
for a human percent and converts it before the wallet RPC call.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from utils.errors import ConfigError
from utils.format import mask_secret

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_BECH32_BODY = r"[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{50,80}"
_ADDRESS = re.compile(rf"^(?:xch|txch)1{_BECH32_BODY}$")
_DID = re.compile(rf"^did:chia:1{_BECH32_BODY}$")
_PLACEHOLDERS = {
    "your_token_here",
    "changeme",
    "your_access_key",
    "your_secret_key",
}

# A mint fee above this is refused unless ALLOW_HIGH_FEE is set.
# 0.01 XCH is far above a normal NFT mint fee.
HIGH_FEE_MOJOS = 10_000_000_000


def _raw(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    if stripped == "" and default is not None:
        return default
    return stripped


def _require(name: str) -> str:
    value = _raw(name)
    if not value:
        raise ConfigError(f"{name} is required.")
    if value in _PLACEHOLDERS:
        raise ConfigError(f"{name} is still a placeholder. Set a real value.")
    return value


def _bool(name: str, default: bool = False) -> bool:
    value = _raw(name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false.")


def _int(name: str, default: int | None = None, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = _raw(name)
    if raw is None:
        if default is None:
            raise ConfigError(f"{name} is required.")
        value = default
    else:
        try:
            value = int(raw, 10)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a whole number.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be at most {maximum}.")
    return value


def _int_set(name: str, *, required: bool = False) -> frozenset[int]:
    raw = _raw(name, "")
    assert raw is not None
    if raw == "":
        if required:
            raise ConfigError(f"{name} must list at least one Discord id.")
        return frozenset()
    values: list[int] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        if not piece.isdigit():
            raise ConfigError(f"{name} contains a non-numeric id: {piece!r}.")
        values.append(int(piece))
    if required and not values:
        raise ConfigError(f"{name} must list at least one Discord id.")
    return frozenset(values)


def _https_url(name: str) -> str:
    value = _require(name).rstrip("/")
    if not value.startswith("https://"):
        raise ConfigError(f"{name} must be an https URL.")
    return value


def _expand(path: Path) -> Path:
    try:
        return path.expanduser()
    except RuntimeError as exc:
        raise ConfigError("A configured path uses ~ and the home directory is not set.") from exc


def _chia_config_root(network: str, chia_root: Path | None) -> Path:
    """Return the Chia configuration root used to find the wallet certificates."""
    if chia_root is not None:
        return chia_root
    folder = "mainnet" if network == "mainnet" else "testnet11"
    try:
        home = Path.home()
    except RuntimeError as exc:
        raise ConfigError(
            "Could not find a home directory for the Chia certificate paths. "
            "Set CHIA_ROOT, or set CHIA_SSL_CERT and CHIA_SSL_KEY to absolute paths."
        ) from exc
    return home / ".chia" / folder


def _path(name: str, default: Path) -> Path:
    raw = _raw(name)
    if not raw:
        return default
    return _expand(Path(raw))


def _uuid(name: str) -> str:
    raw = _require(name)
    try:
        parsed = uuid.UUID(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a UUID.") from exc
    return str(parsed)


def _chia_address(value: str, *, network: str, field: str, allow_did: bool = False) -> str:
    text = value.strip().lower()
    if allow_did and text.startswith("did:"):
        if not _DID.fullmatch(text):
            raise ConfigError(f"{field} is not a did:chia id.")
        return text
    prefix = "xch1" if network == "mainnet" else "txch1"
    if not text.startswith(prefix):
        raise ConfigError(f"{field} must start with {prefix} on {network}.")
    if not _ADDRESS.fullmatch(text):
        raise ConfigError(f"{field} is not a valid {prefix} address.")
    return text


def _did(value: str, field: str) -> str:
    text = value.strip().lower()
    if not _DID.fullmatch(text):
        raise ConfigError(f"{field} must look like did:chia:1… using the bech32 character set.")
    return text


@dataclass(frozen=True)
class Settings:
    """Validated process settings. Secrets are omitted from ``repr``."""

    discord_bot_token: str
    authorized_user_ids: frozenset[int]
    seedling_emoji: str
    discord_guild_id: int | None
    allowed_guild_ids: frozenset[int]

    chia_network: str
    chia_rpc_host: str
    chia_wallet_rpc_port: int
    chia_rpc_mode: str
    chia_rpc_timeout: int
    chia_executable: str
    chia_root: Path | None
    chia_ssl_cert: Path
    chia_ssl_key: Path
    chia_ssl_ca: Path | None
    chia_ssl_verify: bool
    chia_nft_wallet_id: int
    chia_fee_wallet_id: int
    chia_did_id: str
    chia_royalty_address: str
    chia_target_address: str
    chia_fee_mojos: int
    allow_high_fee: bool

    filebase_access_key: str
    filebase_secret_key: str
    filebase_bucket: str
    filebase_endpoint: str
    filebase_region: str
    filebase_gateway: str

    default_collection_id: str
    default_collection_name: str
    default_royalty_percentage: int
    minting_tool: str
    upload_license: bool
    license_file: Path | None
    include_gateway_uris: bool
    dry_run: bool
    require_midjourney: bool
    midjourney_bot_ids: frozenset[int]

    mintgarden_nft_url: str
    spacescan_nft_url: str
    dexie_api_url: str
    dexie_offer_url: str
    offer_fee_mojos: int
    offer_wait_seconds: int
    collections_file: Path
    offers_dir: Path

    log_level: str
    log_file: Path
    temp_dir: Path
    max_image_bytes: int
    session_timeout_seconds: int
    reaction_cooldown_seconds: int
    keep_temp_on_error: bool

    def __repr__(self) -> str:
        return (
            f"Settings(network={self.chia_network}, wallet={self.chia_nft_wallet_id}, "
            f"dry_run={self.dry_run}, rpc={self.chia_rpc_mode})"
        )

    @classmethod
    def load(cls, *, dotenv: bool = True) -> Settings:
        """Read ``.env`` from the project root, then validate the environment."""
        if dotenv:
            load_dotenv(ROOT / ".env")

        network = (_raw("CHIA_NETWORK", "mainnet") or "mainnet").lower()
        if network not in {"mainnet", "testnet"}:
            raise ConfigError("CHIA_NETWORK must be mainnet or testnet.")

        rpc_mode = (_raw("CHIA_RPC_MODE", "https") or "https").lower()
        if rpc_mode not in {"https", "subprocess"}:
            raise ConfigError("CHIA_RPC_MODE must be https or subprocess.")

        chia_root_raw = _raw("CHIA_ROOT")
        chia_root = _expand(Path(chia_root_raw)) if chia_root_raw else None
        # Explicit certificate paths do not need a home directory. The default
        # still points at ~/.chia so a normal install works with an empty override.
        if chia_root is None and _raw("CHIA_SSL_CERT") and _raw("CHIA_SSL_KEY"):
            ssl_base = _expand(Path(_require("CHIA_SSL_CERT"))).parent.parent
        else:
            ssl_base = _chia_config_root(network, chia_root) / "config" / "ssl"

        royalty = _int("DEFAULT_ROYALTY_PERCENTAGE", minimum=0, maximum=10_000)
        fee = _int("CHIA_FEE_MOJOS", minimum=0)
        allow_high_fee = _bool("ALLOW_HIGH_FEE", False)
        if fee > HIGH_FEE_MOJOS and not allow_high_fee:
            raise ConfigError(
                "CHIA_FEE_MOJOS is above 0.01 XCH. Set ALLOW_HIGH_FEE=true to use that fee."
            )

        collection_name = _require("DEFAULT_COLLECTION_NAME")
        if not 1 <= len(collection_name) <= 100:
            raise ConfigError("DEFAULT_COLLECTION_NAME must be 1 to 100 characters.")

        minting_tool = _raw("MINTING_TOOL", "xd-mint-bot Discord Bot") or "xd-mint-bot Discord Bot"
        if len(minting_tool) > 200:
            raise ConfigError("MINTING_TOOL must be 200 characters or fewer.")

        log_level = (_raw("LOG_LEVEL", "INFO") or "INFO").upper()
        if log_level not in _LOG_LEVELS:
            raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")

        license_raw = _raw("LICENSE_FILE")
        license_file = _expand(Path(license_raw)) if license_raw else None
        if license_file is not None and not license_file.is_file():
            raise ConfigError(f"LICENSE_FILE does not exist: {license_file}")

        guild_raw = _raw("DISCORD_GUILD_ID")
        if guild_raw:
            if not guild_raw.isdigit():
                raise ConfigError("DISCORD_GUILD_ID must be a numeric Discord guild id.")
            guild_id: int | None = int(guild_raw)
        else:
            guild_id = None

        emoji = _raw("SEEDLING_EMOJI", "🌱") or "🌱"
        if len(emoji) > 64:
            raise ConfigError("SEEDLING_EMOJI is too long.")

        if network == "mainnet":
            default_mintgarden = "https://mintgarden.io/nfts/{nft_id}"
            default_spacescan = "https://www.spacescan.io/nft/{nft_id}"
        else:
            default_mintgarden = "https://testnet11.mintgarden.io/nfts/{nft_id}"
            default_spacescan = "https://testnet11.spacescan.io/nft/{nft_id}"
        mintgarden = _raw("MINTGARDEN_NFT_URL", default_mintgarden) or default_mintgarden
        spacescan = _raw("SPACESCAN_NFT_URL", default_spacescan) or default_spacescan
        for label, template in (("MINTGARDEN_NFT_URL", mintgarden), ("SPACESCAN_NFT_URL", spacescan)):
            if "{nft_id}" not in template:
                raise ConfigError(f"{label} must contain {{nft_id}}.")

        if network == "mainnet":
            default_dexie_api = "https://api.dexie.space/v1"
            default_dexie_offer = "https://dexie.space/offers/{offer_id}"
        else:
            default_dexie_api = "https://api-testnet.dexie.space/v1"
            default_dexie_offer = "https://testnet.dexie.space/offers/{offer_id}"
        dexie_api = (_raw("DEXIE_API_URL", default_dexie_api) or default_dexie_api).rstrip("/")
        dexie_offer = _raw("DEXIE_OFFER_URL", default_dexie_offer) or default_dexie_offer
        if not dexie_api.startswith("https://"):
            raise ConfigError("DEXIE_API_URL must be an https URL.")
        if "{offer_id}" not in dexie_offer:
            raise ConfigError("DEXIE_OFFER_URL must contain {offer_id}.")

        # A creator fee locks an XCH coin inside the offer until it is taken or cancelled.
        # The taker can add a fee when accepting, so the default here is zero.
        offer_fee = _int("OFFER_FEE_MOJOS", 0, minimum=0)
        if offer_fee > HIGH_FEE_MOJOS and not allow_high_fee:
            raise ConfigError(
                "OFFER_FEE_MOJOS is above 0.01 XCH. Set ALLOW_HIGH_FEE=true to use that fee."
            )

        verify_ca = _bool("CHIA_SSL_VERIFY", False)
        ca_path = _path("CHIA_SSL_CA", ssl_base / "ca" / "chia_ca.crt")

        log_file = _path("LOG_FILE", ROOT / "logs" / "xd-mint-bot.log")
        if not log_file.is_absolute():
            log_file = ROOT / log_file
        temp_dir = _path("TEMP_DIR", ROOT / "temp")
        if not temp_dir.is_absolute():
            temp_dir = ROOT / temp_dir
        collections_file = _path("COLLECTIONS_FILE", ROOT / "data" / "collections.json")
        if not collections_file.is_absolute():
            collections_file = ROOT / collections_file
        offers_dir = _path("OFFERS_DIR", ROOT / "data" / "offers")
        if not offers_dir.is_absolute():
            offers_dir = ROOT / offers_dir

        return cls(
            discord_bot_token=_require("DISCORD_BOT_TOKEN"),
            authorized_user_ids=_int_set("AUTHORIZED_USER_IDS", required=True),
            seedling_emoji=emoji,
            discord_guild_id=guild_id,
            allowed_guild_ids=_int_set("ALLOWED_GUILD_IDS"),
            chia_network=network,
            chia_rpc_host=_raw("CHIA_RPC_HOST", "127.0.0.1") or "127.0.0.1",
            chia_wallet_rpc_port=_int("CHIA_WALLET_RPC_PORT", 9256, minimum=1, maximum=65535),
            chia_rpc_mode=rpc_mode,
            chia_rpc_timeout=_int("CHIA_RPC_TIMEOUT", 180, minimum=10, maximum=600),
            chia_executable=_raw("CHIA_EXECUTABLE", "chia") or "chia",
            chia_root=chia_root,
            chia_ssl_cert=_path("CHIA_SSL_CERT", ssl_base / "wallet" / "private_wallet.crt"),
            chia_ssl_key=_path("CHIA_SSL_KEY", ssl_base / "wallet" / "private_wallet.key"),
            chia_ssl_ca=ca_path if verify_ca else None,
            chia_ssl_verify=verify_ca,
            chia_nft_wallet_id=_int("CHIA_NFT_WALLET_ID", minimum=1),
            chia_fee_wallet_id=_int("CHIA_FEE_WALLET_ID", 1, minimum=1),
            chia_did_id=_did(_require("CHIA_DID_ID"), "CHIA_DID_ID"),
            chia_royalty_address=_chia_address(
                _require("CHIA_ROYALTY_ADDRESS"),
                network=network,
                field="CHIA_ROYALTY_ADDRESS",
                allow_did=True,
            ),
            chia_target_address=_chia_address(
                _require("CHIA_TARGET_ADDRESS"),
                network=network,
                field="CHIA_TARGET_ADDRESS",
            ),
            chia_fee_mojos=fee,
            allow_high_fee=allow_high_fee,
            filebase_access_key=_require("FILEBASE_ACCESS_KEY"),
            filebase_secret_key=_require("FILEBASE_SECRET_KEY"),
            filebase_bucket=_require("FILEBASE_BUCKET"),
            filebase_endpoint=_https_url("FILEBASE_ENDPOINT"),
            filebase_region=_raw("FILEBASE_REGION", "us-east-1") or "us-east-1",
            filebase_gateway=_https_url("FILEBASE_GATEWAY"),
            default_collection_id=_uuid("DEFAULT_COLLECTION_ID"),
            default_collection_name=collection_name,
            default_royalty_percentage=royalty,
            minting_tool=minting_tool,
            upload_license=_bool("UPLOAD_LICENSE", True),
            license_file=license_file,
            include_gateway_uris=_bool("INCLUDE_GATEWAY_URIS", True),
            dry_run=_bool("DRY_RUN", False),
            require_midjourney=_bool("REQUIRE_MIDJOURNEY", False),
            midjourney_bot_ids=_int_set("MIDJOURNEY_BOT_IDS"),
            mintgarden_nft_url=mintgarden,
            spacescan_nft_url=spacescan,
            dexie_api_url=dexie_api,
            dexie_offer_url=dexie_offer,
            offer_fee_mojos=offer_fee,
            offer_wait_seconds=_int("OFFER_WAIT_SECONDS", 600, minimum=0, maximum=900),
            collections_file=collections_file,
            offers_dir=offers_dir,
            log_level=log_level,
            log_file=log_file,
            temp_dir=temp_dir,
            max_image_bytes=_int("MAX_IMAGE_BYTES", 26_214_400, minimum=1, maximum=104_857_600),
            session_timeout_seconds=_int("SESSION_TIMEOUT_SECONDS", 900, minimum=30, maximum=3600),
            reaction_cooldown_seconds=_int("REACTION_COOLDOWN_SECONDS", 15, minimum=0, maximum=3600),
            keep_temp_on_error=_bool("KEEP_TEMP_ON_ERROR", False),
        )


def log_startup(settings: Settings) -> None:
    """Write a secret-free summary once logging is configured."""
    log = logging.getLogger("xd_mint_bot")
    log.info("xd-mint-bot %s", VERSION)
    log.info(
        "network=%s rpc=%s host=%s port=%s nft_wallet=%s fee_wallet=%s fee_mojos=%s dry_run=%s",
        settings.chia_network,
        settings.chia_rpc_mode,
        settings.chia_rpc_host,
        settings.chia_wallet_rpc_port,
        settings.chia_nft_wallet_id,
        settings.chia_fee_wallet_id,
        settings.chia_fee_mojos,
        settings.dry_run,
    )
    log.info(
        "authorized_users=%d collection=%s royalty_hundredths=%s target=%s",
        len(settings.authorized_user_ids),
        settings.default_collection_name,
        settings.default_royalty_percentage,
        mask_secret(settings.chia_target_address),
    )
    if settings.dry_run:
        log.info("DRY_RUN is enabled. The bot uploads to IPFS and skips the wallet mint.")
    if settings.chia_fee_mojos == 0:
        log.warning("CHIA_FEE_MOJOS is 0. Mainnet mints usually need a fee to be included.")
    if 0 < settings.default_royalty_percentage < 100:
        log.warning(
            "DEFAULT_ROYALTY_PERCENTAGE is %s, which is %s%%. A 3%% royalty is 300.",
            settings.default_royalty_percentage,
            settings.default_royalty_percentage / 100,
        )
    if settings.chia_rpc_mode == "https":
        if settings.chia_rpc_host not in {"127.0.0.1", "localhost", "::1"}:
            log.warning(
                "CHIA_RPC_HOST is %s. Keep the wallet RPC on a private network.",
                settings.chia_rpc_host,
            )
        if not settings.chia_ssl_verify:
            log.warning("CHIA_SSL_VERIFY is off. Client certificates still authenticate the bot.")
        if not settings.chia_ssl_cert.is_file() or not settings.chia_ssl_key.is_file():
            log.warning(
                "Chia wallet certificate files were not found at %s and %s.",
                settings.chia_ssl_cert,
                settings.chia_ssl_key,
            )
    if settings.chia_rpc_mode == "subprocess":
        log.info("Subprocess mode uses the chia CLI and CHIA_ROOT. Host and port apply to https mode.")
