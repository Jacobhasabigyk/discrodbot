import discord
from discord.ext import commands
from discord import app_commands

from database import get_order_from_db

REFUND_LOG_CHANNEL = 1485943251855867944


# =========================
# 🧠 CHECK IF SHIPPED
# =========================
def can_refund(order):
    if not order:
        return False, "❌ order not found"

    tracking = order.get("tracking") or {}

    # ✅ allow if no tracking number (label only or not shipped)
    if not tracking.get("number"):
        return True, None

    return False, "❌ order already shipped"


# =========================
# 🎛 YOUR VIEW (keep yours)
# =========================
class RefundView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def ask_for_reason(self, interaction: discord.Interaction):
        await interaction.followup.send(
            "✍️ type your message to send to the user (30s timeout)",
            ephemeral=True
        )

        def check(m):
            return (
                m.author.id == interaction.user.id and
                m.channel.id == interaction.channel.id
            )

        try:
            msg = await interaction.client.wait_for("message", timeout=30, check=check)
            return msg.content
        except:
            return None

    async def dm_user(self, interaction: discord.Interaction, status: str, message: str):
        embed = interaction.message.embeds[0]
        footer = embed.footer.text or ""

        try:
            user_id = int(footer.split(":")[1].strip())
            user = await interaction.client.fetch_user(user_id)
        except:
            return False

        # safer order extraction
        order_field = next(
            (f.value for f in embed.fields if "Order" in f.name),
            "Unknown"
        )

        try:
            dm_embed = discord.Embed(
                title=f"💸 Refund {status}",
                color=0x00ff00 if status == "Approved" else 0xff0000
            )

            dm_embed.add_field(name="📦 Order", value=order_field)
            dm_embed.add_field(name="💬 Message", value=message or "No message provided")

            await user.send(embed=dm_embed)
            return True
        except:
            return False

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):

        if not interaction.message.components:
            return await interaction.response.send_message("⚠️ already handled", ephemeral=True)

        reply = await self.ask_for_reason(interaction)

        if reply is None:
            return await interaction.followup.send("❌ timed out", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = 0x00ff00
        embed.title = "✅ Refund Approved"
        embed.add_field(name="🧑‍💼 Staff Reply", value=reply, inline=False)
        embed.add_field(name="👮 Handled By", value=interaction.user.mention, inline=False)

        await interaction.message.edit(embed=embed, view=None)

        success = await self.dm_user(interaction, "Approved", reply)

        await interaction.followup.send(
            "✅ approved + user notified" if success else "⚠️ approved but DMs closed",
            ephemeral=True
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):

        if not interaction.message.components:
            return await interaction.response.send_message("⚠️ already handled", ephemeral=True)

        reply = await self.ask_for_reason(interaction)

        if reply is None:
            return await interaction.followup.send("❌ timed out", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = 0xff0000
        embed.title = "❌ Refund Denied"
        embed.add_field(name="🧑‍💼 Staff Reply", value=reply, inline=False)
        embed.add_field(name="👮 Handled By", value=interaction.user.mention, inline=False)

        await interaction.message.edit(embed=embed, view=None)

        success = await self.dm_user(interaction, "Denied", reply)

        await interaction.followup.send(
            "❌ denied + user notified" if success else "⚠️ denied but DMs closed",
            ephemeral=True
        )


# =========================
# 🎟 COG + SLASH COMMAND
# =========================
class Refunds(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="refund", description="Request a refund for an order")
    @app_commands.describe(
        order="Order number (ex: #1000)",
        reason="Why do you want a refund?"
    )
    async def refund(self, interaction: discord.Interaction, order: str, reason: str):

        await interaction.response.defer(ephemeral=True)

        # 🔒 only in tickets
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.followup.send(
                "❌ use this inside a support ticket",
                ephemeral=True
            )

        # parse order
        try:
            order_number = int(order.replace("#", ""))
        except:
            return await interaction.followup.send(
                "❌ invalid format (use #1000)",
                ephemeral=True
            )

        order_data = get_order_from_db(order_number)

        allowed, error = can_refund(order_data)

        if not allowed:
            return await interaction.followup.send(error, ephemeral=True)

        # 📤 send to staff channel
        channel = self.bot.get_channel(REFUND_LOG_CHANNEL)

        embed = discord.Embed(
            title="💸 Refund Request",
            color=0xf1c40f
        )

        embed.add_field(name="👤 User", value=interaction.user.mention, inline=False)
        embed.add_field(name="📦 Order", value=f"#{order_number}", inline=True)
        embed.add_field(name="📝 Reason", value=reason, inline=False)

        embed.set_footer(text=f"User ID: {interaction.user.id}")

        await channel.send(embed=embed, view=RefundView())

        await interaction.followup.send("✅ refund request submitted", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Refunds(bot))