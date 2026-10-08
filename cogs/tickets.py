"""
Buttonland Tickets v2

Flow: panel (pick a topic) -> short form -> private thread with a live
status card -> AI assistant first, staff when needed -> close with an HTML
transcript (log channel + DM) and a 1-5 star rating.

Staff work from #ticket-queue: one card per open ticket, edited in place.
Inactive tickets (customer silent) get a warning after 24h and close after
48h. Claimed tickets and tickets waiting on staff never auto-close.

All buttons use fixed custom IDs ("tk:<action>:<ticket>") handled in
on_interaction, so they keep working after the bot restarts.
"""

import asyncio
import io
import json
import re
import statistics
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    BUYER_ROLE,
    HEAD_MOD_ROLE,
    MOD_ROLE,
    OWNER_ROLE,
    SUPPORT_CHANNEL_ID,
    SUPPORT_ROLE,
)
from database import (
    blacklist_add,
    blacklist_get,
    blacklist_remove,
    claim_ticket,
    close_ticket_row,
    create_ticket,
    get_setting,
    get_ticket,
    get_ticket_by_thread,
    open_tickets,
    open_tickets_for,
    set_setting,
    tickets_since,
    update_ticket,
)
from services import buttonland
from services.buttonland import ButtonlandError
from services.support_ai import SupportAgent
from services.transcript import build_transcript
from utils.embeds import BRAND_RED, money, order_embed

# ------------------------------------------------------
# Settings
# ------------------------------------------------------
ADMIN_ROLE = 1484473034462199849
STAFF_ROLES = {OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE, SUPPORT_ROLE, ADMIN_ROLE}
BLACKLIST_ROLES = {OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE, ADMIN_ROLE}  # mods and up
PANEL_ROLES = {OWNER_ROLE, ADMIN_ROLE}

# Where refund/cancel requests filed by the AI are announced (existing channel).
REQUEST_LOG_CHANNEL = 1485943251855867944

WARN_AFTER = 24 * 3600   # customer silent this long -> "still need help?"
CLOSE_AFTER = 24 * 3600  # ...and this long after the warning -> closed
MAX_AI_REPLIES_PER_DAY = 40

COLOR = {
    "ai": 0x23A55A,
    "needs_staff": 0xF0B232,
    "claimed": 0x5865F2,
    "waiting": 0x949BA4,
    "closed": 0x4E5058,
}

TOPICS = {
    "order": {
        "label": "Order help",
        "emoji": "📦",
        "hint": "Tracking, status, changes",
        "fields": [
            ("Email", "Email you ordered with", True, discord.TextStyle.short, "you@example.com", 254),
            ("Order number", "Order number (if you have it)", False, discord.TextStyle.short, "BL-20261007-…", 40),
            ("What's going on", "What's going on?", True, discord.TextStyle.paragraph, "e.g. tracking hasn't moved since Monday", 1000),
        ],
    },
    "product": {
        "label": "Product question",
        "emoji": "🛒",
        "hint": "Fit, stock, which one to get",
        "fields": [
            ("Product", "Which product?", True, discord.TextStyle.short, "e.g. aluminum grip", 100),
            ("Needs to fit", "What does it need to fit? (optional)", False, discord.TextStyle.short, "e.g. TM Hi-Capa 5.1", 100),
            ("Question", "Your question", True, discord.TextStyle.paragraph, "Ask anything", 1000),
        ],
    },
    "returns": {
        "label": "Returns & refunds",
        "emoji": "↩️",
        "hint": "14 days, unused items",
        "fields": [
            ("Email", "Email you ordered with", True, discord.TextStyle.short, "you@example.com", 254),
            ("Order number", "Order number", True, discord.TextStyle.short, "BL-20261007-…", 40),
            ("Reason", "What happened?", True, discord.TextStyle.paragraph, "e.g. wrong size, arrived damaged", 1000),
        ],
    },
    "other": {
        "label": "Something else",
        "emoji": "💬",
        "hint": "Anything we didn't list",
        "fields": [
            ("Subject", "Subject", True, discord.TextStyle.short, "Short summary", 100),
            ("Details", "Details", True, discord.TextStyle.paragraph, "Tell us more", 1000),
        ],
    },
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
CODE_RE = re.compile(r"^\s*(\d{3})\s?-?\s?(\d{3})\s*$")
HUMAN_RE = re.compile(
    r"\b(?:human|real person|live agent|support agent|representative)\b|\b(?:talk|speak) to (?:a |an |the )?(?:person|someone|staff|owner|mod)\b",
    re.I,
)
SLURS = ["retard", "nigger", "nigga"]


# ------------------------------------------------------
# Helpers
# ------------------------------------------------------
def has_role(member, roles):
    if not isinstance(member, discord.Member):
        return False
    if member.guild and member.guild.owner_id == member.id:
        return True
    if member.guild_permissions.administrator:
        return True
    return any(role.id in roles for role in member.roles)


def is_staff(member):
    return has_role(member, STAFF_ROLES)


def num(ticket):
    return f"#{int(ticket['id']):04d}"


def ts(value):
    return f"<t:{int(value)}:R>" if value else "—"


def form_of(ticket):
    try:
        return json.loads(ticket.get("form") or "{}")
    except ValueError:
        return {}


def mask_email(email):
    user, _, domain = (email or "").partition("@")
    return f"{user[:2]}{'•' * max(len(user) - 2, 1)}@{domain}" if domain else ""


def status_of(ticket):
    if ticket["status"] == "closed":
        return "closed"
    if ticket["status"] == "claimed" and (ticket.get("last_reply_at") or 0) > (ticket.get("last_customer_at") or 0):
        return "waiting"
    return ticket["status"]


def status_line(ticket):
    state = status_of(ticket)
    if state == "ai":
        return "🟢 **AI assistant is helping**" + (" · ⏳ waiting for a reply" if ticket.get("warned_at") else "")
    if state == "needs_staff":
        return f"🟠 **Waiting for staff**" + (f"\n> {ticket['handoff_reason']}" if ticket.get("handoff_reason") else "")
    if state == "claimed":
        return f"🔵 **Claimed by <@{ticket['claimed_by']}>**"
    if state == "waiting":
        return f"⏳ **Waiting on customer** · claimed by <@{ticket['claimed_by']}>"
    return "⚫ **Closed**"


def plain_view(*items):
    view = discord.ui.View(timeout=None)
    for item in items:
        view.add_item(item)
    return view


# ------------------------------------------------------
# Embeds and buttons
# ------------------------------------------------------
def panel_embed(status_text):
    embed = discord.Embed(
        title="How can we help?",
        description=(
            "Pick a topic and we'll open a **private ticket** only you and our team can see.\n"
            "Our AI assistant answers right away, and a person joins whenever you need one."
        ),
        color=BRAND_RED,
    )
    for topic in TOPICS.values():
        embed.add_field(name=f"{topic['emoji']} {topic['label']}", value=topic["hint"], inline=True)
    embed.set_footer(text=status_text)
    return embed


def panel_view():
    styles = {"order": discord.ButtonStyle.danger}
    return plain_view(*[
        discord.ui.Button(
            label=topic["label"],
            emoji=topic["emoji"],
            style=styles.get(key, discord.ButtonStyle.secondary),
            custom_id=f"tk:open:{key}",
        )
        for key, topic in TOPICS.items()
    ])


def card_embed(ticket):
    topic = TOPICS.get(ticket["topic"], TOPICS["other"])
    form = form_of(ticket)
    embed = discord.Embed(
        title=f"Ticket {num(ticket)} · {topic['emoji']} {topic['label']}",
        description=status_line(ticket),
        color=COLOR.get(status_of(ticket), BRAND_RED),
    )
    embed.add_field(name="Customer", value=f"<@{ticket['owner_id']}>", inline=True)
    if form.get("Email"):
        embed.add_field(name="Email", value=mask_email(form["Email"]), inline=True)
    if form.get("Order number"):
        embed.add_field(name="Order", value=form["Order number"][:40], inline=True)
    if form.get("Product"):
        embed.add_field(name="Product", value=form["Product"][:100], inline=True)
    if form.get("Subject"):
        embed.add_field(name="Subject", value=form["Subject"][:100], inline=True)
    embed.add_field(name="Opened", value=ts(ticket["created_at"]), inline=True)
    embed.set_footer(text="Buttonland Support · say \"human\" any time to get a team member")
    return embed


def card_view(ticket):
    if ticket["status"] == "closed":
        return None
    tid = ticket["id"]
    items = []
    if not ticket.get("claimed_by"):
        items.append(discord.ui.Button(label="Claim", emoji="✋", style=discord.ButtonStyle.primary, custom_id=f"tk:claim:{tid}"))
    items.append(discord.ui.Button(label="Add person", emoji="➕", style=discord.ButtonStyle.secondary, custom_id=f"tk:add:{tid}"))
    items.append(discord.ui.Button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id=f"tk:close:{tid}"))
    return plain_view(*items)


def queue_embed(ticket, owner_name):
    topic = TOPICS.get(ticket["topic"], TOPICS["other"])
    state = status_of(ticket)
    form = form_of(ticket)
    summary = form.get("What's going on") or form.get("Question") or form.get("Reason") or form.get("Details") or ""
    lines = [status_line(ticket)]
    if summary:
        lines.append(f"“{summary[:140]}”")
    lines.append(f"Opened {ts(ticket['created_at'])}")
    embed = discord.Embed(
        title=f"{num(ticket)} · {topic['emoji']} {topic['label']} · {owner_name}",
        description="\n".join(lines),
        color=COLOR.get(state, BRAND_RED),
    )
    return embed


def queue_view(ticket, jump_url):
    items = []
    if not ticket.get("claimed_by"):
        items.append(discord.ui.Button(label="Claim", emoji="✋", style=discord.ButtonStyle.primary, custom_id=f"tk:claim:{ticket['id']}"))
    if jump_url:
        items.append(discord.ui.Button(label="Jump", style=discord.ButtonStyle.link, url=jump_url))
    return plain_view(*items) if items else None


# ------------------------------------------------------
# Modals (answered right away, so they don't need to survive restarts)
# ------------------------------------------------------
class TicketForm(discord.ui.Modal):
    def __init__(self, cog, topic_key):
        topic = TOPICS[topic_key]
        super().__init__(title=f"{topic['emoji']} {topic['label']}"[:45], timeout=900)
        self.cog = cog
        self.topic_key = topic_key
        self.inputs = []
        for key, label, required, style, placeholder, max_length in topic["fields"]:
            field = discord.ui.TextInput(label=label[:45], required=required, style=style, placeholder=placeholder[:100], max_length=max_length)
            self.inputs.append((key, field))
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        answers = {key: str(field.value or "").strip() for key, field in self.inputs}
        await self.cog.open_ticket(interaction, self.topic_key, answers)


class CloseForm(discord.ui.Modal, title="Close this ticket"):
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, max_length=200, placeholder="e.g. Solved, order delivered")

    def __init__(self, cog, ticket_id):
        super().__init__(timeout=600)
        self.cog = cog
        self.ticket_id = ticket_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔒 Closing and saving the transcript…")
        await self.cog.close_ticket(self.ticket_id, interaction.user, str(self.reason.value or "").strip() or "Closed")


class RatingComment(discord.ui.Modal, title="Anything to add?"):
    comment = discord.ui.TextInput(label="Comment (optional)", required=False, style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, cog, ticket_id, stars):
        super().__init__(timeout=600)
        self.cog = cog
        self.ticket_id = ticket_id
        self.stars = stars

    async def on_submit(self, interaction: discord.Interaction):
        text = str(self.comment.value or "").strip()
        if text:
            update_ticket(self.ticket_id, rating_comment=text)
        await interaction.response.send_message("thanks for the feedback! 💛")
        if interaction.message:
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                pass
        await self.cog.log_rating(self.ticket_id, self.stars, text)


class AddPersonView(discord.ui.View):
    def __init__(self, cog, ticket_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.ticket_id = ticket_id

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Choose someone to add", min_values=1, max_values=1)
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        member = select.values[0]
        message = await self.cog.add_person(self.ticket_id, member, interaction.user)
        await interaction.response.edit_message(content=message, view=None)


# ------------------------------------------------------
# The cog
# ------------------------------------------------------
class Tickets(commands.Cog):
    ticket = app_commands.Group(name="ticket", description="Ticket tools")

    def __init__(self, bot):
        self.bot = bot
        self.agent = SupportAgent()
        self.locks = {}
        self.ai_usage = {}
        self.opening = set()

    async def cog_load(self):
        self.housekeeping.start()

    async def cog_unload(self):
        self.housekeeping.cancel()
        await buttonland.close()

    # ---------------- channels ----------------
    def support_channel(self):
        return self.bot.get_channel(SUPPORT_CHANNEL_ID)

    async def staff_channel(self, guild, kind):
        """#ticket-queue / #ticket-logs: found by saved ID, then by name, else created (staff-only)."""
        key = f"{kind}_channel_{guild.id}"
        saved = get_setting(key)
        channel = guild.get_channel(int(saved)) if saved else None
        if channel:
            return channel

        name = "ticket-queue" if kind == "queue" else "ticket-logs"
        channel = discord.utils.get(guild.text_channels, name=name)

        if not channel:
            support = self.support_channel()
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True),
            }
            for role_id in STAFF_ROLES:
                role = guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
            try:
                channel = await guild.create_text_channel(
                    name,
                    category=support.category if support else None,
                    overwrites=overwrites,
                    topic="Open tickets, updated live" if kind == "queue" else "Ticket transcripts and ratings",
                )
            except discord.HTTPException as error:
                print(f"[tickets] Can't create #{name}: {error}")
                return None

        set_setting(key, channel.id)
        return channel

    async def fetch_thread(self, ticket):
        if not ticket.get("thread_id"):
            return None
        thread = self.bot.get_channel(int(ticket["thread_id"]))
        if thread:
            return thread
        try:
            return await self.bot.fetch_channel(int(ticket["thread_id"]))
        except discord.HTTPException:
            return None

    # ---------------- panel ----------------
    def panel_status(self):
        open_now = len(open_tickets())
        recent = [t for t in tickets_since(time.time() - 7 * 86400) if t.get("first_staff_reply_at")]
        waits = [t["first_staff_reply_at"] - t["created_at"] for t in recent]
        if waits:
            minutes = statistics.median(waits) / 60
            reply = f"~{int(minutes)} min" if minutes < 90 else f"~{round(minutes / 60)} hr"
            staff = f"Staff usually reply in {reply}"
        else:
            staff = "Staff reply as soon as they can"
        ai = "Assistant online" if self.agent.available else "Assistant offline"
        return f"● {ai} · {staff} · {open_now} ticket{'s' if open_now != 1 else ''} open"

    async def ensure_panel(self, force=False):
        channel = self.support_channel()
        if not channel:
            print("[tickets] Support channel not found (SUPPORT_CHANNEL_ID)")
            return None

        saved = get_setting(f"panel_message_{channel.id}")
        if saved and not force:
            try:
                message = await channel.fetch_message(int(saved))
                return message
            except discord.HTTPException:
                pass

        # Remove the bot's older panels (v1 green button or a previous v2).
        async for old in channel.history(limit=30):
            if old.author == self.bot.user and old.components:
                try:
                    await old.delete()
                except discord.HTTPException:
                    pass

        message = await channel.send(embed=panel_embed(self.panel_status()), view=panel_view())
        set_setting(f"panel_message_{channel.id}", message.id)
        set_setting(f"panel_status_{channel.id}", self.panel_status())
        return message

    async def refresh_panel(self):
        channel = self.support_channel()
        if not channel:
            return
        saved = get_setting(f"panel_message_{channel.id}")
        text = self.panel_status()
        if not saved or get_setting(f"panel_status_{channel.id}") == text:
            return
        try:
            await channel.get_partial_message(int(saved)).edit(embed=panel_embed(text), view=panel_view())
            set_setting(f"panel_status_{channel.id}", text)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_ready(self):
        if getattr(self.bot, "_ticket_panel_checked", False):
            return
        self.bot._ticket_panel_checked = True
        try:
            await self.ensure_panel()
            support = self.support_channel()
            if support:
                me = support.guild.me
                perms = support.permissions_for(me)
                missing = [
                    name for name, ok in [
                        ("Create Private Threads", perms.create_private_threads),
                        ("Manage Threads", perms.manage_threads),
                        ("Send Messages in Threads", perms.send_messages_in_threads),
                        ("Embed Links", perms.embed_links),
                        ("Attach Files", perms.attach_files),
                        ("Read Message History", perms.read_message_history),
                    ] if not ok
                ]
                if missing:
                    print(f"[tickets] ⚠️ Bot is missing permissions in #{support.name}: {', '.join(missing)}")
                await self.staff_channel(support.guild, "queue")
                await self.staff_channel(support.guild, "logs")
        except Exception as error:
            print("[tickets] Panel setup failed:", repr(error))

    # ---------------- ticket card + queue ----------------
    async def refresh(self, ticket_id):
        ticket = get_ticket(ticket_id)
        if not ticket:
            return
        thread = await self.fetch_thread(ticket)

        if thread and ticket.get("card_message_id"):
            try:
                await thread.get_partial_message(int(ticket["card_message_id"])).edit(embed=card_embed(ticket), view=card_view(ticket))
            except discord.HTTPException:
                pass

        guild = self.bot.get_guild(int(ticket["guild_id"]))
        if not guild:
            return
        queue = await self.staff_channel(guild, "queue")
        if not queue:
            return

        if ticket["status"] == "closed":
            for field in ("queue_message_id", "ping_message_id"):
                if ticket.get(field):
                    try:
                        await queue.get_partial_message(int(ticket[field])).delete()
                    except discord.HTTPException:
                        pass
            return

        owner = guild.get_member(int(ticket["owner_id"]))
        owner_name = owner.display_name if owner else ticket["owner_id"]
        embed = queue_embed(ticket, owner_name)
        view = queue_view(ticket, thread.jump_url if thread else None)

        if ticket.get("queue_message_id"):
            try:
                await queue.get_partial_message(int(ticket["queue_message_id"])).edit(embed=embed, view=view)
                return
            except discord.HTTPException:
                pass
        try:
            message = await queue.send(embed=embed, view=view)
            update_ticket(ticket_id, queue_message_id=str(message.id))
        except discord.HTTPException:
            pass

    async def clear_ping(self, ticket):
        if not ticket.get("ping_message_id"):
            return
        guild = self.bot.get_guild(int(ticket["guild_id"]))
        queue = await self.staff_channel(guild, "queue") if guild else None
        if queue:
            try:
                await queue.get_partial_message(int(ticket["ping_message_id"])).delete()
            except discord.HTTPException:
                pass
        update_ticket(ticket["id"], ping_message_id=None)

    # ---------------- opening ----------------
    async def open_ticket(self, interaction, topic_key, answers):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = interaction.user
        guild = interaction.guild
        support = self.support_channel()

        if not support or not guild:
            return await interaction.followup.send("tickets aren't set up right now, please ping a mod.", ephemeral=True)

        if blacklist_get(user.id):
            return await interaction.followup.send("you can't open tickets right now. if you think that's a mistake, message a moderator.", ephemeral=True)

        for existing in open_tickets_for(user.id):
            thread = await self.fetch_thread(existing)
            if thread:
                return await interaction.followup.send(f"you already have a ticket open: {thread.mention}", ephemeral=True)
            close_ticket_row(existing["id"], self.bot.user.id, "Thread was deleted", time.time())

        if user.id in self.opening:
            return await interaction.followup.send("one sec, your ticket is being created…", ephemeral=True)
        self.opening.add(user.id)

        try:
            now = time.time()
            topic = TOPICS[topic_key]
            ticket_id = create_ticket(guild.id, user.id, topic_key, json.dumps(answers), now)
            ticket = get_ticket(ticket_id)

            safe_name = re.sub(r"[^\w.-]", "", user.display_name)[:40] or str(user.id)
            try:
                thread = await support.create_thread(
                    name=f"{topic['emoji']} {int(ticket_id):04d} · {safe_name}"[:100],
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=10080,
                    reason=f"Ticket {num(ticket)} for {user}",
                )
                await thread.add_user(user)
            except discord.HTTPException as error:
                close_ticket_row(ticket_id, self.bot.user.id, "Could not create thread", time.time())
                print("[tickets] create_thread failed:", repr(error))
                return await interaction.followup.send("couldn't open a ticket (missing permissions). a mod has been told.", ephemeral=True)

            update_ticket(ticket_id, thread_id=str(thread.id))
            ticket = get_ticket(ticket_id)

            card = await thread.send(content=f"{user.mention}", embed=card_embed(ticket), view=card_view(ticket))
            update_ticket(ticket_id, card_message_id=str(card.id))
            try:
                await card.pin()
            except discord.HTTPException:
                pass

            await interaction.followup.send(f"✅ your ticket is open: {thread.mention}", ephemeral=True)
            await self.refresh(ticket_id)
            asyncio.create_task(self.greet(ticket_id, thread, user, answers))
        finally:
            self.opening.discard(user.id)

    async def greet(self, ticket_id, thread, user, answers):
        """First reply: start email verification from the form, then let the AI answer the question."""
        ticket = get_ticket(ticket_id)
        topic = TOPICS.get(ticket["topic"], TOPICS["other"])
        ctx = self.ctx(ticket, thread, user)

        if answers.get("Email") and EMAIL_RE.fullmatch(answers["Email"]):
            try:
                await buttonland.start_verification(user.id, user.display_name, answers["Email"])
                self.agent.remember(thread.id, "user", f"(ticket form) my email is {answers['Email']}")
                self.agent.remember(thread.id, "assistant", "[a 6-digit verification code was emailed to them]")
            except ButtonlandError as error:
                print("[tickets] verify start failed:", error.message)

        question = "\n".join(f"{k}: {v}" for k, v in answers.items() if v and k != "Email")
        if not self.agent.available:
            await thread.send(f"thanks {user.mention}! a team member will be with you soon.")
            return await self.hand_off(ticket_id, "AI assistant is offline")

        async with thread.typing():
            try:
                result = await self.agent.respond(ctx, f"(opened a {topic['label']} ticket)\n{question}")
            except Exception as error:
                print("[tickets] AI error:", repr(error))
                await thread.send("thanks! a team member will be with you shortly.")
                return await self.hand_off(ticket_id, "AI assistant error on the first message")
        await self.after_ai(ticket_id, thread, user, result)

    def ctx(self, ticket, thread, user):
        topic = TOPICS.get(ticket["topic"], TOPICS["other"])
        return {
            "channel_id": thread.id,
            "user_id": user.id,
            "user_name": user.display_name,
            "channel_name": thread.name,
            "topic_label": topic["label"],
            "form": form_of(ticket),
        }

    async def after_ai(self, ticket_id, thread, member, result):
        if result.get("text"):
            await thread.send(result["text"], allowed_mentions=discord.AllowedMentions.none())
            update_ticket(ticket_id, last_reply_at=time.time())

        if result.get("verified"):
            try:
                data = await buttonland.customer(member.id)
                await self.give_buyer_role(member, data.get("orders") or [])
            except ButtonlandError:
                pass

        if result.get("request_filed"):
            await self.announce_request(thread, member, result["request_filed"])

        if result.get("handoff"):
            await self.hand_off(ticket_id, result["handoff"])

    # ---------------- staff actions ----------------
    async def hand_off(self, ticket_id, reason):
        ticket = get_ticket(ticket_id)
        if not ticket or ticket["status"] == "closed":
            return
        if ticket.get("claimed_by"):
            update_ticket(ticket_id, ai_paused=True)
            return await self.refresh(ticket_id)

        update_ticket(ticket_id, status="needs_staff", ai_paused=True, handoff_reason=reason[:300])
        ticket = get_ticket(ticket_id)
        guild = self.bot.get_guild(int(ticket["guild_id"]))
        queue = await self.staff_channel(guild, "queue") if guild else None
        thread = await self.fetch_thread(ticket)

        if queue and not ticket.get("ping_message_id"):
            role = guild.get_role(SUPPORT_ROLE)
            try:
                ping = await queue.send(
                    f"🙋 {role.mention if role else 'Staff'} ticket **{num(ticket)}** needs a person: {reason[:200]}"
                    + (f"\n{thread.jump_url}" if thread else ""),
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
                update_ticket(ticket_id, ping_message_id=str(ping.id))
            except discord.HTTPException:
                pass
        await self.refresh(ticket_id)

    async def claim(self, ticket_id, member, thread=None):
        if not claim_ticket(ticket_id, member.id):
            ticket = get_ticket(ticket_id)
            who = f"<@{ticket['claimed_by']}>" if ticket and ticket.get("claimed_by") else "someone"
            return False, f"already claimed by {who}."
        ticket = get_ticket(ticket_id)
        thread = thread or await self.fetch_thread(ticket)
        if thread:
            try:
                await thread.add_user(member)
            except discord.HTTPException:
                pass
            await thread.send(f"✋ {member.mention} has this ticket now. The AI assistant is paused.")
        await self.clear_ping(ticket)
        await self.refresh(ticket_id)
        return True, f"you claimed {num(ticket)}" + (f": {thread.jump_url}" if thread else ".")

    async def add_person(self, ticket_id, member, by):
        ticket = get_ticket(ticket_id)
        thread = await self.fetch_thread(ticket) if ticket else None
        if not thread or ticket["status"] == "closed":
            return "that ticket isn't open."
        if blacklist_get(member.id):
            return f"{member.mention} is blacklisted from tickets."
        try:
            await thread.add_user(member)
        except discord.HTTPException:
            return "couldn't add them (missing permissions?)."
        await thread.send(f"➕ {member.mention} was added by {by.mention}.")
        return f"added {member.mention}."

    async def close_ticket(self, ticket_id, closer, reason):
        now = time.time()
        if not close_ticket_row(ticket_id, getattr(closer, "id", self.bot.user.id), reason, now):
            return
        ticket = get_ticket(ticket_id)
        thread = await self.fetch_thread(ticket)
        guild = self.bot.get_guild(int(ticket["guild_id"]))
        topic = TOPICS.get(ticket["topic"], TOPICS["other"])

        messages = []
        if thread:
            try:
                async for m in thread.history(limit=2000, oldest_first=True):
                    messages.append({
                        "author_id": m.author.id,
                        "author_name": m.author.display_name,
                        "created_at": m.created_at.timestamp(),
                        "content": m.clean_content,
                        "embeds": [
                            {"title": e.title, "description": e.description, "fields": [(f.name, f.value) for f in e.fields]}
                            for e in m.embeds
                        ],
                        "attachments": [{"filename": a.filename, "url": a.url} for a in m.attachments],
                    })
            except discord.HTTPException:
                pass

        def name_of(user_id):
            if not user_id:
                return ""
            member = guild.get_member(int(user_id)) if guild else None
            return member.display_name if member else str(user_id)

        staff_ids = {str(m["author_id"]) for m in messages if guild and is_staff(guild.get_member(int(m["author_id"])))}
        html_text = build_transcript(
            ticket, messages,
            topic_label=topic["label"],
            owner_name=name_of(ticket["owner_id"]),
            claimed_name=name_of(ticket.get("claimed_by")),
            closed_name=name_of(ticket.get("closed_by")),
            staff_ids=staff_ids,
            bot_id=self.bot.user.id,
        )
        filename = f"ticket-{int(ticket_id):04d}-transcript.html"
        data = html_text.encode("utf-8")
        duration = max(int((now - ticket["created_at"]) / 60), 0)

        logs = await self.staff_channel(guild, "logs") if guild else None
        if logs:
            embed = discord.Embed(title=f"🔒 Ticket {num(ticket)} closed · {topic['emoji']} {topic['label']}", color=COLOR["closed"])
            embed.add_field(name="Customer", value=f"<@{ticket['owner_id']}>")
            embed.add_field(name="Claimed by", value=f"<@{ticket['claimed_by']}>" if ticket.get("claimed_by") else "AI only")
            embed.add_field(name="Closed by", value=f"<@{ticket['closed_by']}>")
            embed.add_field(name="Open for", value=f"{duration // 60}h {duration % 60}m" if duration >= 60 else f"{duration}m")
            embed.add_field(name="Reason", value=reason[:200] or "—", inline=False)
            try:
                await logs.send(embed=embed, file=discord.File(io.BytesIO(data), filename=filename))
            except discord.HTTPException:
                pass

        owner = guild.get_member(int(ticket["owner_id"])) if guild else None
        if owner:
            dm = discord.Embed(
                title=f"Ticket {num(ticket)} is closed",
                description=f"Thanks for reaching out, {owner.display_name}. Here's a copy of the conversation.\n**How did we do?**",
                color=BRAND_RED,
            )
            stars = plain_view(*[
                discord.ui.Button(label=f"{n}", emoji="⭐", style=discord.ButtonStyle.success if n == 5 else discord.ButtonStyle.secondary, custom_id=f"tk:rate:{ticket_id}:{n}")
                for n in range(1, 6)
            ])
            try:
                await owner.send(embed=dm, view=stars, file=discord.File(io.BytesIO(data), filename=filename))
            except discord.HTTPException:
                pass  # DMs closed

        if thread:
            try:
                await thread.send(f"🔒 Closed by {closer.mention if hasattr(closer, 'mention') else 'the bot'} · {reason[:200]}\nA transcript was saved{' and sent to you by DM' if owner else ''}.")
            except discord.HTTPException:
                pass
        await self.refresh(ticket_id)
        if thread:
            try:
                await thread.edit(archived=True, locked=True)
            except discord.HTTPException:
                pass
        self.agent.forget(int(ticket["thread_id"] or 0))

    async def log_rating(self, ticket_id, stars, comment=""):
        ticket = get_ticket(ticket_id)
        guild = self.bot.get_guild(int(ticket["guild_id"])) if ticket else None
        logs = await self.staff_channel(guild, "logs") if guild else None
        if not logs:
            return
        embed = discord.Embed(
            title=f"{'⭐' * stars}{'☆' * (5 - stars)}  Ticket {num(ticket)}",
            description=(f"“{comment[:900]}”\n" if comment else "") + f"from <@{ticket['owner_id']}>",
            color=0x23A55A if stars >= 4 else 0xF0B232 if stars == 3 else 0xDA373C,
        )
        try:
            await logs.send(embed=embed)
        except discord.HTTPException:
            pass

    # ---------------- buttons ----------------
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")

        # Buttons left over from the first ticket system.
        if custom_id == "open_ticket":
            channel = self.support_channel()
            return await interaction.response.send_message(f"this panel is old, use the new one in {channel.mention if channel else '#support'} 👍", ephemeral=True)
        if custom_id == "close_ticket":
            channel = interaction.channel
            allowed = is_staff(interaction.user) or (channel and channel.overwrites_for(interaction.user).view_channel)
            if not allowed or not getattr(channel, "name", "").startswith("ticket-"):
                return await interaction.response.send_message("only the ticket owner or staff can close this.", ephemeral=True)
            await interaction.response.send_message("🔒 closing in 5 seconds…")
            await asyncio.sleep(5)
            try:
                await channel.delete(reason=f"Old ticket closed by {interaction.user}")
            except discord.HTTPException:
                pass
            return

        if not custom_id.startswith("tk:"):
            return

        parts = custom_id.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if action == "open":
            topic = parts[2] if len(parts) > 2 else ""
            if topic not in TOPICS:
                return await interaction.response.send_message("unknown topic.", ephemeral=True)
            if blacklist_get(interaction.user.id):
                return await interaction.response.send_message("you can't open tickets right now. if you think that's a mistake, message a moderator.", ephemeral=True)
            for existing in open_tickets_for(interaction.user.id):
                thread = await self.fetch_thread(existing)
                if thread:
                    return await interaction.response.send_message(f"you already have a ticket open: {thread.mention}", ephemeral=True)
            return await interaction.response.send_modal(TicketForm(self, topic))

        try:
            ticket_id = int(parts[2])
        except (IndexError, ValueError):
            return
        ticket = get_ticket(ticket_id)
        if not ticket:
            return await interaction.response.send_message("that ticket doesn't exist anymore.", ephemeral=True)

        if action == "claim":
            if not is_staff(interaction.user):
                return await interaction.response.send_message("only staff can claim tickets.", ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            ok, message = await self.claim(ticket_id, interaction.user)
            return await interaction.followup.send(message, ephemeral=True)

        if action == "add":
            if not is_staff(interaction.user):
                return await interaction.response.send_message("only staff can add people.", ephemeral=True)
            return await interaction.response.send_message("who should be added?", view=AddPersonView(self, ticket_id), ephemeral=True)

        if action == "close":
            if str(interaction.user.id) != ticket["owner_id"] and not is_staff(interaction.user):
                return await interaction.response.send_message("only the customer or staff can close this.", ephemeral=True)
            if ticket["status"] == "closed":
                return await interaction.response.send_message("already closed.", ephemeral=True)
            return await interaction.response.send_modal(CloseForm(self, ticket_id))

        if action == "keep":
            if str(interaction.user.id) != ticket["owner_id"] and not is_staff(interaction.user):
                return await interaction.response.send_message("only the customer can do that.", ephemeral=True)
            update_ticket(ticket_id, warned_at=None, last_customer_at=time.time())
            await interaction.response.edit_message(content="👍 keeping this ticket open.", embed=None, view=None)
            return await self.refresh(ticket_id)

        if action == "rate":
            if str(interaction.user.id) != ticket["owner_id"]:
                return await interaction.response.send_message("only the customer can rate this ticket.", ephemeral=True)
            if ticket.get("rating"):
                return await interaction.response.send_message(f"you already rated this {ticket['rating']}⭐, thanks!")
            try:
                stars = max(1, min(5, int(parts[3])))
            except (IndexError, ValueError):
                return
            update_ticket(ticket_id, rating=stars)
            await interaction.response.send_modal(RatingComment(self, ticket_id, stars))
            return

    # ---------------- messages in tickets ----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not isinstance(message.channel, discord.Thread):
            return
        ticket = get_ticket_by_thread(message.channel.id)
        if not ticket or ticket["status"] == "closed":
            return

        now = time.time()
        thread = message.channel
        author = message.author
        is_owner = str(author.id) == ticket["owner_id"]

        if not is_owner and is_staff(author):
            fields = {"last_reply_at": now, "staff_replied": True}
            if not ticket.get("first_staff_reply_at"):
                fields["first_staff_reply_at"] = now
            update_ticket(ticket["id"], **fields)
            if not ticket.get("claimed_by"):
                await self.claim(ticket["id"], author, thread)
            else:
                await self.refresh(ticket["id"])
            return

        # Customer (or someone staff added) speaking.
        update_ticket(ticket["id"], last_customer_at=now, warned_at=None)
        if ticket.get("warned_at") or ticket["status"] == "claimed":
            await self.refresh(ticket["id"])

        if not is_owner or ticket["ai_paused"]:
            return

        content = message.content.strip()
        if not content:
            return
        if any(word in content.lower() for word in SLURS):
            return await thread.send("⚠️ keep it respectful or staff will step in")

        lock = self.locks.setdefault(thread.id, asyncio.Lock())
        if lock.locked():
            return

        async with lock:
            ctx = self.ctx(ticket, thread, author)

            code = CODE_RE.match(content)
            if code:
                self.agent.remember(thread.id, "user", content)
                try:
                    data = await buttonland.confirm_verification(author.id, author.display_name, code.group(1) + code.group(2))
                except ButtonlandError as error:
                    reply = f"❌ {error.message}"
                    self.agent.remember(thread.id, "assistant", reply)
                    return await thread.send(reply)
                orders = data.get("orders") or []
                await self.give_buyer_role(author, orders)
                reply = f"✅ verified ({data.get('email')})! " + ("here's your latest order 👇 ask me anything about it." if orders else "i don't see any orders on that email yet.")
                self.agent.remember(thread.id, "assistant", f"{reply} [verified; {len(orders)} orders visible via get_my_orders]")
                update_ticket(ticket["id"], last_reply_at=time.time())
                return await thread.send(reply, embed=order_embed(orders[0]) if orders else None)

            email = EMAIL_RE.search(content)
            if email and len(content) <= len(email.group(0)) + 40:
                self.agent.remember(thread.id, "user", content)
                try:
                    note = await buttonland.start_verification(author.id, author.display_name, email.group(0))
                    reply = f"📧 {note} paste the code here (check spam too)."
                except ButtonlandError as error:
                    reply = f"❌ {error.message}"
                self.agent.remember(thread.id, "assistant", reply)
                update_ticket(ticket["id"], last_reply_at=time.time())
                return await thread.send(reply)

            if HUMAN_RE.search(content):
                self.agent.remember(thread.id, "user", content)
                self.agent.remember(thread.id, "assistant", "got it, getting a team member for you.")
                await thread.send("got it, getting a team member for you 👍")
                return await self.hand_off(ticket["id"], f"Customer asked for a person: “{content[:150]}”")

            if not self.agent.available:
                return await self.hand_off(ticket["id"], "AI assistant isn't configured; customer is waiting")

            day = time.strftime("%Y-%m-%d")
            used = self.ai_usage.get(thread.id)
            used = [day, used[1] + 1] if used and used[0] == day else [day, 1]
            self.ai_usage[thread.id] = used
            if used[1] > MAX_AI_REPLIES_PER_DAY:
                await thread.send("i've answered a lot here today, so i'm bringing in a team member.")
                return await self.hand_off(ticket["id"], "Daily AI reply limit reached")

            async with thread.typing():
                try:
                    result = await self.agent.respond(ctx, content)
                except Exception as error:
                    print("[tickets] AI error:", repr(error))
                    await thread.send("hmm, i'm having trouble right now. getting a team member for you.")
                    return await self.hand_off(ticket["id"], "AI assistant error")
            await self.after_ai(ticket["id"], thread, author, result)

    # ---------------- background ----------------
    @tasks.loop(minutes=10)
    async def housekeeping(self):
        now = time.time()
        for ticket in open_tickets():
            try:
                thread = await self.fetch_thread(ticket)
                if not thread:
                    close_ticket_row(ticket["id"], self.bot.user.id, "Thread was deleted", now)
                    await self.refresh(ticket["id"])
                    continue

                # Only tickets where we're waiting on the customer.
                if ticket["status"] != "ai" or ticket["ai_paused"]:
                    continue
                last_customer = ticket.get("last_customer_at") or ticket["created_at"]
                waiting_on_customer = (ticket.get("last_reply_at") or 0) >= last_customer

                if ticket.get("warned_at"):
                    if now - ticket["warned_at"] >= CLOSE_AFTER:
                        await self.close_ticket(ticket["id"], self.bot.user, "No reply for 48 hours")
                elif waiting_on_customer and now - last_customer >= WARN_AFTER:
                    keep = plain_view(discord.ui.Button(label="Keep it open", emoji="👍", style=discord.ButtonStyle.success, custom_id=f"tk:keep:{ticket['id']}"))
                    await thread.send(
                        f"<@{ticket['owner_id']}> still need help? this ticket closes automatically in 24 hours if there's no reply.",
                        view=keep,
                    )
                    update_ticket(ticket["id"], warned_at=now)
                    await self.refresh(ticket["id"])
            except Exception as error:
                print(f"[tickets] housekeeping error on {ticket.get('id')}:", repr(error))
        await self.refresh_panel()

    @housekeeping.before_loop
    async def before_housekeeping(self):
        await self.bot.wait_until_ready()

    # ---------------- misc helpers ----------------
    async def give_buyer_role(self, member, orders):
        if not orders or not isinstance(member, discord.Member):
            return
        role = member.guild.get_role(BUYER_ROLE)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="Verified Buttonland customer")
            except discord.HTTPException:
                pass

    async def announce_request(self, thread, member, request):
        log = self.bot.get_channel(REQUEST_LOG_CHANNEL)
        if not log or not request:
            return
        kind = "Cancellation" if request.get("type") == "cancel" else "Refund"
        embed = discord.Embed(title=f"📝 {kind} request from Discord", color=0xF5A623)
        embed.add_field(name="Order", value=request.get("orderNumber", "?"))
        if request.get("amount"):
            embed.add_field(name="Amount (suggested)", value=money(request["amount"]))
        embed.add_field(name="Customer", value=member.mention)
        embed.add_field(name="Ticket", value=thread.mention)
        embed.set_footer(text="Review it in the admin panel → Requests. Nothing has been refunded yet.")
        try:
            await log.send(embed=embed)
        except discord.HTTPException:
            pass

    def current_ticket(self, interaction):
        if isinstance(interaction.channel, discord.Thread):
            ticket = get_ticket_by_thread(interaction.channel.id)
            if ticket and ticket["status"] != "closed":
                return ticket
        return None

    # ---------------- /ticket commands ----------------
    @ticket.command(name="add", description="Staff: add someone to this ticket")
    async def cmd_add(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ staff only", ephemeral=True)
        ticket = self.current_ticket(interaction)
        if not ticket:
            return await interaction.response.send_message("use this inside an open ticket.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.add_person(ticket["id"], member, interaction.user), ephemeral=True)

    @ticket.command(name="remove", description="Staff: remove someone from this ticket")
    async def cmd_remove(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ staff only", ephemeral=True)
        ticket = self.current_ticket(interaction)
        if not ticket:
            return await interaction.response.send_message("use this inside an open ticket.", ephemeral=True)
        if str(member.id) == ticket["owner_id"]:
            return await interaction.response.send_message("that's the customer who opened it. close the ticket instead.", ephemeral=True)
        try:
            await interaction.channel.remove_user(member)
        except discord.HTTPException:
            return await interaction.response.send_message("couldn't remove them.", ephemeral=True)
        await interaction.response.send_message(f"➖ {member.mention} was removed by {interaction.user.mention}.")

    @ticket.command(name="blacklist", description="Mods: stop someone from opening tickets")
    async def cmd_blacklist(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if not has_role(interaction.user, BLACKLIST_ROLES):
            return await interaction.response.send_message("❌ mods and up only", ephemeral=True)
        if is_staff(member):
            return await interaction.response.send_message("you can't blacklist staff.", ephemeral=True)
        blacklist_add(member.id, reason[:300], interaction.user.id, time.time())
        await interaction.response.send_message(f"🚫 {member.mention} can no longer open tickets. Reason: {reason[:300]}", ephemeral=True)
        logs = await self.staff_channel(interaction.guild, "logs")
        if logs:
            await logs.send(f"🚫 {interaction.user.mention} blacklisted {member.mention} from tickets: {reason[:300]}")

    @ticket.command(name="unblacklist", description="Mods: let someone open tickets again")
    async def cmd_unblacklist(self, interaction: discord.Interaction, member: discord.Member):
        if not has_role(interaction.user, BLACKLIST_ROLES):
            return await interaction.response.send_message("❌ mods and up only", ephemeral=True)
        removed = blacklist_remove(member.id)
        await interaction.response.send_message(f"✅ {member.mention} can open tickets again." if removed else "they weren't blacklisted.", ephemeral=True)
        if removed:
            logs = await self.staff_channel(interaction.guild, "logs")
            if logs:
                await logs.send(f"✅ {interaction.user.mention} removed {member.mention} from the ticket blacklist.")

    @ticket.command(name="ai", description="Staff: turn the AI assistant on or off in this ticket")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    async def cmd_ai(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ staff only", ephemeral=True)
        ticket = self.current_ticket(interaction)
        if not ticket:
            return await interaction.response.send_message("use this inside an open ticket.", ephemeral=True)
        if state.value == "on":
            fields = {"ai_paused": False}
            if ticket["status"] == "needs_staff":
                fields.update(status="ai", handoff_reason=None)
            update_ticket(ticket["id"], **fields)
            await self.clear_ping(get_ticket(ticket["id"]))
            await interaction.response.send_message("🤖 AI assistant is back on in this ticket.")
        else:
            update_ticket(ticket["id"], ai_paused=True)
            await interaction.response.send_message("🛑 AI assistant paused in this ticket.")
        await self.refresh(ticket["id"])

    @ticket.command(name="stats", description="Staff: ticket numbers for the last 7 or 30 days")
    @app_commands.choices(days=[app_commands.Choice(name="7 days", value=7), app_commands.Choice(name="30 days", value=30)])
    async def cmd_stats(self, interaction: discord.Interaction, days: app_commands.Choice[int] = None):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ staff only", ephemeral=True)
        span = days.value if days else 7
        rows = tickets_since(time.time() - span * 86400)
        closed = [t for t in rows if t["status"] == "closed"]
        ai_only = [t for t in closed if not t["staff_replied"] and not t.get("claimed_by") and not t.get("handoff_reason")]
        waits = [t["first_staff_reply_at"] - t["created_at"] for t in rows if t.get("first_staff_reply_at")]
        rated = [t["rating"] for t in rows if t.get("rating")]
        by_topic = {key: sum(1 for t in rows if t["topic"] == key) for key in TOPICS}

        def fmt_minutes(seconds):
            minutes = seconds / 60
            return f"{int(minutes)} min" if minutes < 90 else f"{minutes / 60:.1f} hr"

        embed = discord.Embed(title=f"🎟 Tickets · last {span} days", color=BRAND_RED)
        embed.add_field(name="Opened", value=str(len(rows)))
        embed.add_field(name="Open now", value=str(len(open_tickets(interaction.guild.id))))
        embed.add_field(name="Solved by AI alone", value=f"{round(100 * len(ai_only) / len(closed))}%" if closed else "—")
        embed.add_field(name="First staff reply (median)", value=fmt_minutes(statistics.median(waits)) if waits else "—")
        embed.add_field(name="Rating", value=f"{sum(rated) / len(rated):.1f} ⭐ ({len(rated)})" if rated else "—")
        embed.add_field(name="By topic", value="\n".join(f"{TOPICS[k]['emoji']} {TOPICS[k]['label']}: {v}" for k, v in by_topic.items()), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket.command(name="panel", description="Owner: post (or re-post) the ticket panel in #support")
    async def cmd_panel(self, interaction: discord.Interaction):
        if not has_role(interaction.user, PANEL_ROLES):
            return await interaction.response.send_message("❌ owner only", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        message = await self.ensure_panel(force=True)
        await interaction.followup.send(f"✅ panel posted: {message.jump_url}" if message else "❌ couldn't find the support channel.", ephemeral=True)

    # ---------------- customer commands ----------------
    @app_commands.command(name="myorders", description="See your Buttonland orders (after verifying your email in a ticket)")
    async def myorders(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            data = await buttonland.customer(interaction.user.id)
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)
        if not data.get("linked"):
            return await interaction.followup.send("you haven't verified an email yet. open an Order help ticket and enter the email you ordered with 👍", ephemeral=True)
        orders = data.get("orders") or []
        if not orders:
            return await interaction.followup.send(f"no orders found for {data.get('email')}.", ephemeral=True)
        await interaction.followup.send(content=f"orders for {data.get('email')}:", embeds=[order_embed(o) for o in orders[:5]], ephemeral=True)

    @app_commands.command(name="unlink", description="Remove the email linked to your Discord account")
    async def unlink(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await buttonland.unlink(interaction.user.id)
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)
        await interaction.followup.send("done, your email is no longer linked.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tickets(bot))
