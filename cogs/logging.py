import discord
from discord.ext import commands
from config import LOG_CHANNEL

class Logging(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_log(self, embed):
        channel = self.bot.get_channel(LOG_CHANNEL)
        if channel:
            await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message):

        if message.author.bot:
            return

        embed = discord.Embed(title="🗑 Deleted", color=0xff0000)
        embed.add_field(name="User", value=message.author.mention)
        embed.add_field(name="Content", value=message.content or "None")

        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):

        if before.author.bot:
            return

        embed = discord.Embed(title="✏ Edited", color=0xff9900)
        embed.add_field(name="Before", value=before.content or "None", inline=False)
        embed.add_field(name="After", value=after.content or "None", inline=False)

        await self.send_log(embed)

async def setup(bot):
    await bot.add_cog(Logging(bot))