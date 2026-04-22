import discord
from discord.ext import commands
from discord import app_commands

from database import get_order_from_db

REFUND_LOG_CHANNEL = 1485943251855867944
CUSTOM_LOG_CHANNEL = 1485943251855867944

OWNER_IDS = [
    1267677795975303242,
    1303076149160837121,
    1331462779894501450
]

OWNER_ROLE = 1459718191344259155

custom_orders = {}


# =========================
# 🔐 OWNER CHECK
# =========================
def is_owner(interaction):
    return (
        interaction.user.id in OWNER_IDS
        or interaction.user == interaction.guild.owner
        or any(role.id == OWNER_ROLE for role in interaction.user.roles)
    )


# =========================
# 📦 CUSTOM PACKAGE VIEW
# =========================
class CustomPackageView(discord.ui.View):
    def __init__(self, order_id):
        super().__init__(timeout=None)
        self.order_id = order_id

    def get_data(self):
        return custom_orders.get(self.order_id)

    async def update(self, interaction, status, color):
        data = self.get_data()
        if not data:
            return await interaction.response.send_message("❌ order not found", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = color
        embed.set_field_at(6, name="📊 Status", value=status, inline=True)

        data["status"] = status

        await interaction.message.edit(embed=embed, view=None)

    @discord.ui.button(label="✅ Packaged", style=discord.ButtonStyle.green)
    async def packaged(self, interaction, button):
        await self.update(interaction, "📦 Packaged", 0x2ecc71)
        await interaction.response.send_message("marked packaged", ephemeral=True)

    @discord.ui.button(label="🚚 Shipped", style=discord.ButtonStyle.blurple)
    async def shipped(self, interaction, button):
        await self.update(interaction, "🚚 Shipped", 0x3498db)
        await interaction.response.send_message("marked shipped", ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction, button):
        await self.update(interaction, "❌ Cancelled", 0xe74c3c)
        await interaction.response.send_message("cancelled", ephemeral=True)


# =========================
# 📩 CONFIRM VIEW
# =========================
class ConfirmPackageView(discord.ui.View):
    def __init__(self, data, bot):
        super().__init__(timeout=300)
        self.data = data
        self.bot = bot

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction, button):

        order_id = str(len(custom_orders) + 1)

        custom_orders[order_id] = {
            **self.data,
            "status": "⏳ Pending"
        }

        embed = discord.Embed(title="📦 Custom Package", color=0x9b59b6)

        embed.add_field(name="👤 Customer", value=f"<@{self.data['user']}>", inline=False)
        embed.add_field(name="📦 Item", value=self.data["item"], inline=True)
        embed.add_field(name="📝 Notes", value=self.data["notes"], inline=False)

        embed.add_field(name="📛 Name", value=self.data["name"], inline=True)
        embed.add_field(name="🏠 Address", value=self.data["address"], inline=False)
        embed.add_field(name="🌆 City", value=f"{self.data['city']}, {self.data['state']} {self.data['zip']}", inline=False)

        embed.add_field(name="📊 Status", value="⏳ Pending", inline=True)
        embed.set_footer(text=f"Order ID: {order_id}")

        channel = self.bot.get_channel(CUSTOM_LOG_CHANNEL)
        await channel.send(embed=embed, view=CustomPackageView(order_id))

        await interaction.response.edit_message(content="✅ confirmed!", view=None)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="❌ cancelled", view=None)


# =========================
# 💸 REFUND VIEW
# =========================
class RefundView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def ask_for_reason(self, interaction):
        await interaction.followup.send("✍️ type your reply (30s)", ephemeral=True)

        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

        try:
            msg = await interaction.client.wait_for("message", timeout=30, check=check)
            return msg.content
        except:
            return None

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="refund_approve")
    async def approve(self, interaction, button):
        reply = await self.ask_for_reason(interaction)
        if not reply:
            return await interaction.followup.send("❌ timeout", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = 0x00ff00
        embed.title = "✅ Refund Approved"
        embed.add_field(name="🧑‍💼 Staff Reply", value=reply, inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("✅ done", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="refund_deny")
    async def deny(self, interaction, button):
        reply = await self.ask_for_reason(interaction)
        if not reply:
            return await interaction.followup.send("❌ timeout", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = 0xff0000
        embed.title = "❌ Refund Denied"
        embed.add_field(name="🧑‍💼 Staff Reply", value=reply, inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("❌ done", ephemeral=True)


# =========================
# 🎟 COG
# =========================
class Refunds(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="custompackage", description="Create custom package")
    async def custompackage(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        item: str,
        notes: str,
        name: str,
        address: str,
        city: str,
        state: str,
        zip: str
    ):
        if not is_owner(interaction):
            return await interaction.response.send_message("❌ owner only", ephemeral=True)

        embed = discord.Embed(
            title="📦 Confirm Shipping Info",
            description="Please confirm before we ship",
            color=0x5865F2
        )

        embed.add_field(name="📦 Item", value=item, inline=True)
        embed.add_field(name="📝 Notes", value=notes, inline=False)
        embed.add_field(name="📛 Name", value=name, inline=True)
        embed.add_field(name="🏠 Address", value=address, inline=False)
        embed.add_field(name="🌆 City", value=f"{city}, {state} {zip}", inline=False)

        try:
            await user.send(
                embed=embed,
                view=ConfirmPackageView({
                    "user": user.id,
                    "item": item,
                    "notes": notes,
                    "name": name,
                    "address": address,
                    "city": city,
                    "state": state,
                    "zip": zip
                }, self.bot)
            )

            await interaction.response.send_message(
                f"📩 confirmation sent to {user.mention}",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ user has DMs closed",
                ephemeral=True
            )

    @app_commands.command(name="customlist", description="View custom orders")
    async def customlist(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            return await interaction.response.send_message("❌ owner only", ephemeral=True)

        if not custom_orders:
            return await interaction.response.send_message("no orders", ephemeral=True)

        msg = ""
        for oid, data in custom_orders.items():
            msg += f"**#{oid}** - {data['item']} ({data['status']})\n"

        await interaction.response.send_message(msg, ephemeral=True)


# =========================
# 🔌 SETUP
# =========================
async def setup(bot):
    await bot.add_cog(Refunds(bot))
    bot.add_view(RefundView())