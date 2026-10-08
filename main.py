import asyncio
import os

import discord
from aiohttp import web
from discord.ext import commands

from config import TOKEN
from services import buttonland

# ================================
# ⚙️ INTENTS
# ================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# ================================
# 🤖 BOT SETUP
# ================================
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


def log(msg):
    print(f"[DEBUG] {msg}")


# ================================
# 📦 LOAD COGS
# ================================
COGS = [
    "cogs.admin",
    "cogs.moderation",
    "cogs.economy",
    "cogs.fun",
    "cogs.general",
    "cogs.logging",
    "cogs.tickets",
    "cogs.refunds",
]


async def load_cogs():
    log("Starting cog load...")

    for cog in COGS:
        try:
            await bot.load_extension(cog)
            log(f"✅ Loaded {cog}")
        except Exception as e:
            log(f"❌ Failed to load {cog}")
            print(e)


# ================================
# 🔄 COMMAND SYNC
# ================================
async def sync_commands():
    try:
        log("Syncing slash commands...")
        synced = await bot.tree.sync()
        log(f"✅ Synced {len(synced)} commands globally")
    except Exception as e:
        log("❌ Sync failed")
        print(e)


# ================================
# 🔌 EVENTS
# ================================
@bot.event
async def on_ready():
    log("Bot connected to Discord")

    # Check the store connection once at startup (no secrets printed).
    if not hasattr(bot, "store_checked"):
        bot.store_checked = True
        if not buttonland.is_configured():
            log("⚠️ Store not connected: set BUTTONLAND_API_URL and BUTTONLAND_BOT_KEY")
        else:
            try:
                store = await buttonland.store_info()
                log(f"✅ Connected to Buttonland store ({store.get('website') or 'ok'})")
            except Exception as e:
                log(f"⚠️ Store API not reachable yet: {e}")

    # The ticket panel is posted/updated by cogs/tickets.py.

    await sync_commands()

    print(f"🚀 Logged in as {bot.user} ({bot.user.id})")


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)


@bot.event
async def on_error(event, *args, **kwargs):
    print(f"❌ Error in event: {event}")
    import traceback
    traceback.print_exc()


# ================================
# 🚀 SAFE START SYSTEM
# ================================
async def start_bot():
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)


# ================================
# 🌐 HEALTH CHECK (for hosts like Render "Web Service")
# Render keeps restarting a web service that never opens a port. If the
# host gives us a PORT, answer on it so the service counts as healthy.
# ================================
async def start_health_server():
    port = os.getenv("PORT")
    if not port:
        return

    async def health(request):
        ready = bot.is_ready() if not bot.is_closed() else False
        return web.Response(text="ok" if ready else "starting")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    log(f"🌐 Health check listening on port {port}")


async def main():
    log("Booting bot...")

    await start_health_server()

    if not TOKEN:
        print("❌ DISCORD_TOKEN is not set. Add it to .env or your host's environment variables.")

    retry_delay = 30

    while True:
        try:
            await start_bot()
        except discord.LoginFailure as e:
            print("❌ Discord rejected the token:", e)
            print("   Reset it in the Discord Developer Portal (Bot > Reset Token) and set DISCORD_TOKEN again.")
            await asyncio.sleep(300)
        except Exception as e:
            print("❌ Bot crashed:", e)
            print(f"⏳ Waiting {retry_delay}s before reconnect...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 300)


if __name__ == "__main__":
    asyncio.run(main())
