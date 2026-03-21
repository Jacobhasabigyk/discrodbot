import discord
from discord.ext import commands
from discord import app_commands
from config import OWNER_ROLE
from database import cursor, conn, update_balance
from utils.permissions import has_role_interaction

OWNER_IDS = {1303076149160837121, 1267677795975303242}

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # 📦 TRACK ORDER
    # =========================
    @app_commands.command(name="track", description="Track an order (owner only)")
    async def track(self, interaction: discord.Interaction, order_number: int):

        if interaction.user.id not in OWNER_IDS:
            return await interaction.response.send_message("❌ no permission", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            from services.shopify import sync_orders, get_tracking_info

            orders = sync_orders()
            order = next((o for o in orders if o.get("order_number") == order_number), None)

            if not order:
                return await interaction.followup.send("❌ order not found", ephemeral=True)

            tracking = get_tracking_info(order)

            embed = discord.Embed(title=f"📦 Order #{order_number}", color=0x00ff99)
            embed.add_field(name="📧 Email", value=order.get("email", "N/A"), inline=False)
            embed.add_field(name="💰 Total", value=order.get("total_price", "N/A"), inline=True)
            embed.add_field(name="📦 Status", value=order.get("fulfillment_status", "N/A"), inline=True)
            embed.add_field(name="🕒 Created", value=order.get("created_at", "N/A"), inline=False)

            items = order.get("line_items", [])
            if items:
                embed.add_field(
                    name="🛒 Items",
                    value="\n".join([f"{i.get('quantity')}x {i.get('title')}" for i in items])[:1000],
                    inline=False
                )

            if tracking:
                embed.add_field(
                    name="🚚 Tracking",
                    value=f"{tracking.get('company')}\n{tracking.get('number')}\n{tracking.get('url')}",
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print("Track error:", e)
            await interaction.followup.send("❌ error fetching order", ephemeral=True)

    # =========================
    # 📧 LOOKUP EMAIL (FIXED)
    # =========================
    @app_commands.command(name="lookup", description="Lookup orders by email (owner only)")
    async def lookup(self, interaction: discord.Interaction, email: str):

        if interaction.user.id not in OWNER_IDS:
            return await interaction.response.send_message("❌ no permission", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            from services.shopify import sync_orders

            orders = sync_orders()

            user_orders = [
                o for o in orders
                if o.get("email", "").lower() == email.lower()
            ]

            if not user_orders:
                return await interaction.followup.send("❌ no orders found", ephemeral=True)

            first_order = user_orders[0]
            shipping = first_order.get("shipping_address") or {}

            name = shipping.get("name", "N/A")
            address = f"{shipping.get('address1', '')}\n{shipping.get('city', '')}, {shipping.get('province', '')} {shipping.get('zip', '')}\n{shipping.get('country', '')}"

            embed = discord.Embed(
                title=f"📧 Customer Lookup",
                description=f"**{email}**",
                color=0x00ff99
            )

            embed.add_field(name="👤 Name", value=name, inline=False)
            embed.add_field(name="🏠 Address", value=address or "N/A", inline=False)

            total = 0

            for o in user_orders[:10]:
                price = float(o.get("total_price", 0))
                total += price

                embed.add_field(
                    name=f"📦 Order #{o.get('order_number')}",
                    value=f"💰 ${price}\n📦 {o.get('fulfillment_status', 'unknown')}",
                    inline=False
                )

            embed.set_footer(
                text=f"{len(user_orders)} orders | ${round(total, 2)} total spent"
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print("Lookup error:", e)
            await interaction.followup.send("❌ error fetching orders", ephemeral=True)

    # =========================
    # 🌎 GIVE ALL
    # =========================
    @app_commands.command(name="giveall")
    async def giveall(self, interaction: discord.Interaction, amount: int):

        if not has_role_interaction(interaction, [OWNER_ROLE]):
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
    # 💰 ADD BALANCE
    # =========================
    @app_commands.command(name="addbalance")
    async def addbalance(self, interaction: discord.Interaction, member: discord.Member, amount: int):

        if not has_role_interaction(interaction, [OWNER_ROLE]):
            return await interaction.response.send_message("❌ Owner only", ephemeral=True)

        new_balance = update_balance(member.id, amount)

        await interaction.response.send_message(
            f"💰 Added ${amount} → New Balance: ${new_balance}"
        )

async def setup(bot):
    await bot.add_cog(Admin(bot))