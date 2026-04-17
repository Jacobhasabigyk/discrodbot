import discord
from discord.ext import commands
from discord import app_commands

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"{round(self.bot.latency*1000)}ms")

    @app_commands.command(name="help")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message("Use / commands")

    @app_commands.command(name="avatar")
    async def avatar(self, interaction: discord.Interaction, member: discord.Member = None):

        member = member or interaction.user
        embed = discord.Embed()
        embed.set_image(url=member.display_avatar.url)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo")
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member = None):

        member = member or interaction.user

        embed = discord.Embed(title=member.name)
        embed.add_field(name="ID", value=member.id)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo")
    async def serverinfo(self, interaction: discord.Interaction):

        guild = interaction.guild

        embed = discord.Embed(title=guild.name)
        embed.add_field(name="Members", value=guild.member_count)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shop")
    async def shop(self, interaction: discord.Interaction):
        await interaction.response.send_message("https://buttonland.store")

    @app_commands.command(name="applyforcc")
    async def shop(self, interaction: discord.Interaction):
        await interaction.response.send_message("Go to https://buttonlandmail.com to apply")
        
    @app_commands.command(name="fuckcross")
    async def shop(self, interaction: discord.Interaction):
        await interaction.response.send_message("Hmm, I agree with you he kinda is a little bitch.")
async def setup(bot):
    await bot.add_cog(General(bot))
