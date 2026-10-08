import discord
from discord.ext import commands
from discord import app_commands
from config import OWNER_ROLE
from database import cursor, conn, update_balance
from utils.permissions import has_role_interaction
from services import buttonland
from services.buttonland import ButtonlandError
from utils.embeds import money, order_embed

# 👑 OWNER IDS ONLY (TRUE OWNERS)
OWNER_IDS = {1303076149160837121, 1267677795975303242}

# 🔐 STAFF ROLES (ONLY for lookup + track)
ALLOWED_STAFF_ROLES = [
    1484473034462199849,
    1459718191344259155,
    1468440661153026059  # mods
]

# =========================
# 🔐 PERMISSION SYSTEM
# =========================
def has_staff_permission(interaction: discord.Interaction):
    return (
        interaction.user.id in OWNER_IDS or
        any(role.id in ALLOWED_STAFF_ROLES for role in interaction.user.roles)
    )


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # 📦 ORDER DETAILS (Buttonland store)
    # =========================
    @app_commands.command(name="track", description="Staff: look up a Buttonland order by number")
    @app_commands.describe(order_number="e.g. BL-20261007-7F3A2C")
    async def track(self, interaction: discord.Interaction, order_number: str):

        if not has_staff_permission(interaction):
            return await interaction.response.send_message("❌ no permission", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            order = await buttonland.staff_order(order_number.strip())
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)

        embed = order_embed(order)
        embed.insert_field_at(0, name="👤 Customer", value=f"{order.get('customerName') or '—'}\n{order.get('email', '')}\n{order.get('shipTo', '')}"[:1000], inline=False)
        placed = order.get("placedAt", "")
        if placed:
            embed.set_footer(text=f"Placed {placed[:10]} · payment {order.get('paymentStatus')} · fulfillment {order.get('fulfillmentStatus')}")
        if order.get("attention"):
            embed.add_field(name="⚠️ Needs attention", value="\n".join(order["attention"])[:1000], inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================
    # 🔎 SEARCH ORDERS
    # =========================
    @app_commands.command(name="lookup", description="Staff: find orders by email, name, order number or tracking number")
    async def lookup(self, interaction: discord.Interaction, query: str):

        if not has_staff_permission(interaction):
            return await interaction.response.send_message("❌ no permission", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            orders = await buttonland.staff_search(query.strip())
        except ButtonlandError as error:
            return await interaction.followup.send(f"❌ {error.message}", ephemeral=True)

        orders = [o for o in orders if o.get("isPlaced", True)]
        if not orders:
            return await interaction.followup.send("❌ no orders found", ephemeral=True)

        embed = discord.Embed(title="🔎 Order search", description=f"`{query[:80]}`", color=0xE5231F)
        total = 0.0
        for o in orders[:10]:
            total += float(o.get("total") or 0)
            embed.add_field(
                name=f"📦 {o.get('orderNumber')}",
                value=(
                    f"{o.get('customerName') or '—'} · {o.get('email', '')}\n"
                    f"{money(o.get('total'))} · {o.get('status')} / {o.get('fulfillmentStatus')}"
                    f"{' · ' + o['location'] if o.get('location') else ''}"
                )[:1000],
                inline=False,
            )
        embed.set_footer(text=f"{len(orders)} order(s) · {money(total)} total · /track <order> for details")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================
    # 🌎 GIVE ALL (OWNER IDS ONLY)
    # =========================
    @app_commands.command(name="giveall")
    async def giveall(self, interaction: discord.Interaction, amount: int):

        if interaction.user.id not in OWNER_IDS:
            return await interaction.response.send_message("❌ Owner only", ephemeral=True)

        cursor.execute("SELECT user_id FROM balances")
        users = cursor.fetchall()

        for (user_id,) in users:
            cursor.execute(
                "UPDATE balances SET balance = balance + ? WHERE user_id=?",
                (amount, user_id)
            )

        conn.commit()

        await interaction.response.send_message(f"🌎 Gave ${amount} to everyone")

    # =========================
    # 💰 ADD BALANCE (OWNER IDS ONLY 🔥)
    # =========================
    @app_commands.command(name="addbalance")
    async def addbalance(self, interaction: discord.Interaction, member: discord.Member, amount: int):

        if interaction.user.id not in OWNER_IDS:
            return await interaction.response.send_message("❌ Owner only", ephemeral=True)

        new_balance = update_balance(member.id, amount)

        await interaction.response.send_message(
            f"💰 Added ${amount} → New Balance: ${new_balance}"
        )


async def setup(bot):
    await bot.add_cog(Admin(bot))
