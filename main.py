import random
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from datetime import timedelta
from discord import app_commands
import sqlite3

conn = sqlite3.connect("warnings.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    user_id TEXT,
    reason TEXT
)
""")

conn.commit()
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in environment variables.")
# ROLE IDS
OWNER_ROLE = 1459718191344259155
HEAD_MOD_ROLE = 1471268870885998632
MOD_ROLE = 1468440661153026059
SUPPORT_ROLE = 1475784384404783176
BUYER_ROLE = 1459718958931513354

# LOG CHANNEL
LOG_CHANNEL = 1480454191666429952

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=None, intents=intents, help_command=None)

def has_role_interaction(interaction, roles):
    user_roles = [role.id for role in interaction.user.roles]
    return any(role in user_roles for role in roles)
# ROLE CHECK
def has_role(ctx, roles):
    user_roles = [role.id for role in ctx.author.roles]
    return any(role in user_roles for role in roles)


# LOG FUNCTION
async def send_log(embed):
    channel = bot.get_channel(LOG_CHANNEL)
    if channel:
        await channel.send(embed=embed)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)

    print(f"Bot online as {bot.user}")
@bot.tree.command(name="warn", description="Warn a user")
@app_commands.describe(
    member="User to warn",
    reason="Reason for warning"
)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):

    # Staff permission check
    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
        await interaction.response.send_message(
            "❌ Staff only.",
            ephemeral=True
        )
        return

    # Insert warning into database
    cursor.execute(
        "INSERT INTO warnings (user_id, reason) VALUES (?, ?)",
        (str(member.id), reason)
    )
    conn.commit()

    # Count warnings
    cursor.execute(
        "SELECT COUNT(*) FROM warnings WHERE user_id=?",
        (str(member.id),)
    )

    warn_count = cursor.fetchone()[0]

    await interaction.response.send_message(
        f"⚠️ {member.mention} warned.\nTotal warnings: **{warn_count}**"
    )
@bot.tree.command(name="warnings", description="Check user warnings")
async def warnings(
    interaction: discord.Interaction,
    member: discord.Member
):

    cursor.execute(
        "SELECT reason FROM warnings WHERE user_id=?",
        (str(member.id),)
    )

    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message("✅ No warnings.")
        return

    embed = discord.Embed(
        title=f"Warnings for {member}",
        color=0xffcc00
    )

    for i, row in enumerate(rows, 1):
        embed.add_field(
            name=f"Warning {i}",
            value=row[0],
            inline=False
        )

    await interaction.response.send_message(embed=embed)
@bot.tree.command(name="clearwarns", description="Clear warnings for a user")
async def clearwarns(
    interaction: discord.Interaction,
    member: discord.Member
):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE]):
        await interaction.response.send_message(
            "❌ Only senior staff.",
            ephemeral=True
        )
        return

    cursor.execute(
        "DELETE FROM warnings WHERE user_id=?",
        (str(member.id),)
    )

    conn.commit()

    await interaction.response.send_message(
        f"✅ Cleared warnings for {member.mention}"
    )
@bot.tree.command(name="lock", description="Lock the channel")
async def lock(interaction: discord.Interaction):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False

    await interaction.channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message("🔒 Channel locked.")
@bot.tree.command(name="unlock", description="Unlock the channel")
async def unlock(interaction: discord.Interaction):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True

    await interaction.channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message("🔓 Channel unlocked.")
@bot.tree.command(name="unmute", description="Unmute a user")
async def unmute(
    interaction: discord.Interaction,
    member: discord.Member
):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(
            "❌ Cannot unmute someone with equal or higher role.",
            ephemeral=True
        )
        return

    await member.timeout(None)

    await interaction.response.send_message(
        f"🔊 {member.mention} has been unmuted."
    )

@bot.tree.command(name="unban", description="Unban a user")
async def unban(
    interaction: discord.Interaction,
    user_id: str
):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE]):
        await interaction.response.send_message(
            "❌ Only senior staff.",
            ephemeral=True
        )
        return

    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)

        await interaction.response.send_message(
            f"✅ {user} has been unbanned."
        )

    except:
        await interaction.response.send_message(
            "❌ Could not find that banned user."
        )

# MESSAGE DELETE LOG
@bot.event
async def on_message_delete(message):

    if message.author.bot:
        return

    embed = discord.Embed(
        title="🗑 Message Deleted",
        color=0xff0000
    )

    embed.add_field(name="User", value=message.author.mention)
    embed.add_field(name="Channel", value=message.channel.mention)
    embed.add_field(name="Content", value=message.content or "No text")

    await send_log(embed)


# MESSAGE EDIT LOG
@bot.event
async def on_message_edit(before, after):

    if before.author.bot:
        return

    embed = discord.Embed(
        title="✏ Message Edited",
        color=0xff9900
    )

    embed.add_field(name="User", value=before.author.mention)
    embed.add_field(name="Channel", value=before.channel.mention)
    embed.add_field(name="Before", value=before.content or "None", inline=False)
    embed.add_field(name="After", value=after.content or "None", inline=False)

    await send_log(embed)


# ERROR HANDLER
@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠ Missing arguments.")

    elif isinstance(error, commands.CommandNotFound):
        return


@bot.tree.command(name="help", description="Show bot commands")
async def help(interaction: discord.Interaction):

    embed = discord.Embed(
        title="📘 Buttonland Bot",
        description="Use `/` to see all commands.",
        color=0x00ff00
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="shop", description="View the Buttonland store")
async def shop(interaction: discord.Interaction):

    embed = discord.Embed(
        title="Official Buttonland Store",
        description="https://buttonland.store",
        color=0x3498db
    )

    await interaction.response.send_message(embed=embed)

# PING
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):

    latency = round(bot.latency * 1000)

    await interaction.response.send_message(
        f"Pong! {latency}ms"
    )

@bot.tree.command(name="avatar", description="Show a user's avatar")
async def avatar(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    member = member or interaction.user

    embed = discord.Embed(
        title=f"{member.name}'s Avatar",
        color=0x3498db
    )

    embed.set_image(url=member.display_avatar.url)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="View user info")
async def userinfo(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    member = member or interaction.user

    embed = discord.Embed(
        title=f"{member.name}'s Info",
        color=0x3498db
    )

    embed.add_field(name="Username", value=member.name)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Created", value=member.created_at.strftime("%Y-%m-%d"))

    embed.set_thumbnail(url=member.display_avatar.url)

    await interaction.response.send_message(embed=embed)

# SERVERINFO
@bot.tree.command(name="serverinfo", description="Show server info")
async def serverinfo(interaction: discord.Interaction):

    guild = interaction.guild

    embed = discord.Embed(
        title=guild.name,
        color=0x2ecc71
    )

    embed.add_field(name="Members", value=guild.member_count)
    embed.add_field(name="Owner", value=guild.owner)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"))

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="say", description="Make the bot say something")
async def say(
    interaction: discord.Interaction,
    message: str
):

    # Only Head Mods and Owner
    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE]):
        await interaction.response.send_message(
            "❌ Only Head Mods or Owner can use this.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(message)

@bot.tree.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):

    await interaction.response.send_message(
        random.choice(["Heads 🪙", "Tails 🪙"])
    )

@bot.tree.command(name="roll", description="Roll a number 1-100")
async def roll(interaction: discord.Interaction):

    await interaction.response.send_message(
        f"You rolled **{random.randint(1,100)}** 🎲"
    )

@bot.tree.command(name="8ball", description="Ask the magic 8ball")
async def eightball(
    interaction: discord.Interaction,
    question: str
):

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

    await interaction.response.send_message(
        f"🎱 {random.choice(responses)}"
    )

@bot.tree.command(name="invy", description="Send love from Invy")
async def invy(interaction: discord.Interaction):

    messages = [
        f"Invy loves you {interaction.user.mention} ❤️",
        f"Invy appreciates you {interaction.user.mention}",
        f"Invy thinks you're awesome {interaction.user.mention}",
    ]

    await interaction.response.send_message(random.choice(messages))

# PP MEME
@bot.tree.command(name="pp", description="Measure pp size")
async def pp(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    member = member or interaction.user

    if member.id == 1267677795975303242:
        await interaction.response.send_message(
            "Error: too big to measure 😳"
        )
        return

    size = random.randint(1, 10)

    await interaction.response.send_message(
        f"{member.mention}'s pp size:\n8{'='*size}D"
    )

@bot.tree.command(name="clear", description="Delete messages")
async def clear(
    interaction: discord.Interaction,
    amount: int
):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    await interaction.channel.purge(limit=amount)

    await interaction.response.send_message(
        f"🧹 Deleted {amount} messages."
    )

@bot.tree.command(name="kick", description="Kick a user")
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = None
):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    await member.kick(reason=reason)

    embed = discord.Embed(
        title="👢 User Kicked",
        color=0xff0000
    )

    embed.add_field(name="User", value=member.mention)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.add_field(name="Reason", value=reason or "None")

    await send_log(embed)

    await interaction.response.send_message(f"{member.mention} has been kicked.")



@bot.tree.command(name="ban", description="Ban a user")
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = None
):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE]):
        await interaction.response.send_message("❌ Only senior staff.", ephemeral=True)
        return

    await member.ban(reason=reason)

    embed = discord.Embed(
        title="🔨 User Banned",
        color=0x8B0000
    )

    embed.add_field(name="User", value=member.mention)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.add_field(name="Reason", value=reason or "None")

    await send_log(embed)

    await interaction.response.send_message(f"{member.mention} has been banned.")


@bot.tree.command(name="mute", description="Mute a user for a specific time")
@app_commands.describe(
    user="User to mute",
    duration="Time like 10m, 2h, 1d",
    reason="Reason for mute"
)
async def mute_slash(
    interaction: discord.Interaction,
    user: discord.Member,
    duration: str,
    reason: str = "No reason provided"
):

    # role check
    roles = [role.id for role in interaction.user.roles]

    if not any(r in roles for r in [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
        await interaction.response.send_message(
            "❌ Staff only.",
            ephemeral=True
        )
        return

    # role hierarchy protection
    if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(
            "❌ Cannot mute someone with equal or higher role.",
            ephemeral=True
        )
        return

    # parse duration
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
        await interaction.response.send_message(
            "❌ Time must be like `10m`, `2h`, `1d`",
            ephemeral=True
        )
        return

    await user.timeout(td, reason=reason)

    await interaction.response.send_message(
        f"🔇 {user.mention} muted for **{duration}**"
    )

    embed = discord.Embed(
        title="🔇 User Muted",
        color=0xffcc00
    )

    embed.add_field(name="User", value=user.mention)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.add_field(name="Duration", value=duration)
    embed.add_field(name="Reason", value=reason)

    await send_log(embed)
@bot.tree.command(name="verifybuyer", description="Verify a buyer")
async def verifybuyer(
    interaction: discord.Interaction,
    member: discord.Member
):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, SUPPORT_ROLE]):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    role = interaction.guild.get_role(BUYER_ROLE)

    await member.add_roles(role)

    embed = discord.Embed(
        title="💰 Buyer Verified",
        color=0x00ff00
    )

    embed.add_field(name="User", value=member.mention)
    embed.add_field(name="Verified By", value=interaction.user.mention)

    await send_log(embed)

    await interaction.response.send_message(f"{member.mention} is now a verified buyer.")

# BUYER COMMAND
@bot.tree.command(name="buyers", description="Buyer only command")
async def buyers(interaction: discord.Interaction):

    if BUYER_ROLE not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Buyers only.", ephemeral=True)
        return

    await interaction.response.send_message(
        "💙 Thanks for supporting Buttonland!"
    )
@bot.tree.command(name="staff", description="Ping staff for help")
async def staff(interaction: discord.Interaction):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE]):
        await interaction.response.send_message("❌ Management only.", ephemeral=True)
        return

    await interaction.response.send_message(
        "<@&1468440661153026059> Staff assistance needed."
    )
print("Starting Buttonland bot...")
bot.run(TOKEN)
