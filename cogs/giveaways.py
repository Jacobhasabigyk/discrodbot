"""
Buttonland giveaways

/giveaway start posts a giveaway with an Enter button. When time is up the
store backend picks the winners (fair random draw) and the bot:
  - edits the giveaway post and announces the winners with a Claim prize button
  - DMs each winner their private claim link (buttonland.store/giveaway/...)
  - reminds them after 24 hours
  - tells the owner in #giveaway-logs when a link runs out after 48 hours,
    with Reroll / Give 48h more buttons, and when a prize is claimed

The winner fills in their address on the website, which creates a normal
order (free, or they pay shipping), so it lands in Orders -> Needs label.

/giveaway sendlink sends a winner their link again. /giveaway award gives a
prize to someone by hand (e.g. a winner from the old giveaway bot).

Buttons use fixed custom IDs ("gw:<action>:<id>") handled in on_interaction,
so they keep working after a restart.
"""

import asyncio
import datetime
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import HEAD_MOD_ROLE, OWNER_ROLE
from database import get_setting, set_setting
from services import buttonland
from services.buttonland import ButtonlandError

ADMIN_ROLE = 1484473034462199849
# Who can run giveaways, resend links, reroll and give more time.
GIVEAWAY_ROLES = {OWNER_ROLE, HEAD_MOD_ROLE, ADMIN_ROLE}
# Giving a prize away by hand (no giveaway): owner / admin only.
AWARD_ROLES = {OWNER_ROLE, ADMIN_ROLE}

GOLD = 0xF5B841
GREEN = 0x23A55A
GREY = 0x4E5058
ENTRY_EDIT_DELAY = 5  # seconds: entry counts on the post update at most this often

SHIPPING_TEXT = {"store": "Free shipping 🎉", "winner": "Winner pays shipping"}


def has_role(member, roles):
    if member is None:
        return False
    perms = getattr(member, "guild_permissions", None)
    if perms is not None and getattr(perms, "administrator", False):
        return True
    return any(getattr(role, "id", None) in roles for role in getattr(member, "roles", []))


def parse_duration(text):
    """'1d 12h', '30m', '2h', '1w' -> minutes (None if invalid)."""
    text = str(text or "").strip().lower().replace(" ", "")
    if not text:
        return None
    if text.isdigit():
        return int(text)  # plain number = minutes
    units = {"w": 7 * 24 * 60, "d": 24 * 60, "h": 60, "m": 1}
    total, pos = 0, 0
    for match in re.finditer(r"(\d+)(w|d|h|m)", text):
        if match.start() != pos:
            return None
        total += int(match.group(1)) * units[match.group(2)]
        pos = match.end()
    return total if pos == len(text) and total > 0 else None


def unix(value):
    """ISO date from the API -> unix seconds."""
    if not value:
        return None
    try:
        return int(datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def rel(value):
    stamp = unix(value)
    return f"<t:{stamp}:R>" if stamp else "soon"


def giveaway_embed(g, *, entries=None, ended=False, winners=None):
    count = g.get("entryCount", 0) if entries is None else entries
    embed = discord.Embed(
        title=f"🎁 {g.get('title') or g.get('productName')}",
        description=f"**Prize:** {g.get('productName')}",
        color=GREY if ended else GOLD,
    )
    if ended:
        if winners:
            embed.add_field(name="Winners", value=" ".join(f"<@{w['discordUserId']}>" for w in winners), inline=False)
        else:
            embed.add_field(name="Winners", value="Nobody entered 😢", inline=False)
        embed.add_field(name="Entries", value=str(count), inline=True)
    else:
        stamp = unix(g.get("endsAt"))
        embed.add_field(name="Ends", value=f"<t:{stamp}:R> (<t:{stamp}:f>)" if stamp else "soon", inline=True)
        embed.add_field(name="Winners", value=str(g.get("winnersCount", 1)), inline=True)
        embed.add_field(name="Entries", value=str(count), inline=True)
    embed.add_field(name="Shipping", value=SHIPPING_TEXT.get(g.get("shippingPaidBy"), "Free"), inline=True)
    if g.get("requiredRoleId") and not ended:
        embed.add_field(name="Who can enter", value=f"<@&{g['requiredRoleId']}> only", inline=True)
    embed.set_footer(text="Winners claim their prize on buttonland.store" if ended else "Press Enter to join • winners get a claim link by DM")
    return embed


def enter_view(giveaway_id):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Enter", emoji="🎉", style=discord.ButtonStyle.success, custom_id=f"gw:enter:{giveaway_id}"))
    return view


def claim_view(giveaway_id):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Claim prize", emoji="🎁", style=discord.ButtonStyle.primary, custom_id=f"gw:claim:{giveaway_id}"))
    return view


def link_view(url, label="Claim your prize"):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url))
    return view


def dm_embed(kind, dm):
    g = dm.get("giveaway") or {}
    prize = g.get("productName") or "your prize"
    shipping = "Shipping is on us." if g.get("shippingPaidBy") != "winner" else "You'll just pay shipping at checkout."
    if kind == "reminder":
        title, intro = "⏰ Don't forget your prize!", f"You still haven't claimed **{prize}**."
    elif kind == "resend":
        title, intro = "🎁 Here's your claim link", f"Here's your link to claim **{prize}** again."
    else:
        title, intro = "🎉 You won!", f"You won **{prize}** in **{g.get('title') or 'our giveaway'}**!"
    embed = discord.Embed(
        title=title,
        description=(
            f"{intro}\n\n"
            f"Press the button below and fill in where to ship it. {shipping}\n"
            f"Your link runs out {rel(dm.get('expiresAt'))}. Keep it private, it only works for you."
        ),
        color=GREEN,
    )
    embed.set_footer(text="Buttonland giveaways")
    return embed


class Giveaways(commands.Cog):
    giveaway = app_commands.Group(name="giveaway", description="Run giveaways and send prize claim links")

    def __init__(self, bot):
        self.bot = bot
        self.edit_tasks = {}
        self.entry_counts = {}
        self.entry_messages = {}
        self.prize_cache = {"at": 0, "query": None, "items": []}

    async def cog_load(self):
        self.work_loop.start()

    async def cog_unload(self):
        self.work_loop.cancel()

    # ---------------- helpers ----------------
    async def log_channel(self, guild):
        """#giveaway-logs (owner/head mod/admin only): found by saved ID, then name, else created."""
        if guild is None:
            return None
        key = f"giveaway_logs_{guild.id}"
        saved = get_setting(key)
        channel = guild.get_channel(int(saved)) if saved else None
        if channel:
            return channel
        channel = discord.utils.get(guild.text_channels, name="giveaway-logs")
        if not channel:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True),
            }
            for role_id in GIVEAWAY_ROLES:
                role = guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
            try:
                channel = await guild.create_text_channel("giveaway-logs", overwrites=overwrites, topic="Giveaway winners, claims and expired links")
            except discord.HTTPException as error:
                print(f"[giveaways] can't create #giveaway-logs: {error}")
                return None
        set_setting(key, channel.id)
        return channel

    def guild_for(self, g):
        guild_id = g.get("guildId")
        if guild_id:
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                return guild
        channel = self.bot.get_channel(int(g["channelId"])) if g.get("channelId") else None
        if channel:
            return channel.guild
        guilds = list(getattr(self.bot, "guilds", []) or [])
        if isinstance(getattr(self.bot, "guilds", None), dict):
            guilds = list(self.bot.guilds.values())
        return guilds[0] if guilds else None

    async def get_channel(self, channel_id):
        if not channel_id:
            return None
        channel = self.bot.get_channel(int(channel_id))
        if channel:
            return channel
        try:
            return await self.bot.fetch_channel(int(channel_id))
        except (discord.HTTPException, AttributeError):
            return None

    async def get_user(self, user_id):
        user = self.bot.get_user(int(user_id)) if hasattr(self.bot, "get_user") else None
        if user:
            return user
        try:
            return await self.bot.fetch_user(int(user_id))
        except (discord.HTTPException, AttributeError):
            return None

    async def prizes(self, query):
        now = time.time()
        if self.prize_cache["query"] == query and now - self.prize_cache["at"] < 20:
            return self.prize_cache["items"]
        try:
            items = await buttonland.giveaway_prizes(query)
        except ButtonlandError:
            items = []
        self.prize_cache = {"at": now, "query": query, "items": items}
        return items

    async def deny(self, interaction, text="only the owner / head mods can run giveaways."):
        await interaction.response.send_message(text, ephemeral=True)

    # ---------------- autocomplete ----------------
    async def prize_choices(self, interaction, current):
        items = await self.prizes(current or "")
        return [app_commands.Choice(name=item["name"][:100], value=item["id"]) for item in items[:25]]

    async def option_choices(self, interaction, current):
        product_id = getattr(getattr(interaction, "namespace", None), "prize", None)
        if not product_id:
            return []
        items = await self.prizes("")
        product = next((item for item in items if item["id"] == product_id), None)
        if not product:
            for item in await self.prizes(current or ""):
                if item["id"] == product_id:
                    product = item
        if not product:
            return []
        choices = [app_commands.Choice(name="Winner picks", value="choose")]
        for variant in product.get("variants", []):
            label = variant["title"] + ("" if variant.get("inStock") else " (out of stock)")
            if not current or current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label[:100], value=variant["id"]))
        return choices[:25]

    async def giveaway_choices(self, interaction, current, status=""):
        try:
            items = await buttonland.list_giveaways(status)
        except ButtonlandError:
            return []
        choices = []
        for g in items:
            if g.get("source") == "manual" and status == "active":
                continue
            label = f"{g.get('title')} · {g.get('status')}"
            if not current or current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label[:100], value=g["id"]))
        return choices[:25]

    async def active_choices(self, interaction, current):
        return await self.giveaway_choices(interaction, current, "active")

    async def any_choices(self, interaction, current):
        return await self.giveaway_choices(interaction, current, "")

    # ---------------- commands ----------------
    @giveaway.command(name="start", description="Start a giveaway with an Enter button")
    @app_commands.describe(
        prize="Product to give away",
        duration="How long, e.g. 30m, 2h, 1d, 1d12h, 1w",
        winners="How many winners (1-20)",
        shipping="Who pays shipping",
        option="Size/color (or let the winner pick)",
        title="Optional title (defaults to the product name)",
        channel="Where to post it (default: this channel)",
        required_role="Only people with this role can enter",
    )
    @app_commands.choices(shipping=[
        app_commands.Choice(name="Free for the winner", value="store"),
        app_commands.Choice(name="Winner pays shipping", value="winner"),
    ])
    async def start(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: str,
        shipping: app_commands.Choice[str],
        winners: int = 1,
        option: str = "",
        title: str = "",
        channel: discord.TextChannel = None,
        required_role: discord.Role = None,
    ):
        if not has_role(interaction.user, GIVEAWAY_ROLES):
            return await self.deny(interaction)
        minutes = parse_duration(duration)
        if not minutes:
            return await interaction.response.send_message("couldn't read that duration. try `30m`, `2h`, `1d` or `1d12h`.", ephemeral=True)
        if not 1 <= int(winners) <= 20:
            return await interaction.response.send_message("winners must be 1 to 20.", ephemeral=True)

        target = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        try:
            g = await buttonland.create_giveaway(
                productId=prize,
                variantId="" if option in ("", "choose") else option,
                winnersCount=int(winners),
                durationMinutes=minutes,
                shippingPaidBy=getattr(shipping, "value", shipping),
                title=title,
                guildId=str(interaction.guild.id) if interaction.guild else "",
                channelId=str(target.id),
                requiredRoleId=str(required_role.id) if required_role else "",
                createdByDiscordId=str(interaction.user.id),
                createdByName=str(interaction.user),
            )
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)

        try:
            message = await target.send(embed=giveaway_embed(g), view=enter_view(g["id"]))
        except discord.HTTPException:
            await buttonland.cancel_giveaway(g["id"], str(interaction.user))
            return await interaction.followup.send(f"❌ I can't post in {target.mention}. Check my permissions there.", ephemeral=True)

        await buttonland.set_giveaway_message(g["id"], message.id)
        await interaction.followup.send(f"✅ giveaway posted in {target.mention}. it ends {rel(g.get('endsAt'))}.", ephemeral=True)

    @start.autocomplete("prize")
    async def start_prize_autocomplete(self, interaction, current: str):
        return await self.prize_choices(interaction, current)

    @start.autocomplete("option")
    async def start_option_autocomplete(self, interaction, current: str):
        return await self.option_choices(interaction, current)

    @giveaway.command(name="end", description="End a running giveaway now and pick the winners")
    @app_commands.describe(giveaway="Which giveaway")
    async def end(self, interaction: discord.Interaction, giveaway: str):
        if not has_role(interaction.user, GIVEAWAY_ROLES):
            return await self.deny(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            await buttonland.end_giveaway(giveaway, str(interaction.user))
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)
        await self.run_work()
        await interaction.followup.send("✅ ended. winners are posted and DM'd.", ephemeral=True)

    @end.autocomplete("giveaway")
    async def end_autocomplete(self, interaction, current: str):
        return await self.active_choices(interaction, current)

    @giveaway.command(name="cancel", description="Cancel a running giveaway (no winners)")
    @app_commands.describe(giveaway="Which giveaway")
    async def cancel(self, interaction: discord.Interaction, giveaway: str):
        if not has_role(interaction.user, GIVEAWAY_ROLES):
            return await self.deny(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            data = await buttonland.cancel_giveaway(giveaway, str(interaction.user))
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)
        g = data.get("giveaway") or {}
        channel = await self.get_channel(g.get("channelId"))
        if channel and g.get("messageId"):
            try:
                message = await channel.fetch_message(int(g["messageId"]))
                embed = giveaway_embed(g, ended=True)
                embed.title = f"❌ {g.get('title')} (cancelled)"
                await message.edit(embed=embed, view=None)
            except (discord.HTTPException, ValueError):
                pass
        await interaction.followup.send("✅ cancelled.", ephemeral=True)

    @cancel.autocomplete("giveaway")
    async def cancel_autocomplete(self, interaction, current: str):
        return await self.active_choices(interaction, current)

    @giveaway.command(name="reroll", description="Pick a new winner instead of someone")
    @app_commands.describe(user="The winner to replace", giveaway="Which giveaway")
    async def reroll(self, interaction: discord.Interaction, user: discord.Member, giveaway: str):
        if not has_role(interaction.user, GIVEAWAY_ROLES):
            return await self.deny(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            g = await buttonland.get_giveaway(giveaway)
            winner = next((w for w in (g or {}).get("winners", []) if w["discordUserId"] == str(user.id) and w["status"] in ("pending", "expired")), None)
            if not winner:
                return await interaction.followup.send(f"{user.mention} isn't an unclaimed winner of that giveaway.", ephemeral=True)
            data = await buttonland.reroll_winner(winner["id"], str(interaction.user))
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)
        await self.run_work()
        new = data.get("winner")
        await interaction.followup.send(f"🔁 new winner: <@{new['discordUserId']}>" if new else "nobody else entered, so there's no one to reroll to.", ephemeral=True)

    @reroll.autocomplete("giveaway")
    async def reroll_autocomplete(self, interaction, current: str):
        return await self.any_choices(interaction, current)

    @giveaway.command(name="sendlink", description="DM a winner their prize claim link again (gives more time if needed)")
    @app_commands.describe(user="The winner", giveaway="Which giveaway (optional: their latest unclaimed prize)")
    async def sendlink(self, interaction: discord.Interaction, user: discord.Member, giveaway: str = ""):
        if not has_role(interaction.user, GIVEAWAY_ROLES):
            return await self.deny(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            data = await buttonland.send_giveaway_link(user.id, giveaway, str(interaction.user))
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)

        winner = data.get("winner") or {}
        sent = await self.send_dm(user.id, "resend", {"claimUrl": data["claimUrl"], "expiresAt": winner.get("claimExpiresAt"), "giveaway": data.get("giveaway")})
        if winner.get("id"):
            try:
                await buttonland.giveaway_dm_done(winner["id"], sent, "")
            except ButtonlandError:
                pass
        if sent:
            await interaction.followup.send(f"✅ sent {user.mention} their claim link. it works until {rel(winner.get('claimExpiresAt'))}.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"⚠️ {user.mention} has DMs closed. send them this link yourself (it only works for them):\n{data['claimUrl']}",
                ephemeral=True,
            )

    @sendlink.autocomplete("giveaway")
    async def sendlink_autocomplete(self, interaction, current: str):
        return await self.any_choices(interaction, current)

    @giveaway.command(name="award", description="Give a prize to someone by hand and DM them a claim link")
    @app_commands.describe(
        user="Who won",
        prize="Product they won",
        shipping="Who pays shipping",
        option="Size/color (or let them pick)",
        title="What they won it in (e.g. 'October giveaway')",
    )
    @app_commands.choices(shipping=[
        app_commands.Choice(name="Free for the winner", value="store"),
        app_commands.Choice(name="Winner pays shipping", value="winner"),
    ])
    async def award(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        prize: str,
        shipping: app_commands.Choice[str],
        option: str = "",
        title: str = "",
    ):
        if not has_role(interaction.user, AWARD_ROLES):
            return await self.deny(interaction, "only the owner can give prizes by hand.")
        await interaction.response.defer(ephemeral=True)
        try:
            data = await buttonland.award_prize(
                discordUserId=str(user.id),
                discordName=str(user),
                productId=prize,
                variantId="" if option in ("", "choose") else option,
                shippingPaidBy=getattr(shipping, "value", shipping),
                title=title,
                guildId=str(interaction.guild.id) if interaction.guild else "",
                createdByDiscordId=str(interaction.user.id),
                createdByName=str(interaction.user),
            )
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)

        winner = data.get("winner") or {}
        sent = await self.send_dm(user.id, "won", {"claimUrl": data["claimUrl"], "expiresAt": winner.get("claimExpiresAt"), "giveaway": data.get("giveaway")})
        if winner.get("id"):
            try:
                await buttonland.giveaway_dm_done(winner["id"], sent, "")
            except ButtonlandError:
                pass
        logs = await self.log_channel(interaction.guild)
        if logs:
            await logs.send(f"🎁 {interaction.user.mention} gave **{(data.get('giveaway') or {}).get('productName')}** to {user.mention} ({SHIPPING_TEXT.get(getattr(shipping, 'value', shipping), '')}).")
        if sent:
            await interaction.followup.send(f"✅ {user.mention} got a DM with their claim link (48 hours).", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ {user.mention} has DMs closed. send them this link yourself:\n{data['claimUrl']}", ephemeral=True)

    @award.autocomplete("prize")
    async def award_prize_autocomplete(self, interaction, current: str):
        return await self.prize_choices(interaction, current)

    @award.autocomplete("option")
    async def award_option_autocomplete(self, interaction, current: str):
        return await self.option_choices(interaction, current)

    @giveaway.command(name="list", description="Recent giveaways and who still has to claim")
    async def list_(self, interaction: discord.Interaction):
        if not has_role(interaction.user, GIVEAWAY_ROLES):
            return await self.deny(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            items = await buttonland.list_giveaways()
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)
        if not items:
            return await interaction.followup.send("no giveaways yet. start one with `/giveaway start`.", ephemeral=True)
        icons = {"pending": "⏳", "expired": "⌛", "claimed": "✅", "rerolled": "🔁", "cancelled": "✖️"}
        lines = []
        for g in items[:10]:
            when = f"ends {rel(g.get('endsAt'))}" if g.get("status") == "active" else g.get("status")
            lines.append(f"**{g.get('title')}** · {g.get('productName')} · {when} · {g.get('entryCount', 0)} entries")
            for w in g.get("winners", []):
                extra = f" · {w['orderNumber']}" if w.get("orderNumber") else ""
                lines.append(f"  {icons.get(w['status'], '•')} <@{w['discordUserId']}> {w['status']}{extra}")
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)

    # ---------------- buttons ----------------
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("gw:"):
            return
        parts = custom_id.split(":")
        action = parts[1] if len(parts) > 1 else ""
        target = parts[2] if len(parts) > 2 else ""
        if not re.fullmatch(r"[a-f0-9]{24}", target):
            return

        if action == "enter":
            return await self.handle_enter(interaction, target)
        if action == "leave":
            try:
                data = await buttonland.leave_giveaway(target, interaction.user.id)
            except ButtonlandError as error:
                return await interaction.response.send_message(f"❌ {error.message}", ephemeral=True)
            self.queue_entry_edit(target, data.get("entries"), None)
            return await interaction.response.send_message("you left the giveaway.", ephemeral=True)
        if action == "claim":
            return await self.handle_claim(interaction, target)
        if action in ("reroll", "extend"):
            if not has_role(interaction.user, GIVEAWAY_ROLES):
                return await self.deny(interaction, "only the owner / head mods can do that.")
            await interaction.response.defer()
            try:
                if action == "reroll":
                    data = await buttonland.reroll_winner(target, str(interaction.user))
                    new = data.get("winner")
                    note = f"🔁 rerolled by {interaction.user.mention}: new winner <@{new['discordUserId']}>" if new else f"🔁 {interaction.user.mention} tried to reroll, but nobody else entered."
                else:
                    data = await buttonland.extend_winner(target, str(interaction.user))
                    note = f"⏳ {interaction.user.mention} gave <@{data['winner']['discordUserId']}> 48 more hours and re-sent their link."
            except ButtonlandError as error:
                return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)
            if interaction.message:
                try:
                    await interaction.message.edit(content=f"{interaction.message.content}\n{note}", view=None)
                except discord.HTTPException:
                    pass
            await self.run_work()

    async def handle_enter(self, interaction, giveaway_id):
        try:
            g = await buttonland.get_giveaway(giveaway_id)
        except ButtonlandError as error:
            return await interaction.response.send_message(f"❌ {error.message}", ephemeral=True)
        if not g or g.get("status") != "active":
            return await interaction.response.send_message("this giveaway has ended.", ephemeral=True)
        role_id = g.get("requiredRoleId")
        if role_id and not has_role(interaction.user, {int(role_id)}):
            return await interaction.response.send_message(f"only <@&{role_id}> can enter this one.", ephemeral=True)
        try:
            data = await buttonland.enter_giveaway(giveaway_id, interaction.user.id, str(interaction.user))
        except ButtonlandError as error:
            return await interaction.response.send_message(f"❌ {error.message}", ephemeral=True)
        if data.get("already"):
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="Leave giveaway", style=discord.ButtonStyle.danger, custom_id=f"gw:leave:{giveaway_id}"))
            return await interaction.response.send_message("you're already in 👍 good luck!", view=view, ephemeral=True)
        self.queue_entry_edit(giveaway_id, data.get("entries"), interaction.message, g)
        await interaction.response.send_message("🎉 you're in! if you win, I'll DM you a link to claim your prize. keep your DMs open.", ephemeral=True)

    async def handle_claim(self, interaction, giveaway_id):
        try:
            data = await buttonland.giveaway_claim_link(giveaway_id, interaction.user.id)
        except ButtonlandError as error:
            return await interaction.response.send_message(f"❌ {error.message}", ephemeral=True)
        status = data.get("status")
        if status == "pending":
            return await interaction.response.send_message(
                f"🎁 here's your private claim link. fill in your address and it ships! it runs out {rel(data.get('expiresAt'))}.",
                view=link_view(data["claimUrl"]),
                ephemeral=True,
            )
        if status == "claimed":
            order = f" (order {data['orderNumber']})" if data.get("orderNumber") else ""
            return await interaction.response.send_message(f"✅ you already claimed it{order}. we'll email you tracking once it ships.", ephemeral=True)
        if status == "expired":
            return await interaction.response.send_message("⌛ your 48 hours to claim ran out. message a mod if you think that's a mistake.", ephemeral=True)
        await interaction.response.send_message("this prize isn't yours, sorry! better luck next time 🍀", ephemeral=True)

    def queue_entry_edit(self, giveaway_id, entries, message, g=None):
        """Update the entry count on the post, at most once every few seconds."""
        if entries is not None:
            self.entry_counts[giveaway_id] = entries
        if message is not None:
            self.entry_messages[giveaway_id] = (message, g)
        if giveaway_id in self.edit_tasks or giveaway_id not in self.entry_messages:
            return

        async def later():
            try:
                await asyncio.sleep(ENTRY_EDIT_DELAY)
                message_ref, giveaway = self.entry_messages.get(giveaway_id, (None, None))
                if message_ref is not None and giveaway is not None:
                    await message_ref.edit(embed=giveaway_embed(giveaway, entries=self.entry_counts.get(giveaway_id, 0)))
            except discord.HTTPException:
                pass
            finally:
                self.edit_tasks.pop(giveaway_id, None)

        self.edit_tasks[giveaway_id] = asyncio.create_task(later())

    # ---------------- DMs ----------------
    async def send_dm(self, user_id, kind, dm):
        user = await self.get_user(user_id)
        if not user:
            return False
        try:
            await user.send(embed=dm_embed(kind, dm), view=link_view(dm["claimUrl"]))
            return True
        except discord.HTTPException:
            return False

    # ---------------- background ----------------
    @tasks.loop(seconds=30)
    async def work_loop(self):
        await self.run_work()

    @work_loop.before_loop
    async def before_work(self):
        await self.bot.wait_until_ready()

    async def run_work(self):
        if not buttonland.is_configured():
            return
        try:
            work = await buttonland.giveaway_work()
        except ButtonlandError as error:
            print(f"[giveaways] work failed: {error.message}")
            return
        for item in work.get("announcements", []):
            await self.safe(self.announce, item)
        for item in work.get("rerolls", []):
            await self.safe(self.announce_reroll, item)
        for item in work.get("dms", []):
            await self.safe(self.deliver_dm, item)
        for item in work.get("expired", []):
            await self.safe(self.report_expired, item)
        for item in work.get("claims", []):
            await self.safe(self.report_claim, item)

    async def safe(self, fn, item):
        try:
            await fn(item)
        except Exception as error:  # one bad item never stops the rest
            print(f"[giveaways] {fn.__name__} failed: {error!r}")

    async def announce(self, item):
        g, winners = item["giveaway"], item.get("winners", [])
        channel = await self.get_channel(g.get("channelId"))
        message_id = None
        if channel:
            if g.get("messageId"):
                try:
                    original = await channel.fetch_message(int(g["messageId"]))
                    await original.edit(embed=giveaway_embed(g, ended=True, winners=winners), view=claim_view(g["id"]))
                except (discord.HTTPException, ValueError):
                    pass
            if winners:
                mentions = " ".join(f"<@{w['discordUserId']}>" for w in winners)
                text = (
                    f"🎉 congrats {mentions}! you won **{g.get('productName')}**.\n"
                    f"check your DMs for your claim link, or press **Claim prize** below. you have 48 hours to claim."
                )
            else:
                text = f"nobody entered **{g.get('title')}**, so there's no winner this time."
            message = await channel.send(text, view=claim_view(g["id"]) if winners else None)
            message_id = message.id
        await buttonland.giveaway_announced(g["id"], message_id)

        logs = await self.log_channel(self.guild_for(g))
        if logs:
            names = ", ".join(f"<@{w['discordUserId']}>" for w in winners) or "nobody"
            await logs.send(f"🏁 **{g.get('title')}** ended with {g.get('entryCount', 0)} entries. winners: {names}")

    async def announce_reroll(self, item):
        g = item["giveaway"]
        channel = await self.get_channel(g.get("channelId"))
        if channel:
            replaced = f" (replacing <@{item['replacedDiscordUserId']}>)" if item.get("replacedDiscordUserId") else ""
            await channel.send(
                f"🔁 new winner for **{g.get('title')}**: <@{item['discordUserId']}>{replaced}! check your DMs or press **Claim prize**.",
                view=claim_view(g["id"]),
            )
        await buttonland.giveaway_reroll_announced(item["winnerId"])

    async def deliver_dm(self, item):
        sent = await self.send_dm(item["discordUserId"], item.get("kind") or "won", item)
        await buttonland.giveaway_dm_done(item["winnerId"], sent, item.get("kind") or "")
        if not sent and item.get("kind") != "reminder":
            g = item.get("giveaway") or {}
            channel = await self.get_channel(g.get("channelId"))
            if channel:
                await channel.send(
                    f"<@{item['discordUserId']}> I couldn't DM you (DMs closed). press **Claim prize** on the giveaway to get your link.",
                    view=claim_view(g["id"]),
                )
            else:
                logs = await self.log_channel(self.guild_for(g))
                if logs:
                    await logs.send(f"⚠️ couldn't DM <@{item['discordUserId']}> their claim link (DMs closed). use `/giveaway sendlink` and send it yourself.")

    async def report_expired(self, item):
        g = item["giveaway"]
        logs = await self.log_channel(self.guild_for(g))
        if logs:
            view = discord.ui.View(timeout=None)
            if item.get("canReroll"):
                view.add_item(discord.ui.Button(label="Reroll", emoji="🔁", style=discord.ButtonStyle.primary, custom_id=f"gw:reroll:{item['winnerId']}"))
            view.add_item(discord.ui.Button(label="Give 48h more", emoji="⏳", style=discord.ButtonStyle.secondary, custom_id=f"gw:extend:{item['winnerId']}"))
            await logs.send(f"⌛ <@{item['discordUserId']}> didn't claim **{g.get('productName')}** ({g.get('title')}) within 48 hours.", view=view)
        await buttonland.giveaway_expired_noted(item["winnerId"])

    async def report_claim(self, item):
        g = item["giveaway"]
        logs = await self.log_channel(self.guild_for(g))
        if logs:
            await logs.send(
                f"✅ <@{item['discordUserId']}> claimed **{g.get('productName')}** ({g.get('title')}). "
                f"order **{item.get('orderNumber')}** is in Orders → Needs label."
            )
        await buttonland.giveaway_claim_noted(item["winnerId"])


async def setup(bot):
    await bot.add_cog(Giveaways(bot))
