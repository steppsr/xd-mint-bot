"""Errors whose messages are safe to show in Discord."""


class MintBotError(Exception):
    """Base class for failures the bot can explain to the operator or the user."""


class ConfigError(MintBotError):
    """The environment is missing a value or a value is unusable."""


class ValidationError(MintBotError):
    """User input or the source message cannot be minted as-is."""


class UploadError(MintBotError):
    """Filebase rejected an upload or did not return a CID."""


class ChiaMintError(MintBotError):
    """The wallet RPC could not be reached or refused the request."""


class DexieError(MintBotError):
    """Dexie rejected an offer or could not be reached."""
