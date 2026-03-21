import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta

from config import OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE
from database import cursor, conn
from utils.permissions import has_role_interaction

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="warn")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):

        if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        cursor.execute(
            "INSERT INTO warnings (user_id, reason) VALUES (?, ?)",
            (str(member.id), reason)
        )
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id=?", (str(member.id),))
        warn_count = cursor.fetchone()[0]

        punishment = "None"

        try:
            if warn_count == 1:
                await member.timeout(timedelta(minutes=10))
                punishment = "10 min timeout"
            elif warn_count == 2:
                await member.timeout(timedelta(minutes=30))
                punishment = "30 min timeout"
            elif warn_count == 3:
                await member.timeout(timedelta(hours=2))
                punishment = "2h timeout"
            elif warn_count == 4:
                await member.timeout(timedelta(hours=24))
                punishment = "24h timeout"
            elif warn_count >= 5:
                await member.ban(reason="5 warnings reached")
                punishment = "Ban"
        except:
            punishment = "Failed punishment"

        embed = discord.Embed(title="⚖️ User Warned", color=0xffcc00)
        embed.add_field(name="User", value=member.mention)
        embed.add_field(name="Reason", value=reason)
        embed.add_field(name="Warnings", value=warn_count)
        embed.add_field(name="Punishment", value=punishment)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="clear")
    async def clear(self, interaction: discord.Interaction, amount: int):

        if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)

        await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages.", ephemeral=True)

    @app_commands.command(name="kick")
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):

        if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await member.kick(reason=reason)
        await interaction.response.send_message(f"👢 {member.mention} kicked.")

    @app_commands.command(name="ban")
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):

        if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE]):
            return await interaction.response.send_message("❌ Senior staff only.", ephemeral=True)

        await member.ban(reason=reason)
        await interaction.response.send_message(f"🔨 {member.mention} banned.")

    @app_commands.command(name="mute")
    async def mute(self, interaction: discord.Interaction, user: discord.Member, duration: str):

        if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        try:
            unit = duration[-1]
            amount = int(duration[:-1])

            if unit == "m":
                td = timedelta(minutes=amount)
            elif unit == "h":
                td = timedelta(hours=amount)
            elif unit == "d":
                td = timedelta(days=amount)
            else:
                raise ValueError
        except:
            return await interaction.response.send_message("❌ Use 10m / 2h / 1d", ephemeral=True)

        await user.timeout(td)
        await interaction.response.send_message(f"🔇 {user.mention} muted for {duration}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))