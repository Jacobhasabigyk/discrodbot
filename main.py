import random
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")


@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")


# HELP
@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="Buttonland Bot Commands",
        description="List of available commands",
        color=0x00ff00
    )

    embed.add_field(name="!help", value="Shows this command list", inline=False)
    embed.add_field(name="!ping", value="Shows bot latency", inline=False)
    embed.add_field(name="!shop", value="Shows our official store", inline=False)
    embed.add_field(name="!userinfo", value="Shows user info", inline=False)
    embed.add_field(name="!serverinfo", value="Shows server info", inline=False)
    embed.add_field(name="!say", value="Bot repeats your message", inline=False)
    embed.add_field(name="!clear", value="Deletes messages (admin only)", inline=False)
    embed.add_field(name="!coinflip", value="Flip a coin", inline=False)
    embed.add_field(name="!invy", value="Invy sends love ❤️", inline=False)
    embed.add_field(name="!pp", value="Shows pp size (meme command)", inline=False)
    embed.add_field(name="!roll", value="Roll a number 1-100 🎲", inline=False)
    embed.add_field(name="!8ball", value="Ask the magic 8ball a question", inline=False)

    await ctx.send(embed=embed)


# SHOP
@bot.command()
async def shop(ctx):
    embed = discord.Embed(
        title="Official Buttonland Store",
        description="You can find all our legit products only we sell at:",
        color=0x3498db
    )

    embed.add_field(
        name="Website",
        value="https://buttonland.store\n\nThis is the **only official website**.",
        inline=False
    )

    await ctx.send(embed=embed)


# PING
@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"Pong! {latency}ms")


# COINFLIP
@bot.command()
async def coinflip(ctx):
    result = random.choice(["Heads 🪙", "Tails 🪙"])
    await ctx.send(result)


# INVY
@bot.command()
async def invy(ctx):
    messages = [
        f"Invy loves you {ctx.author.mention} ❤️",
        f"Invy appreciates you {ctx.author.mention} 🙌",
        f"Invy thinks you're awesome {ctx.author.mention} 😎",
        f"Invy approves of you {ctx.author.mention} 👍",
        f"Invy says you're a real one {ctx.author.mention} 🔥",
        f"Invy sends love to {ctx.author.mention} 💙"
    ]

    await ctx.send(random.choice(messages))


# USERINFO
@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author

    embed = discord.Embed(title=f"{member.name}'s Info", color=0x3498db)

    embed.add_field(name="Username", value=member.name)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"))

    embed.set_thumbnail(url=member.avatar.url)

    await ctx.send(embed=embed)


# SERVERINFO
@bot.command()
async def serverinfo(ctx):
    guild = ctx.guild

    embed = discord.Embed(title=guild.name, color=0x2ecc71)

    embed.add_field(name="Members", value=guild.member_count)
    embed.add_field(name="Owner", value=guild.owner)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"))

    await ctx.send(embed=embed)


# SAY
@bot.command()
async def say(ctx, *, message):
    await ctx.message.delete()
    await ctx.send(message)


# PP MEME
@bot.command()
async def pp(ctx, member: discord.Member = None):
    member = member or ctx.author

    # Your user ID
    if member.id == 1267677795975303242:
        await ctx.send("Error: his is too big to measure 😳")
        return

    size = random.randint(1, 10)
    pp = "8" + "=" * size + "D"

    await ctx.send(f"{member.mention}'s pp size:\n{pp}")

# ROLL
@bot.command()
async def roll(ctx):
    number = random.randint(1, 100)
    await ctx.send(f"You rolled **{number}** 🎲")


# 8BALL
@bot.command(name="8ball")
async def eightball(ctx, *, question):
    responses = [
        "Yes",
        "No",
        "Maybe",
        "Definitely",
        "Ask again later",
        "Absolutely",
        "Not looking good",
        "Very likely"
    ]

    await ctx.send(f"🎱 {random.choice(responses)}")


# CLEAR
@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Deleted {amount} messages.", delete_after=3)


bot.run(TOKEN)