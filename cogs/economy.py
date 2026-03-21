import discord
from discord.ext import commands
from discord import app_commands
import random

from database import cursor, conn, get_balance

class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="balance")
    async def balance(self, interaction: discord.Interaction):
        bal = get_balance(interaction.user.id)
        await interaction.response.send_message(f"💰 ${bal}")

    @app_commands.command(name="roll")
    async def roll(self, interaction: discord.Interaction, choice: str, amount: int):

        choice = choice.lower()
        if choice not in ["high", "low"]:
            return await interaction.response.send_message("❌ high or low", ephemeral=True)

        balance = get_balance(interaction.user.id)

        if balance < amount:
            return await interaction.response.send_message(f"❌ You have ${balance}", ephemeral=True)

        number = random.randint(1, 100)
        result = "high" if number >= 51 else "low"

        win = choice == result

        if win:
            balance += amount
        else:
            balance -= amount

        cursor.execute("UPDATE balances SET balance=? WHERE user_id=?", (balance, str(interaction.user.id)))
        conn.commit()

        await interaction.response.send_message(
            f"🎲 {number} ({result})\n💰 ${balance}"
        )

    @app_commands.command(name="allin")
    async def allin(self, interaction: discord.Interaction, choice: str):

        balance = get_balance(interaction.user.id)

        if balance <= 0:
            return await interaction.response.send_message("❌ no money", ephemeral=True)

        number = random.randint(1, 100)
        result = "high" if number >= 51 else "low"

        if choice.lower() == result:
            balance *= 2
        else:
            balance = 0

        cursor.execute("UPDATE balances SET balance=? WHERE user_id=?", (balance, str(interaction.user.id)))
        conn.commit()

        await interaction.response.send_message(f"🎲 {number} → ${balance}")

    @app_commands.command(name="leaderboard")
    async def leaderboard(self, interaction: discord.Interaction):

        cursor.execute("SELECT user_id, balance FROM balances ORDER BY balance DESC LIMIT 10")
        rows = cursor.fetchall()

        embed = discord.Embed(title="🏆 Richest", color=0xf1c40f)

        for i, (uid, bal) in enumerate(rows, 1):
            member = interaction.guild.get_member(int(uid))
            name = member.name if member else uid
            embed.add_field(name=f"#{i} {name}", value=f"${bal}", inline=False)

        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Economy(bot))