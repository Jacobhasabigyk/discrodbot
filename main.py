import asyncio

import discord
from discord.ext import commands

from config import TOKEN
from cogs.tickets import CloseTicketView, TicketView
from services import buttonland

# ================================
# ⚙️ INTENTS
# ================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

PANEL_CHANNEL_ID = 1476797889509593211

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

    if not hasattr(bot, "views_loaded"):
        bot.views_loaded = True
        bot.add_view(TicketView(bot))
        bot.add_view(CloseTicketView())
        log("✅ Persistent views loaded")

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

    # 🎟 PANEL SEND
    try:
        channel = bot.get_channel(PANEL_CHANNEL_ID)

        if channel:
            async for msg in channel.history(limit=20):
                if msg.author == bot.user and msg.components:
                    log("✅ Panel already exists")
                    break
            else:
                await channel.send(
                    embed=discord.Embed(
                        title="🎟 ButtonLand Support",
                        description="Click below to open a support ticket.\n\nWe can help with orders, tracking, returns, refunds, and product questions.",
                        color=0xE5231F
                    ),
                    view=TicketView(bot)
                )
                log("✅ Panel sent")

    except Exception as e:
        log("❌ Panel send failed")
        print(e)

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


async def main():
    log("Booting bot...")

    retry_delay = 30

    while True:
        try:
            await start_bot()
        except Exception as e:
            print("❌ Bot crashed:", e)
            print(f"⏳ Waiting {retry_delay}s before reconnect...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 300)


if __name__ == "__main__":
    asyncio.run(main())
