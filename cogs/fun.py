import discord
from discord.ext import commands
from discord import app_commands
import random

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="coinflip")
    async def coinflip(self, interaction: discord.Interaction):
        await interaction.response.send_message(random.choice(["Heads", "Tails"]))

    @app_commands.command(name="8ball")
    async def eightball(self, interaction: discord.Interaction, question: str):
        responses = ["Yes", "No", "Maybe", "Definitely", "Ask again later"]
        await interaction.response.send_message(f"🎱 {random.choice(responses)}")

    @app_commands.command(name="invy")
    async def invy(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Invy loves you {interaction.user.mention} ❤️")

    @app_commands.command(name="pp")
    async def pp(self, interaction: discord.Interaction, member: discord.Member = None):

        member = member or interaction.user
        size = random.randint(1, 10)

        await interaction.response.send_message(f"8{'='*size}D")

async def setup(bot):
    await bot.add_cog(Fun(bot))