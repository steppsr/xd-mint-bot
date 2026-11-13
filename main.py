"""xd-mint-bot entrypoint.

Run ``python main.py`` to connect to Discord. Run ``python main.py --check``
on the Chia machine to verify the wallet RPC and the Filebase bucket first.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import VERSION, Settings, log_startup
from handlers.collection_commands import collection as collection_commands
from handlers.collection_commands import sell
from handlers.metadata_flow import expire_session, notify_queue_dropped, run_confirmed_mint
from handlers.reaction_handler import ReactionCog
from handlers.sessions import SessionStore, remove_temp_file, sweep_old_temp_files
from services.chia_minter import ChiaMinter
from services.collection_store import CollectionStore
from services.dexie_client import DexieClient
from services.filebase_uploader import FilebaseUploader
from services.health import build_health_report
from services.mint_service import MintService
from utils.errors import ConfigError
from utils.format import truncate
from utils.logger import install_redaction, setup_logging

log = logging.getLogger("xd_mint_bot")


class SeedlingMinterBot(commands.Bot):
    """Discord client that owns the mint sessions and service clients."""

    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            chunk_guilds_at_startup=False,
        )
        self.settings = settings
        self.sessions = SessionStore()
        self.collections = CollectionStore(settings.collections_file, settings)
        self.uploader = FilebaseUploader(settings)
        self.minter = ChiaMinter(settings)
        self.dexie = DexieClient(settings)
        self.mints = MintService(settings, self.uploader, self.minter, self.collections)
        self.http_session: aiohttp.ClientSession | None = None
        self.mint_jobs: asyncio.Queue[tuple[str, discord.Message, bool]] = asyncio.Queue()
        self.offer_tasks: set[asyncio.Task[None]] = set()
        self._sweeper: asyncio.Task[None] | None = None
        self._mint_worker: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60),
            headers={"User-Agent": "xd-mint-bot"},
        )
        await self.add_cog(ReactionCog(self))
        self.tree.add_command(nftstatus)
        self.tree.add_command(collection_commands)
        self.tree.add_command(sell)
        try:
            if self.settings.discord_guild_id:
                guild = discord.Object(id=self.settings.discord_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            log.info("Synced %s application command(s).", len(synced))
        except discord.HTTPException:
            log.exception("Could not sync application commands. /nftstatus may be unavailable.")
        self._sweeper = asyncio.create_task(self._sweep_sessions())
        self._mint_worker = asyncio.create_task(self._run_mint_queue())

    async def on_ready(self) -> None:
        user = self.user
        log.info("Logged in as %s (%s) across %s guild(s).", user, getattr(user, "id", "?"), len(self.guilds))
        allowed = self.settings.allowed_guild_ids
        for guild in self.guilds:
            if allowed and guild.id not in allowed:
                log.warning("Connected to guild %s (%s), which is outside ALLOWED_GUILD_IDS.", guild.name, guild.id)

    async def close(self) -> None:
        await self._cancel_task(self._sweeper)
        self._sweeper = None
        # Stop the worker before dropping sessions so a temp image is not deleted mid-mint.
        await self._cancel_task(self._mint_worker)
        self._mint_worker = None
        offer_tasks = list(self.offer_tasks)
        for task in offer_tasks:
            task.cancel()
        if offer_tasks:
            await asyncio.gather(*offer_tasks, return_exceptions=True)
        for session in self.sessions.drain():
            view = session.active_view
            if view is not None and hasattr(view, "stop"):
                view.stop()
            if session.state in {"queued", "uploading", "minting"}:
                try:
                    await notify_queue_dropped(self, session)
                except Exception:
                    log.warning("Could not update the queued mint message for %s", session.key, exc_info=True)
            remove_temp_file(session.image_path)
        if self.http_session is not None and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    async def _run_mint_queue(self) -> None:
        """Mint queued confirmations one at a time. Offer waits run on their own tasks."""
        try:
            while True:
                session_key, message, dry_run = await self.mint_jobs.get()
                try:
                    session = self.sessions.get(session_key)
                    if session is None:
                        continue
                    await run_confirmed_mint(self, session, message, dry_run=dry_run)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Queued mint failed for %s", session_key)
                finally:
                    self.mint_jobs.task_done()
        except asyncio.CancelledError:
            raise

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _sweep_sessions(self) -> None:
        try:
            while not self.is_closed():
                await asyncio.sleep(30)
                for session in self.sessions.expired(self.settings.session_timeout_seconds):
                    await expire_session(
                        self,
                        session,
                        view=session.active_view,
                        reason="The mint session timed out. React with the seedling again to start over.",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Session sweeper stopped.")


@app_commands.command(name="nftstatus", description="Check Chia wallet sync, the fee balance, and Filebase.")
@app_commands.guild_only()
async def nftstatus(interaction: discord.Interaction) -> None:
    """Report whether this process can upload and mint."""
    bot = interaction.client
    if not isinstance(bot, SeedlingMinterBot):
        await interaction.response.send_message("This command is not available.", ephemeral=True)
        return
    settings = bot.settings
    if interaction.guild_id and settings.allowed_guild_ids and interaction.guild_id not in settings.allowed_guild_ids:
        await interaction.response.send_message("This server is not allowed to use the minter.", ephemeral=True)
        return
    if interaction.user.id not in settings.authorized_user_ids:
        await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    report = await asyncio.to_thread(build_health_report, settings, bot.minter, bot.uploader)
    embed = discord.Embed(
        title="xd-mint-bot status",
        color=0x3BA55D if report.ok else 0xED4245,
        description=(
            f"Version {VERSION} • {settings.chia_network} • "
            f"dry run {'on' if settings.dry_run else 'off'}"
        ),
    )
    for check in report.checks:
        label = "OK" if check.ok else "Fail"
        embed.add_field(name=f"{label} · {check.name}", value=truncate(check.detail, 1024), inline=False)
    embed.set_footer(text="xd-mint-bot")
    await interaction.followup.send(embed=embed, ephemeral=True)


def run_check(settings: Settings) -> int:
    """Print connectivity checks and return a process exit code."""
    report = build_health_report(settings, ChiaMinter(settings), FilebaseUploader(settings))
    for check in report.checks:
        status = "OK" if check.ok else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")
    if report.ok:
        print("All checks passed.")
        return 0
    print("One or more checks failed.")
    return 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="xd-mint-bot",
        description="Discord bot that mints Chia NFTs from a seedling reaction.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration, the Chia wallet RPC, and Filebase, then exit.",
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    setup_logging(settings.log_level, settings.log_file)
    install_redaction(
        settings.discord_bot_token,
        settings.filebase_access_key,
        settings.filebase_secret_key,
    )
    log_startup(settings)
    log.info("discord.py %s", discord.__version__)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    sweep_old_temp_files(settings.temp_dir)

    if args.check:
        raise SystemExit(run_check(settings))

    bot = SeedlingMinterBot(settings)
    try:
        bot.run(settings.discord_bot_token, log_handler=None)
    except discord.LoginFailure:
        log.error("Discord rejected the bot token. Check DISCORD_BOT_TOKEN.")
        raise SystemExit(1)
    except discord.PrivilegedIntentsRequired:
        log.error("Enable the Message Content Intent for this bot in the Discord developer portal.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
