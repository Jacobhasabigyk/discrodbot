import discord
from discord.ext import commands
import asyncio
import threading

from config import TOKEN

# 👇 IMPORT YOUR VIEW
from cogs.tickets import TicketView, CloseTicketView, RefundView

# 👇 IMPORT SHOPIFY SERVER
from shopify_server import run_server

from services.shopify import sync_orders, sync_recent_orders, get_tracking_info
from database import save_orders_to_db

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

# ================================
# 🧠 DEBUG LOGGER
# ================================
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

        bot.add_view(RefundView())
        log("✅ Persistent views loaded")

    if not hasattr(bot, "sync_started"):
        bot.sync_started = True
        bot.loop.create_task(auto_sync_orders())
        log("✅ Shopify auto-sync started")

    # 🎟 PANEL SEND (SAFE)
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
                        description="Click below to open a support ticket.\n\nOur team will help you with orders, tracking, refunds, and more.",
                        color=0x00ff99
                    ),
                    view=TicketView(bot)
                )
                log("✅ Panel sent")

    except Exception as e:
        log("❌ Panel send failed")
        print(e)

    # 🔄 SYNC COMMANDS
    await sync_commands()

    print(f"🚀 Logged in as {bot.user} ({bot.user.id})")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    log(f"Message from {message.author}: {message.content}")
    await bot.process_commands(message)

# ================================
# ❌ GLOBAL ERROR HANDLER
# ================================
@bot.event
async def on_error(event, *args, **kwargs):
    print(f"❌ Error in event: {event}")
    import traceback
    traceback.print_exc()

# ================================
# 🔄 SHOPIFY SYNC LOOP
# ================================
async def auto_sync_orders():
    await bot.wait_until_ready()

    print("🔄 Initial full sync...")
    try:
        orders = sync_orders()
        save_orders_to_db(orders, get_tracking_info)
        print(f"✅ Initial cache loaded: {len(orders)} orders")
    except Exception as e:
        print("❌ Initial sync failed:", e)

    while True:
        try:
            print("⚡ Syncing recent orders...")
            orders = sync_recent_orders(10)
            save_orders_to_db(orders, get_tracking_info)
            print(f"⚡ Updated {len(orders)} orders")
        except Exception as e:
            print("❌ Sync error:", e)

        await asyncio.sleep(600)  # every 10 mins

# ================================
# 🚀 SAFE START SYSTEM
# ================================
async def start_bot():
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)

async def main():
    log("Booting bot...")

    # 🌐 START SHOPIFY SERVER
    threading.Thread(target=run_server, daemon=True).start()
    log("🌐 Shopify OAuth server running")

    retry_delay = 30  # start at 30 sec

    while True:
        try:
            await start_bot()
        except Exception as e:
            print("❌ Bot crashed:", e)

            print(f"⏳ Waiting {retry_delay}s before reconnect...")
            await asyncio.sleep(retry_delay)

            # 🔥 exponential backoff (prevents bans)
            retry_delay = min(retry_delay * 2, 300)  # max 5 mins

# ================================
# ▶️ RUN
# ================================
if __name__ == "__main__":
    asyncio.run(main())
