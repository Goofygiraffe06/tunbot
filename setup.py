#!/usr/bin/env python3
"""Interactive setup script for TunBot."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class Colors:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        cls.BOLD = cls.DIM = cls.GREEN = cls.YELLOW = ""
        cls.RED = cls.CYAN = cls.RESET = ""


if not sys.stdout.isatty():
    Colors.disable()


BANNER = f"""
{Colors.CYAN}{Colors.BOLD}
  ████████╗██╗   ██╗███╗   ██╗██████╗  ██████╗ ████████╗
  ╚══██╔══╝██║   ██║████╗  ██║██╔══██╗██╔═══██╗╚══██╔══╝
     ██║   ██║   ██║██╔██╗ ██║██████╔╝██║   ██║   ██║
     ██║   ██║   ██║██║╚██╗██║██╔══██╗██║   ██║   ██║
     ██║   ╚██████╔╝██║ ╚████║██████╔╝╚██████╔╝   ██║
     ╚═╝    ╚═════╝ ╚═╝  ╚═══╝╚═════╝  ╚═════╝    ╚═╝
{Colors.RESET}
{Colors.DIM}  Cloudflare Tunnel Manager for Discord{Colors.RESET}
"""


def print_header(text: str):
    width = 52
    print()
    print(f"  {Colors.CYAN}{'─' * width}{Colors.RESET}")
    print(f"  {Colors.BOLD}{text.center(width)}{Colors.RESET}")
    print(f"  {Colors.CYAN}{'─' * width}{Colors.RESET}")


def print_step(step: int, total: int, text: str):
    print(f"\n  {Colors.CYAN}[{step}/{total}]{Colors.RESET} {Colors.BOLD}{text}{Colors.RESET}")
    print(f"  {Colors.DIM}{'─' * 48}{Colors.RESET}")


def print_ok(text: str):
    print(f"  {Colors.GREEN}✓{Colors.RESET} {text}")


def print_err(text: str):
    print(f"  {Colors.RED}✗{Colors.RESET} {text}")


def print_warn(text: str):
    print(f"  {Colors.YELLOW}!{Colors.RESET} {text}")


def print_info(text: str):
    print(f"  {Colors.DIM}│{Colors.RESET} {text}")


def prompt(text: str, default: str = "") -> str:
    if default:
        hint = f"{Colors.DIM}(default: {default}){Colors.RESET}"
        result = input(f"  {Colors.CYAN}>{Colors.RESET} {text} {hint}: ").strip()
        return result if result else default
    return input(f"  {Colors.CYAN}>{Colors.RESET} {text}: ").strip()


def prompt_yes_no(text: str, default: bool = True) -> bool:
    hint = f"{Colors.DIM}[Y/n]{Colors.RESET}" if default else f"{Colors.DIM}[y/N]{Colors.RESET}"
    result = input(f"  {Colors.CYAN}>{Colors.RESET} {text} {hint}: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


def check_python():
    if sys.version_info < (3, 8):
        print_err(f"Python 3.8+ required (found {sys.version_info.major}.{sys.version_info.minor})")
        sys.exit(1)
    print_ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")


def check_cloudflared():
    path = shutil.which("cloudflared")
    if not path:
        print_err("cloudflared not found")
        print_info("Install from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        print()
        if not prompt_yes_no("Continue without cloudflared?", default=False):
            sys.exit(1)
        print_warn("Install cloudflared before running the bot")
    else:
        try:
            result = subprocess.run(["cloudflared", "--version"], capture_output=True, text=True, timeout=5)
            version = result.stdout.strip().split()[2] if result.stdout else "unknown"
            print_ok(f"cloudflared {version}")
        except Exception:
            print_ok(f"cloudflared found")


def check_dependencies():
    requirements = Path("requirements.txt")
    if not requirements.exists():
        print_err("requirements.txt not found")
        sys.exit(1)

    venv_path = Path("venv")
    in_venv = sys.prefix != sys.base_prefix

    try:
        import discord
        import yaml
        import dotenv
        print_ok("Dependencies installed")
    except ImportError:
        print_warn("Missing dependencies")

        if not in_venv and not venv_path.exists():
            print_info("Creating virtual environment...")
            subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
            print_ok("Created venv/")

        if venv_path.exists() and not in_venv:
            pip_path = venv_path / "bin" / "pip"
            if not pip_path.exists():
                pip_path = venv_path / "Scripts" / "pip.exe"
        else:
            pip_path = Path(sys.executable).parent / "pip"

        print_info("Installing dependencies...")
        subprocess.run([str(pip_path), "install", "-q", "-r", "requirements.txt"], check=True)
        print_ok("Dependencies installed")

        # Re-exec with venv Python so imports work
        if venv_path.exists() and not in_venv:
            venv_python = venv_path / "bin" / "python"
            if not venv_python.exists():
                venv_python = venv_path / "Scripts" / "python.exe"
            print_info("Restarting with venv...")
            os.execv(str(venv_python), [str(venv_python), __file__] + sys.argv[1:])


def validate_discord_id(value: str) -> bool:
    return value.isdigit() and len(value) >= 17


def validate_port(value: str) -> bool:
    try:
        return 1 <= int(value) <= 65535
    except ValueError:
        return False


def validate_duration(value: str) -> bool:
    if not value:
        return False
    return bool(re.match(r'^(\d+h)?(\d+m)?(\d+s)?$|^\d+$', value.lower()))


def get_invite_url(token: str) -> str:
    """Extract client ID from token and generate invite URL."""
    if not token or token == "your_bot_token_here":
        return ""
    try:
        import base64
        encoded = token.split(".")[0]
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        client_id = base64.b64decode(encoded).decode()
        permissions = 2048  # Send Messages
        return f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions={permissions}&scope=bot"
    except Exception:
        return ""


def configure_bot():
    token = prompt("Discord bot token", "leave empty to use env var")
    if token == "leave empty to use env var":
        token = "your_bot_token_here"
        print_warn("Set DISCORD_BOT_TOKEN environment variable")
    else:
        print_ok("Token configured")

    print()
    print_info("To get your Discord user ID:")
    print_info("1. Enable Developer Mode in Discord settings")
    print_info("2. Right-click your name → Copy User ID")
    print()

    allowed_users = []
    while True:
        user_id = prompt("Discord user ID", "press Enter when done")
        if user_id == "press Enter when done" or not user_id:
            if not allowed_users:
                print_err("At least one user ID required")
                continue
            break
        if not validate_discord_id(user_id):
            print_err("Invalid ID (must be 17+ digits)")
            continue
        allowed_users.append(int(user_id))
        print_ok(f"Added user {user_id}")

    return token, allowed_users


def configure_defaults():
    duration = prompt("Default tunnel duration", "1h")
    while not validate_duration(duration):
        print_err("Use format: 30m, 1h, 2h30m")
        duration = prompt("Default tunnel duration", "1h")
    print_ok(f"Duration: {duration}")

    max_concurrent = prompt("Max concurrent tunnels", "3")
    while not max_concurrent.isdigit() or int(max_concurrent) < 1:
        print_err("Must be a positive number")
        max_concurrent = prompt("Max concurrent tunnels", "3")
    print_ok(f"Max tunnels: {max_concurrent}")

    return duration, int(max_concurrent)


def configure_services():
    print_info("Define services that can be tunneled")
    print()

    services = {}

    while True:
        name = prompt("Service name", "press Enter when done")
        if name == "press Enter when done" or not name:
            if not services:
                print_err("At least one service required")
                continue
            break

        name = name.lower().replace(" ", "_")
        if name in services:
            print_err(f"'{name}' already exists")
            continue

        port = prompt(f"  Port for {name}")
        while not validate_port(port):
            print_err("Invalid port (1-65535)")
            port = prompt(f"  Port for {name}")

        description = prompt(f"  Description", f"{name} service")

        max_duration = prompt(f"  Max duration", "2h")
        while not validate_duration(max_duration):
            print_err("Use format: 30m, 1h, 2h30m")
            max_duration = prompt(f"  Max duration", "2h")

        services[name] = {
            "port": int(port),
            "description": description,
            "max_duration": max_duration,
        }
        print_ok(f"Added {name} (:{port})")
        print()

    return services


def write_config(token, allowed_users, duration, max_concurrent, services):
    import yaml

    config_path = Path("config.yaml")
    if config_path.exists():
        if not prompt_yes_no("Overwrite existing config.yaml?", default=False):
            print_warn("Configuration not saved")
            return False

    config = {
        "bot_token": token,
        "allowed_users": allowed_users,
        "defaults": {
            "duration": duration,
            "max_concurrent": max_concurrent,
        },
        "services": services,
    }

    with open(config_path, "w") as f:
        f.write("# TunBot Configuration\n\n")
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print_ok("Created config.yaml")
    return True


def setup_env(token: str):
    if token and token != "your_bot_token_here":
        return

    env_path = Path(".env")
    if env_path.exists():
        return

    print()
    if prompt_yes_no("Create .env file for bot token?"):
        token = prompt("Discord bot token")
        if token:
            env_path.write_text(f"DISCORD_BOT_TOKEN={token}\n")
            print_ok("Created .env")


def setup_systemd():
    """Generate systemd service file for persistent operation."""
    if not shutil.which("systemctl"):
        return

    print()
    if not prompt_yes_no("Generate systemd service file for auto-start?", default=False):
        return

    bot_dir = Path.cwd().resolve()
    venv_python = bot_dir / "venv" / "bin" / "python"
    if not venv_python.exists():
        venv_python = shutil.which("python3") or "python3"

    service_content = f"""[Unit]
Description=TunBot - Discord Cloudflare Tunnel Manager
After=network.target

[Service]
Type=simple
User={os.getenv('USER', 'root')}
WorkingDirectory={bot_dir}
ExecStart={venv_python} {bot_dir}/tunbot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

    service_path = Path("tunbot.service")
    service_path.write_text(service_content)
    print_ok("Created tunbot.service")

    print()
    print_info("To install the service:")
    print_info(f"  {Colors.BOLD}sudo cp tunbot.service /etc/systemd/system/{Colors.RESET}")
    print_info(f"  {Colors.BOLD}sudo systemctl daemon-reload{Colors.RESET}")
    print_info(f"  {Colors.BOLD}sudo systemctl enable --now tunbot{Colors.RESET}")
    print()
    print_info("To check status:")
    print_info(f"  {Colors.BOLD}sudo systemctl status tunbot{Colors.RESET}")
    print_info(f"  {Colors.BOLD}journalctl -u tunbot -f{Colors.RESET}")


def main():
    print(BANNER)

    os.chdir(Path(__file__).parent)

    print_step(1, 5, "Checking Requirements")
    check_python()
    check_cloudflared()
    check_dependencies()

    config_path = Path("config.yaml")
    token = None
    existing_config = None

    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                existing_config = yaml.safe_load(f)
            token = existing_config.get("bot_token", "")
        except Exception:
            pass

        print()
        print_warn("config.yaml already exists")
        print_info("1) Skip - use existing config")
        print_info("2) Add service - add a new service")
        print_info("3) Reconfigure - start fresh")
        print()
        choice = prompt("Choice", "1")

        if choice == "2" and existing_config:
            print_step(2, 3, "Add Service")
            new_services = configure_services()
            existing_config["services"].update(new_services)
            with open(config_path, "w") as f:
                f.write("# TunBot Configuration\n\n")
                yaml.dump(existing_config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            print_ok("Services added to config.yaml")
        elif choice == "3":
            token = None
        else:
            print_ok("Using existing configuration")

    if token is None:
        print_step(2, 5, "Bot Configuration")
        token, allowed_users = configure_bot()

        print_step(3, 5, "Default Settings")
        duration, max_concurrent = configure_defaults()

        print_step(4, 5, "Service Configuration")
        services = configure_services()

        print_step(5, 5, "Writing Configuration")
        write_config(token, allowed_users, duration, max_concurrent, services)
        setup_env(token)

    print_header("Setup Complete")

    venv_exists = Path("venv").exists()
    in_venv = sys.prefix != sys.base_prefix

    print()
    print_info(f"{Colors.BOLD}Discord Developer Portal Setup:{Colors.RESET}")
    print_info("1. Go to: https://discord.com/developers/applications")
    print_info("2. Select your bot → Bot section")
    print_info(f"3. Enable: {Colors.BOLD}MESSAGE CONTENT INTENT{Colors.RESET}")
    print_info(f"4. Disable: {Colors.BOLD}REQUIRES OAUTH2 CODE GRANT{Colors.RESET}")

    invite_url = get_invite_url(token) if token else ""
    if invite_url:
        print()
        print_info("Invite the bot to your server:")
        print(f"\n    {Colors.BOLD}{invite_url}{Colors.RESET}\n")
        print_info("After inviting, you can DM the bot directly.")

    print()
    if venv_exists and not in_venv:
        print_info("Run the bot:")
        print(f"\n    {Colors.BOLD}source venv/bin/activate && python tunbot.py{Colors.RESET}\n")
    else:
        print_info("Run the bot:")
        print(f"\n    {Colors.BOLD}python tunbot.py{Colors.RESET}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {Colors.DIM}Setup cancelled.{Colors.RESET}\n")
        sys.exit(0)
