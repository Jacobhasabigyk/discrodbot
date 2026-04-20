import discord
from discord.ext import commands
from discord import app_commands
import os
import re
import time
import random
import asyncio
import json

from openai import OpenAI
from config import SUPPORT_ROLE, OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE, BUYER_ROLE
from services.shopify import sync_orders, get_tracking_info
from services.emailer import send_verification_email

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =========================
# 🧠 STATE
# =========================
verified_users = {}
verification_codes = {}
conversation_memory = {}
takeover_channels = set()

STAFF_ROLES = {OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE, SUPPORT_ROLE}
# =========================
# 🚚 SHIPPING LOCK SYSTEM
# =========================

RESTRICTED_STATES = [
    "delaware", "new jersey", "rhode island", "district of columbia"
]

RESTRICTED_CITIES = [
    # NYC + boroughs
    "new york", "nyc", "bronx", "brooklyn", "queens", "staten island", "manhattan",

    # NY cities
    "yonkers", "buffalo", "rochester", "glen oaks", "floral park",

    # others
    "bridgeport", "stratford", "seaside",
    "philadelphia", "york"
]

RESTRICTED_COUNTIES = [
    "cook county",  # chicago restriction
    "king county"
]

# =========================
# 🚚 SHIPPING LOCK SYSTEM
# =========================

def check_shipping(msg: str):
    msg = msg.lower()

    # 🚫 NYC + boroughs
    if any(x in msg for x in [
        "new york", "nyc", "bronx", "brooklyn",
        "queens", "staten island", "manhattan"
    ]):
        return "❌ We do NOT ship to NYC or its boroughs due to local laws."

    # 🚫 other restricted cities
    if any(x in msg for x in [
        "yonkers", "buffalo", "rochester",
        "bridgeport", "stratford", "seaside",
        "philadelphia", "york"
    ]):
        return "❌ We do NOT ship to that location due to local laws."

    # 🚫 cook county ONLY (chicago restriction)
    if "cook county" in msg:
        return "❌ We do NOT ship to Cook County (Chicago area) due to local laws."

    # ⚠️ chicago allowed but warn
    if "chicago" in msg:
        return "⚠️ We ship to Illinois, but NOT Cook County (Chicago area)."

    # 🚫 restricted states
    if any(x in msg for x in [
        "delaware", "new jersey", "rhode island", "district of columbia"
    ]):
        return "❌ We do NOT ship to that state due to regulations."

    # ✅ texas explicitly allowed
    if "texas" in msg or "corpus" in msg:
        return "✅ Yes, we DO ship to Texas, including Corpus Christi."

    # 📦 generic shipping
    if any(x in msg for x in ["ship", "shipping", "deliver"]):
        return "📦 We ship within the United States only. Some areas are restricted due to laws."

    return None

try:
    with open("data.json", "r") as f:
        KNOWLEDGE = json.load(f)
except:
    KNOWLEDGE = {}

# =========================
# 📦 SHOPIFY CACHE
# =========================
order_cache = []
last_sync = 0

def get_orders_cached():
    global order_cache, last_sync
    if time.time() - last_sync > 120:
        print("🔄 Syncing orders...")
        order_cache = sync_orders()
        last_sync = time.time()
    return order_cache

def find_orders_by_email(orders, email):
    return [o for o in orders if o.get("email", "").lower() == email.lower()]

def extract_order_number(msg):
    match = re.search(r"#?(\d{3,6})", msg)
    return int(match.group(1)) if match else None

def is_staff(member):
    return any(role.id in STAFF_ROLES for role in member.roles)

def format_order(order, tracking):
    num = order.get("order_number")

    if not order.get("fulfillment_status"):
        return f"📦 Order #{num}\n🔴 Status: Not Fulfilled"

    if tracking:
        return f"""📦 Order #{num}

🟢 Status: Fulfilled
🚚 {tracking.get("company")}
📦 {tracking.get("number")}
🔗 {tracking.get("url")}"""

    return f"📦 Order #{num}\n🟡 Fulfilled (no tracking yet)"

# =========================
# 🎟 VIEWS
# =========================
class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.channel.delete()

class TicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        category = discord.utils.get(guild.categories, name="Tickets") or await guild.create_category("Tickets")
        channel = await guild.create_text_channel(f"ticket-{user.name}", category=category)

        await channel.set_permissions(guild.default_role, view_channel=False)
        await channel.set_permissions(user, view_channel=True)

        support = guild.get_role(SUPPORT_ROLE)
        if support:
            await channel.set_permissions(support, view_channel=True)

        await interaction.followup.send(f"✅ {channel.mention}", ephemeral=True)

        await channel.send(
            embed=discord.Embed(
                title="🎟 ButtonLand Support",
                description="Hey, Im the buttonland support ai chatbot i can help you today.",
                color=0x00ff00
            ),
            view=CloseTicketView()
        )

# =========================
# 🎟 COG
# =========================
class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="takeover")
    async def takeover(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ no permission", ephemeral=True)
            return

        takeover_channels.add(interaction.channel.id)
        await interaction.response.send_message("🛑 staff takeover")

    @app_commands.command(name="resume")
    async def resume(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ no permission", ephemeral=True)
            return

        takeover_channels.discard(interaction.channel.id)
        await interaction.response.send_message("🤖 AI back")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.interaction_metadata or message.content.startswith("/"):
            return
            
        if message.author.bot:
            return

        if not message.channel.name.startswith("ticket-"):
            return

        channel_id = message.channel.id
        content = message.content
        msg = content.lower()

        # =========================
        # 🔄 AUTO LOAD VERIFIED USER (DB)
        # =========================
        from database import get_verified_user, get_orders_by_email, get_order_from_db, save_verified_user

        if channel_id not in verified_users:
            verified_users[channel_id] = {}

        saved_email = get_verified_user(message.author.id)

        if saved_email and "orders" not in verified_users[channel_id]:
            orders = get_orders_by_email(saved_email)
            if orders:
                verified_users[channel_id]["orders"] = orders

        # 🛑 HARD STOP IF STAFF TOOK OVER
        if channel_id in takeover_channels:
            return

        # =========================
        # 🚚 SHIPPING CHECK
        # =========================
        shipping_reply = check_shipping(msg)
        if shipping_reply:
            await message.channel.send(shipping_reply)
            return

        # =========================
        # 🚨 STAFF ESCALATION
        # =========================


        # =========================
        # ⚠️ TOXIC FILTER
        # =========================
        if any(x in msg for x in ["retard", "nigger", "nigga", "fuck you"]):
            await message.channel.send("⚠️ keep it respectful or staff will step in")
            return

        # =========================
        # 📧 EMAIL VERIFY (DB)
        # =========================
        email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-z]{2,}", msg)
        if email_match:
            email = email_match.group(0)

            orders = get_orders_by_email(email)

            if not orders:
                await message.channel.send("❌ no orders found with that email")
                return

            code = str(random.randint(100000, 999999))

            verification_codes[channel_id] = {
                "code": code,
                "orders": orders,
                "expires": time.time() + 600
            }

            send_verification_email(email, code)

            await message.channel.send("📧 i sent a code, send it here 👍")
            return

        # =========================
        # 🔐 VERIFY CODE
        # =========================
        if msg.isdigit() and len(msg) == 6:
            data = verification_codes.get(channel_id)

            if not data:
                return

            if time.time() > data["expires"]:
                verification_codes.pop(channel_id, None)
                await message.channel.send("❌ code expired")
                return

            if msg != data["code"]:
                await message.channel.send("❌ wrong code")
                return

            verified_users[channel_id]["orders"] = data["orders"]
            verification_codes.pop(channel_id, None)

            save_verified_user(message.author.id, data["orders"][0].get("email"))

            role = message.guild.get_role(BUYER_ROLE)
            if role:
                try:
                    await message.author.add_roles(role)
                except:
                    pass

            await message.channel.send("✅ verified 👍")

            order = data["orders"][0]
            tracking = order.get("tracking")
            await message.channel.send(format_order(order, tracking))
            return

        # =========================
        # 🔒 SECURE ORDER TRACK (FAST DB)
        # =========================
        order_number = extract_order_number(msg)

        if order_number:
            user_orders = verified_users.get(channel_id, {}).get("orders")

            if not user_orders:
                await message.channel.send("❌ Please verify your order first by sending your email.")
                return

            order = next((o for o in user_orders if o.get("order_number") == order_number), None)

            if not order:
                await message.channel.send(
                    "❌ That order is not linked to your account.\n"
                    "If you need help, send your email to verify 👍"
                )
                return

            db_order = get_order_from_db(order_number)

            if db_order:
                await message.channel.send(format_order(db_order, db_order.get("tracking")))
                return

            await message.channel.send("⚠️ Order not cached yet, try again in a moment.")
            return

        # =========================
        # 📦 QUICK TRACK
        # =========================
        if "track" in msg or "where" in msg:
            user_orders = verified_users.get(channel_id, {}).get("orders")
            if user_orders:
                order = user_orders[0]
                await message.channel.send(format_order(order, order.get("tracking")))
                return

        # =========================
        # 🤖 AI RESPONSE
        # =========================
        try:
            await message.channel.typing()

            # 🧠 init memory
            if channel_id not in conversation_memory:
                conversation_memory[channel_id] = []

            history = conversation_memory[channel_id]

            # =========================
            # 🚨 SMART ESCALATION
            # =========================
            trigger_words = [
                "refund", "money back", "cancel order",
                "scam", "wtf", "this is bullshit",
                "human", "real person", "support agent",
                "help me", "not working", "issue"
            ]

            if any(word in msg for word in trigger_words):

                role = message.guild.get_role(SUPPORT_ROLE)

                # 💸 refund specific
                if "refund" in msg or "money back" in msg:
                    await message.channel.send(
                        "💸 i got you — a support agent will be here shortly.\n"
                        "👉 in the meantime, use `/refund` to speed up your request 👍"
                    )
                else:
                    await message.channel.send(
                        f"🛑 got you — a real support agent will help you shortly\n"
                        f"{role.mention if role else ''}"
                    )

                takeover_channels.add(channel_id)
                return

            # =========================
            # 🧠 USER CONTEXT
            # =========================
            user_orders = verified_users.get(channel_id, {}).get("orders")
            user_context = "User not verified"

            if user_orders:
                user_context = f"User verified. Order #{user_orders[0].get('order_number')}"

            # =========================
            # 📚 KNOWLEDGE MATCH
            # =========================
            knowledge_hits = [v for k, v in KNOWLEDGE.items() if k in msg]

            # =========================
            # 🧠 STORE MEMORY
            # =========================
            history.append({"role": "user", "content": content})
            history = history[-12:]
            conversation_memory[channel_id] = history

            # =========================
            # 🤖 AI RESPONSE
            # =========================
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": f"""
You are ButtonLand support.

STYLE:
- casual, short, human
- helpful, not robotic

RULES:
- if refund is mentioned → suggest using /refund
- if user sounds frustrated → suggest human support
- do NOT repeat yourself

{user_context}

Knowledge:
{knowledge_hits}
"""
                    },
                    *history
                ]
            )

            reply = response.choices[0].message.content[:1000]

            # 🧠 save AI reply
            history.append({"role": "assistant", "content": reply})

            await message.channel.send(reply)

        except Exception as e:
            print("AI error:", e)

async def setup(bot):
    await bot.add_cog(Tickets(bot))