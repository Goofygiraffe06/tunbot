#!/usr/bin/env python3
"""TunBot - Discord bot for managing temporary Cloudflare tunnels."""

import asyncio
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import discord
import yaml
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tunbot")


@dataclass
class ServiceConfig:
    port: int
    description: str
    max_duration: int


@dataclass
class TunnelInfo:
    service: str
    port: int
    pid: int
    url: str
    expiry: float
    user_id: int

    @property
    def remaining_seconds(self) -> int:
        return max(0, int(self.expiry - time.time()))

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expiry


def parse_duration(value) -> int:
    """Parse duration string (1h30m, 45s, 2h) or int to seconds."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return 3600

    total = 0
    current = ""
    for char in value.lower():
        if char.isdigit():
            current += char
        elif char == "h" and current:
            total += int(current) * 3600
            current = ""
        elif char == "m" and current:
            total += int(current) * 60
            current = ""
        elif char == "s" and current:
            total += int(current)
            current = ""

    if current:
        total += int(current)

    return total if total > 0 else 3600


@dataclass
class BotConfig:
    token: str
    allowed_users: set[int]
    services: dict[str, ServiceConfig]
    default_duration: int
    max_concurrent: int

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "BotConfig":
        path = Path(config_path)
        if not path.exists():
            logger.error(f"Config file not found: {config_path}")
            logger.error("Copy config.example.yaml to config.yaml and configure it")
            sys.exit(1)

        with open(path) as f:
            data = yaml.safe_load(f)

        token = os.environ.get("DISCORD_BOT_TOKEN") or data.get("bot_token", "")
        if not token or token == "your_bot_token_here":
            logger.error("Bot token not configured")
            logger.error("Set DISCORD_BOT_TOKEN environment variable or add to config.yaml")
            sys.exit(1)

        allowed_users = set(data.get("allowed_users", []))
        if not allowed_users:
            logger.error("No allowed users configured in config.yaml")
            sys.exit(1)

        defaults = data.get("defaults", {})
        default_duration = parse_duration(defaults.get("duration", "1h"))

        services_data = data.get("services", {})
        if not services_data:
            logger.error("No services configured in config.yaml")
            sys.exit(1)

        services = {}
        for name, cfg in services_data.items():
            if "port" not in cfg:
                logger.error(f"Service '{name}' missing required 'port' field")
                sys.exit(1)
            max_dur = parse_duration(cfg.get("max_duration", default_duration))
            services[name] = ServiceConfig(
                port=cfg["port"],
                description=cfg.get("description", ""),
                max_duration=max_dur,
            )

        return cls(
            token=token,
            allowed_users=allowed_users,
            services=services,
            default_duration=default_duration,
            max_concurrent=defaults.get("max_concurrent", 3),
        )


class TunnelManager:
    def __init__(self):
        self._tunnels: dict[str, TunnelInfo] = {}
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        return len(self._tunnels)

    @property
    def active_tunnels(self) -> list[TunnelInfo]:
        return list(self._tunnels.values())

    def get(self, service: str) -> Optional[TunnelInfo]:
        return self._tunnels.get(service)

    def is_active(self, service: str) -> bool:
        return service in self._tunnels

    async def start(
        self, service: str, port: int, duration: int, user_id: int
    ) -> tuple[bool, str]:
        async with self._lock:
            if service in self._tunnels:
                return False, "Tunnel already active for this service"

            try:
                process = await asyncio.create_subprocess_exec(
                    "cloudflared",
                    "tunnel",
                    "--url",
                    f"http://localhost:{port}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                return False, "cloudflared not found in PATH"
            except Exception as e:
                return False, f"Failed to start tunnel: {e}"

            url = await self._extract_url(process)
            if not url:
                self._terminate_process(process.pid)
                return False, "Failed to get tunnel URL (timeout or parse error)"

            tunnel = TunnelInfo(
                service=service,
                port=port,
                pid=process.pid,
                url=url,
                expiry=time.time() + duration,
                user_id=user_id,
            )
            self._tunnels[service] = tunnel
            logger.info(f"Tunnel started: {service} -> {url} (PID: {process.pid})")
            return True, url

    async def stop(self, service: str) -> tuple[bool, str]:
        async with self._lock:
            tunnel = self._tunnels.pop(service, None)
            if not tunnel:
                return False, "No active tunnel for this service"

            self._terminate_process(tunnel.pid)
            logger.info(f"Tunnel stopped: {service} (PID: {tunnel.pid})")
            return True, f"Tunnel for {service} stopped"

    async def cleanup_expired(self) -> list[str]:
        async with self._lock:
            expired = [s for s, t in self._tunnels.items() if t.is_expired]
            for service in expired:
                tunnel = self._tunnels.pop(service)
                self._terminate_process(tunnel.pid)
                logger.info(f"Tunnel expired: {service} (PID: {tunnel.pid})")
            return expired

    async def cleanup_all(self):
        async with self._lock:
            for service, tunnel in self._tunnels.items():
                self._terminate_process(tunnel.pid)
                logger.info(f"Tunnel cleaned up: {service} (PID: {tunnel.pid})")
            self._tunnels.clear()

    async def _extract_url(
        self, process: asyncio.subprocess.Process, timeout: float = 30.0
    ) -> Optional[str]:
        url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        deadline = time.time() + timeout

        while time.time() < deadline:
            if process.returncode is not None:
                return None

            try:
                line = await asyncio.wait_for(
                    process.stderr.readline(), timeout=min(1.0, deadline - time.time())
                )
                if line:
                    text = line.decode("utf-8", errors="ignore")
                    match = url_pattern.search(text)
                    if match:
                        return match.group(0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return None

        return None

    def _terminate_process(self, pid: int):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning(f"Failed to terminate process {pid}: {e}")


def check_dependencies():
    if not shutil.which("cloudflared"):
        logger.error("cloudflared is not installed or not in PATH")
        logger.error("Install it from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        sys.exit(1)
    logger.info("Dependencies OK: cloudflared found")


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


class Colors:
    SUCCESS = 0x2ecc71
    ERROR = 0xe74c3c
    INFO = 0x3498db
    WARNING = 0xf39c12


class TunBot(commands.Bot):
    def __init__(self, config: BotConfig):
        # Minimal intents - only what we need
        intents = discord.Intents.none()
        intents.guilds = True  # Required for bot to work
        intents.message_content = True  # Required for commands
        intents.dm_messages = True  # For DM commands

        super().__init__(
            command_prefix="!",
            intents=intents,
            activity=discord.Activity(type=discord.ActivityType.watching, name="!tunnel help"),
            description="Cloudflare Tunnel Manager - Use !tunnel help to get started",
            # Resource optimizations
            member_cache_flags=discord.MemberCacheFlags.none(),
            chunk_guilds_at_startup=False,
            max_messages=None,  # Disable message cache
        )
        self.config = config
        self.tunnels = TunnelManager()

    async def setup_hook(self):
        self.cleanup_task.start()

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Serving {len(self.config.services)} services for {len(self.config.allowed_users)} users")

    async def close(self):
        logger.info("Shutting down, cleaning up tunnels...")
        self.cleanup_task.cancel()
        await self.tunnels.cleanup_all()
        await super().close()

    @tasks.loop(seconds=60)  # Check every 60s instead of 30s
    async def cleanup_task(self):
        expired = await self.tunnels.cleanup_expired()
        for service in expired:
            logger.info(f"Auto-expired tunnel: {service}")

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.wait_until_ready()


bot: Optional[TunBot] = None


def is_allowed():
    async def predicate(ctx: commands.Context) -> bool:
        allowed = ctx.author.id in bot.config.allowed_users
        if not allowed:
            logger.warning(f"Unauthorized access attempt: {ctx.author} ({ctx.author.id})")
        return allowed

    return commands.check(predicate)


@commands.group(invoke_without_command=True)
@is_allowed()
async def tunnel(ctx: commands.Context):
    """Tunnel management commands."""
    embed = discord.Embed(
        title="Tunnel Commands",
        description="`!tunnel list` - Show available services\n"
                    "`!tunnel start <service> [duration]` - Start a tunnel\n"
                    "`!tunnel stop <service>` - Stop a tunnel\n"
                    "`!tunnel status` - Show active tunnels\n"
                    "`!tunnel ping` - Show diagnostics",
        color=Colors.INFO,
    )
    await ctx.send(embed=embed)


@tunnel.command(name="list")
@is_allowed()
async def tunnel_list(ctx: commands.Context):
    """List all configured services."""
    embed = discord.Embed(title="Available Services", color=Colors.INFO)

    for name, svc in bot.config.services.items():
        status = "ACTIVE" if bot.tunnels.is_active(name) else "inactive"
        max_dur = format_duration(svc.max_duration)
        embed.add_field(
            name=f"{name} (port {svc.port})",
            value=f"{svc.description}\nStatus: {status} | Max: {max_dur}",
            inline=False,
        )

    await ctx.send(embed=embed)
    logger.info(f"User {ctx.author} listed services")


@tunnel.command(name="start")
@is_allowed()
async def tunnel_start(ctx: commands.Context, service: str, duration: Optional[int] = None):
    """Start a tunnel for a service."""
    service = service.lower()

    if service not in bot.config.services:
        embed = discord.Embed(
            title="Unknown Service",
            description=f"Service `{service}` not found.\nUse `!tunnel list` to see available services.",
            color=Colors.ERROR,
        )
        await ctx.send(embed=embed)
        return

    if bot.tunnels.count >= bot.config.max_concurrent:
        embed = discord.Embed(
            title="Limit Reached",
            description=f"Maximum concurrent tunnels ({bot.config.max_concurrent}) reached.",
            color=Colors.ERROR,
        )
        await ctx.send(embed=embed)
        return

    svc = bot.config.services[service]
    duration = duration or bot.config.default_duration
    duration = min(duration, svc.max_duration)

    if duration < 60:
        embed = discord.Embed(
            title="Invalid Duration",
            description="Minimum duration is 60 seconds.",
            color=Colors.ERROR,
        )
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(
        title="Starting Tunnel",
        description=f"Initializing tunnel for **{service}**...",
        color=Colors.WARNING,
    )
    status_msg = await ctx.send(embed=embed)

    success, result = await bot.tunnels.start(service, svc.port, duration, ctx.author.id)

    if success:
        dur_str = format_duration(duration)
        embed = discord.Embed(title="Tunnel Active", color=Colors.SUCCESS)
        embed.add_field(name="Service", value=service, inline=True)
        embed.add_field(name="Port", value=str(svc.port), inline=True)
        embed.add_field(name="Duration", value=dur_str, inline=True)
        embed.add_field(name="URL", value=result, inline=False)
        await status_msg.edit(embed=embed)
        logger.info(f"User {ctx.author} started tunnel: {service} for {dur_str}")
    else:
        embed = discord.Embed(
            title="Tunnel Failed",
            description=result,
            color=Colors.ERROR,
        )
        await status_msg.edit(embed=embed)
        logger.error(f"User {ctx.author} failed to start tunnel {service}: {result}")


@tunnel.command(name="stop")
@is_allowed()
async def tunnel_stop(ctx: commands.Context, service: str):
    """Stop an active tunnel."""
    service = service.lower()

    if service not in bot.config.services:
        embed = discord.Embed(
            title="Unknown Service",
            description=f"Service `{service}` not found.",
            color=Colors.ERROR,
        )
        await ctx.send(embed=embed)
        return

    success, result = await bot.tunnels.stop(service)

    if success:
        embed = discord.Embed(
            title="Tunnel Stopped",
            description=f"Tunnel for **{service}** has been terminated.",
            color=Colors.SUCCESS,
        )
        await ctx.send(embed=embed)
        logger.info(f"User {ctx.author} stopped tunnel: {service}")
    else:
        embed = discord.Embed(
            title="Stop Failed",
            description=result,
            color=Colors.ERROR,
        )
        await ctx.send(embed=embed)


@tunnel.command(name="status")
@is_allowed()
async def tunnel_status(ctx: commands.Context):
    """Show all active tunnels."""
    tunnels = bot.tunnels.active_tunnels

    if not tunnels:
        embed = discord.Embed(
            title="Tunnel Status",
            description="No active tunnels.",
            color=Colors.INFO,
        )
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(title="Active Tunnels", color=Colors.SUCCESS)
    for t in tunnels:
        remaining = format_duration(t.remaining_seconds)
        embed.add_field(
            name=t.service,
            value=f"URL: {t.url}\nPort: {t.port} | Remaining: {remaining}",
            inline=False,
        )

    await ctx.send(embed=embed)
    logger.info(f"User {ctx.author} checked tunnel status")


@tunnel.command(name="ping")
@is_allowed()
async def tunnel_ping(ctx: commands.Context):
    """Check if bot is responsive."""
    await ctx.send("Pong!")


@tunnel.error
@tunnel_list.error
@tunnel_start.error
@tunnel_stop.error
@tunnel_status.error
@tunnel_ping.error
async def tunnel_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CheckFailure):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="Missing Argument",
            description=f"Required argument: `{error.param.name}`",
            color=Colors.ERROR,
        )
        await ctx.send(embed=embed)
        return
    logger.error(f"Command error: {error}")
    embed = discord.Embed(
        title="Error",
        description="An error occurred while processing the command.",
        color=Colors.ERROR,
    )
    await ctx.send(embed=embed)


def main():
    global bot

    check_dependencies()
    config = BotConfig.load()

    bot = TunBot(config)
    bot.add_command(tunnel)

    try:
        bot.run(config.token, log_handler=None)
    except discord.LoginFailure:
        logger.error("Invalid bot token. Check your configuration.")
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
