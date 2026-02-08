# tunbot

Discord bot for temporary Cloudflare tunnels to local services behind CGNAT.

I needed a way to quickly expose stuff running on my home server without dealing with port forwarding that doesn't work anyway. This uses cloudflared's free quick tunnels - no account needed.

## How it works

- Whitelist services in config (port, max duration)
- Start tunnels via Discord commands
- Get a public URL that auto-expires
- Only responds to authorized users

## Setup
```bash
git clone https://github.com/Goofygiraffe06/tunbot
cd tunbot
python3 setup.py
```

Setup script handles venv, dependencies, and config. Optionally generates a systemd service.

## Config

Edit `config.yaml`:
```yaml
bot_token: ""
allowed_users:
  - 123456789012345678

defaults:
  duration: "1h"
  max_concurrent: 5

services:
  gitea:
    port: 3000
    max_duration: "2h"
  
  vikunja:
    port: 3456
    max_duration: "1h"
```

Token can also go in `.env` as `DISCORD_BOT_TOKEN`.

Get your Discord ID: Settings / Advanced / Developer Mode, then right-click your name.

## Commands
```
!tunnel list              
!tunnel start gitea       
!tunnel start gitea 30m   
!tunnel stop gitea        
!tunnel status            
```

Returns a URL like `https://random-name.trycloudflare.com` that works until expiry.

## Running
```bash
source venv/bin/activate
python tunbot.py
```

Or install as a service:
```bash
sudo cp tunbot.service /etc/systemd/system/
sudo systemctl enable --now tunbot
```

## Requirements

- Python 3.8+
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
- Discord bot token

## License

GPLv3