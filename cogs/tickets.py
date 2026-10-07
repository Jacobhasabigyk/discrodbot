import asyncio
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from config import BUYER_ROLE, HEAD_MOD_ROLE, LOG_CHANNEL, MOD_ROLE, OWNER_ROLE, SUPPORT_ROLE
from database import (
    delete_ticket,
    get_open_ticket_for,
    get_ticket,
    save_ticket,
    set_ticket_paused,
)
from services import buttonland
from services.buttonland import ButtonlandError
from services.support_ai import SupportAgent

# Staff = these roles, the server owner, or anyone with Administrator.
ADMIN_ROLE = 1484473034462199849
STAFF_ROLES = {OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE, SUPPORT_ROLE, ADMIN_ROLE}

# Where new refund/cancel requests from tickets are announced for staff.
REQUEST_LOG_CHANNEL = 1485943251855867944

# Cost/abuse guard: AI replies per ticket per day before handing to staff.
MAX_AI_REPLIES_PER_DAY = 40

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
CODE_RE = re.compile(r"^\s*(\d{3})\s?-?\s?(\d{3})\s*$")
HUMAN_RE = re.compile(r"\b(?:human|real person|live agent|support agent|representative)\b|\b(?:talk|speak) to (?:a |an |the )?(?:person|someone|staff|owner|mod)\b", re.I)
SLURS = ["retard", "nigger", "nigga"]

agent = SupportAgent()
ticket_locks = {}
ai_usage = {}  # channel_id -> [day, count]


def is_staff(member):
    if not isinstance(member, discord.Member):
        return False
    if member.guild and member.guild.owner_id == member.id:
        return True
    if member.guild_permissions.administrator:
        return True
    return any(role.id in STAFF_ROLES for role in member.roles)


def money(value):
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def order_embed(order, title=None):
    embed = discord.Embed(
        title=title or f"📦 {order.get('orderNumber')}",
        description=f"**{order.get('statusText', 'Unknown')}**",
        color=0xE5231F,
    )

    items = order.get("items") or []
    if items:
        lines = [f"{i.get('quantity')}× {i.get('name')}" + (f" ({i.get('options')})" if i.get("options") else "") for i in items]
        embed.add_field(name="Items", value="\n".join(lines)[:1000], inline=False)

    embed.add_field(name="Total", value=money(order.get("total")), inline=True)
    if order.get("shippingMethod"):
        embed.add_field(name="Shipping", value=order["shippingMethod"], inline=True)

    for shipment in (order.get("shipments") or [])[:3]:
        parts = [f"**{shipment.get('statusText')}**"]
        carrier = " ".join(x for x in [shipment.get("carrier"), shipment.get("service")] if x)
        if carrier:
            parts.append(carrier)
        if shipment.get("trackingNumber"):
            number = shipment["trackingNumber"]
            parts.append(f"[{number}]({shipment['trackingUrl']})" if shipment.get("trackingUrl") else number)
        latest = shipment.get("latestCarrierUpdate") or {}
        if latest.get("text"):
            parts.append(f"Latest: {latest['text']}")
        embed.add_field(name="Package", value="\n".join(parts)[:1000], inline=False)

    for refund in (order.get("refunds") or [])[:3]:
        embed.add_field(
            name="Refund",
            value=f"{money(refund.get('amount'))} · {refund.get('status')}",
            inline=True,
        )

    return embed


# =========================
# 🎟 VIEWS
# =========================
class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = get_ticket(interaction.channel.id)
        owner_id = ticket["owner_id"] if ticket else None

        if interaction.user.id != owner_id and not is_staff(interaction.user):
            return await interaction.response.send_message("only the ticket owner or staff can close this.", ephemeral=True)

        await interaction.response.send_message("🔒 closing this ticket in 5 seconds…")

        log = interaction.client.get_channel(LOG_CHANNEL)
        if log:
            embed = discord.Embed(title="🎟 Ticket closed", color=0x95A5A6)
            embed.add_field(name="Ticket", value=interaction.channel.name)
            embed.add_field(name="Opened by", value=f"<@{owner_id}>" if owner_id else "unknown")
            embed.add_field(name="Closed by", value=interaction.user.mention)
            try:
                await log.send(embed=embed)
            except discord.HTTPException:
                pass

        await asyncio.sleep(5)
        delete_ticket(interaction.channel.id)
        agent.forget(interaction.channel.id)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            pass


class TicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        # One open ticket per person.
        for channel_id in get_open_ticket_for(user.id):
            existing = guild.get_channel(channel_id)
            if existing:
                return await interaction.followup.send(f"you already have a ticket open: {existing.mention}", ephemeral=True)
            delete_ticket(channel_id)

        category = discord.utils.get(guild.categories, name="Tickets") or await guild.create_category("Tickets")

        # Private from the start: only the customer, staff and the bot.
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        for role_id in STAFF_ROLES:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        safe_name = re.sub(r"[^a-z0-9-]", "", user.name.lower())[:80] or str(user.id)
        channel = await guild.create_text_channel(f"ticket-{safe_name}", category=category, overwrites=overwrites)
        save_ticket(channel.id, user.id, time.time())

        await interaction.followup.send(f"✅ your ticket: {channel.mention}", ephemeral=True)

        embed = discord.Embed(
            title="🎟 ButtonLand Support",
            description=(
                f"hey {user.mention}! i'm Buttonland's AI support assistant.\n\n"
                "**i can:**\n"
                "• check your order and tracking (i'll email you a code to confirm it's yours)\n"
                "• answer shipping, returns and product questions\n"
                "• send a refund or cancellation request to the owner\n\n"
                "what's up? say **human** any time to get a team member."
            ),
            color=0xE5231F,
        )
        await channel.send(embed=embed, view=CloseTicketView())


# =========================
# 🎟 COG
# =========================
class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def cog_unload(self):
        asyncio.create_task(buttonland.close())

    # -------------------------
    # Staff controls
    # -------------------------
    @app_commands.command(name="takeover", description="Staff: stop the AI in this ticket")
    async def takeover(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ no permission", ephemeral=True)
        if not get_ticket(interaction.channel.id):
            return await interaction.response.send_message("this isn't a ticket.", ephemeral=True)
        set_ticket_paused(interaction.channel.id, True)
        await interaction.response.send_message("🛑 staff has this ticket. the AI is paused (`/resume` to turn it back on).")

    @app_commands.command(name="resume", description="Staff: let the AI answer in this ticket again")
    async def resume(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ no permission", ephemeral=True)
        if not get_ticket(interaction.channel.id):
            return await interaction.response.send_message("this isn't a ticket.", ephemeral=True)
        set_ticket_paused(interaction.channel.id, False)
        await interaction.response.send_message("🤖 AI assistant is back on.")

    # -------------------------
    # Customer commands
    # -------------------------
    @app_commands.command(name="myorders", description="See your Buttonland orders (after verifying your email in a ticket)")
    async def myorders(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            data = await buttonland.customer(interaction.user.id)
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)

        if not data.get("linked"):
            return await interaction.followup.send("you haven't verified an email yet. open a ticket and send the email you ordered with 👍", ephemeral=True)

        orders = data.get("orders") or []
        if not orders:
            return await interaction.followup.send(f"no orders found for {data.get('email')}.", ephemeral=True)

        await interaction.followup.send(
            content=f"orders for {data.get('email')}:",
            embeds=[order_embed(order) for order in orders[:5]],
            ephemeral=True,
        )

    @app_commands.command(name="unlink", description="Remove the email linked to your Discord account")
    async def unlink(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await buttonland.unlink(interaction.user.id)
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)
        await interaction.followup.send("done, your email is no longer linked.", ephemeral=True)

    # -------------------------
    # Helpers
    # -------------------------
    async def give_buyer_role(self, member, orders):
        if not orders or not isinstance(member, discord.Member):
            return
        role = member.guild.get_role(BUYER_ROLE)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="Verified Buttonland customer")
            except discord.HTTPException:
                pass

    async def hand_off(self, channel, reason):
        set_ticket_paused(channel.id, True)
        role = channel.guild.get_role(SUPPORT_ROLE)
        await channel.send(
            f"🙋 {role.mention if role else 'staff'} — a customer needs help here.\n> {reason[:300]}",
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

    async def announce_request(self, channel, member, request):
        log = self.bot.get_channel(REQUEST_LOG_CHANNEL)
        if not log or not request:
            return
        kind = "Cancellation" if request.get("type") == "cancel" else "Refund"
        embed = discord.Embed(title=f"📝 {kind} request from Discord", color=0xF5A623)
        embed.add_field(name="Order", value=request.get("orderNumber", "?"))
        if request.get("amount"):
            embed.add_field(name="Amount (suggested)", value=money(request["amount"]))
        embed.add_field(name="Customer", value=member.mention)
        embed.add_field(name="Ticket", value=channel.mention)
        embed.set_footer(text="Review it in the admin panel → Requests. Nothing has been refunded yet.")
        try:
            await log.send(embed=embed)
        except discord.HTTPException:
            pass

    def ai_allowed(self, channel_id):
        day = time.strftime("%Y-%m-%d")
        entry = ai_usage.get(channel_id)
        if not entry or entry[0] != day:
            entry = [day, 0]
        entry[1] += 1
        ai_usage[channel_id] = entry
        return entry[1] <= MAX_AI_REPLIES_PER_DAY

    # -------------------------
    # Ticket messages
    # -------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.interaction_metadata or message.content.startswith("/"):
            return

        channel = message.channel
        ticket = get_ticket(channel.id)

        if not ticket:
            # Tickets opened before this version: adopt them.
            if not getattr(channel, "name", "").startswith("ticket-") or is_staff(message.author):
                return
            save_ticket(channel.id, message.author.id, time.time())
            ticket = get_ticket(channel.id)

        # A staff member speaking = they've got it; the AI steps back.
        if message.author.id != ticket["owner_id"]:
            if is_staff(message.author) and not ticket["ai_paused"]:
                set_ticket_paused(channel.id, True)
                await channel.send("🛑 a team member has this ticket, the AI is paused. (`/resume` to turn it back on)")
            return

        if ticket["ai_paused"]:
            return

        content = message.content.strip()
        if not content:
            return

        lowered = content.lower()
        if any(word in lowered for word in SLURS):
            await channel.send("⚠️ keep it respectful or staff will step in")
            return

        lock = ticket_locks.setdefault(channel.id, asyncio.Lock())
        if lock.locked():
            return  # still answering the previous message

        async with lock:
            ctx = {
                "channel_id": channel.id,
                "user_id": message.author.id,
                "user_name": message.author.display_name,
                "channel_name": channel.name,
            }

            # Fast path: the 6-digit code from the email.
            code_match = CODE_RE.match(content)
            if code_match:
                code = code_match.group(1) + code_match.group(2)
                agent.remember(channel.id, "user", content)
                try:
                    data = await buttonland.confirm_verification(message.author.id, message.author.display_name, code)
                except ButtonlandError as error:
                    reply = f"❌ {error.message}"
                    agent.remember(channel.id, "assistant", reply)
                    return await channel.send(reply)

                orders = data.get("orders") or []
                await self.give_buyer_role(message.author, orders)
                reply = f"✅ verified ({data.get('email')})! " + (
                    "here's your latest order 👇 ask me anything about it." if orders else "i don't see any orders on that email yet."
                )
                agent.remember(channel.id, "assistant", f"{reply} [verified; {len(orders)} orders visible via get_my_orders]")
                return await channel.send(reply, embed=order_embed(orders[0]) if orders else None)

            # Fast path: they sent the email they ordered with.
            email_match = EMAIL_RE.search(content)
            if email_match and len(content) <= len(email_match.group(0)) + 40:
                agent.remember(channel.id, "user", content)
                try:
                    note = await buttonland.start_verification(message.author.id, message.author.display_name, email_match.group(0))
                    reply = f"📧 {note} paste the code here (check spam too)."
                except ButtonlandError as error:
                    reply = f"❌ {error.message}"
                agent.remember(channel.id, "assistant", reply)
                return await channel.send(reply)

            # Fast path: they asked for a person.
            if HUMAN_RE.search(content):
                agent.remember(channel.id, "user", content)
                agent.remember(channel.id, "assistant", "got it, getting a team member for you.")
                await channel.send("got it, getting a team member for you 👍")
                return await self.hand_off(channel, f"Customer asked for a person: “{content[:200]}”")

            if not agent.available:
                return await self.hand_off(channel, "AI assistant isn't configured; customer is waiting.")

            if not self.ai_allowed(channel.id):
                await channel.send("i've answered a lot here today, so i'm bringing in a team member.")
                return await self.hand_off(channel, "Daily AI reply limit reached for this ticket.")

            async with channel.typing():
                try:
                    result = await agent.respond(ctx, content)
                except Exception as error:  # API outage etc.
                    print("AI error:", repr(error))
                    await channel.send("hmm, i'm having trouble right now. getting a team member for you.")
                    return await self.hand_off(channel, "AI assistant error; customer is waiting.")

            if result.get("text"):
                await channel.send(result["text"], allowed_mentions=discord.AllowedMentions.none())

            if result.get("verified"):
                try:
                    data = await buttonland.customer(message.author.id)
                    await self.give_buyer_role(message.author, data.get("orders") or [])
                except ButtonlandError:
                    pass

            if result.get("request_filed"):
                await self.announce_request(channel, message.author, result["request_filed"])

            if result.get("handoff"):
                await self.hand_off(channel, result["handoff"])


async def setup(bot):
    await bot.add_cog(Tickets(bot))
