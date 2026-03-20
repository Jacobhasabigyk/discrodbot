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
CREATE TABLE IF NOT EXISTS balances (
    user_id TEXT PRIMARY KEY,
    balance INTEGER
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
def get_balance(user_id):
    cursor.execute("SELECT balance FROM balances WHERE user_id=?", (str(user_id),))
    row = cursor.fetchone()

    if not row:
        # default 100 for new users
        cursor.execute(
            "INSERT INTO balances (user_id, balance) VALUES (?, ?)",
            (str(user_id), 100)
        )
        conn.commit()
        return 100

    return row[0]


def update_balance(user_id, amount):
    current = get_balance(user_id)
    new_balance = current + amount

    cursor.execute(
        "UPDATE balances SET balance=? WHERE user_id=?",
        (new_balance, str(user_id))
    )
    conn.commit()

    return new_balance

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
@bot.tree.command(name="giveall", description="Give money to all users")
@app_commands.describe(amount="Amount to give everyone")
async def giveall(interaction: discord.Interaction, amount: int):

    if not has_role_interaction(interaction, [OWNER_ROLE]):
        return await interaction.response.send_message("❌ Owner only", ephemeral=True)

    if amount <= 0:
        return await interaction.response.send_message("❌ Amount must be positive", ephemeral=True)

    await interaction.response.defer()

    # get all users in DB
    cursor.execute("SELECT user_id FROM balances")
    users = cursor.fetchall()

    count = 0

    for (user_id,) in users:
        cursor.execute(
            "UPDATE balances SET balance = balance + ? WHERE user_id=?",
            (amount, user_id)
        )
        count += 1

    conn.commit()

    await interaction.followup.send(
        f"🌎 Gave **${amount}** to **{count} users**"
    )

    @bot.tree.command(name="addbalance", description="Add money to a user")
@app_commands.describe(member="User", amount="Amount to add")
async def addbalance(interaction: discord.Interaction, member: discord.Member, amount: int):

    if not has_role_interaction(interaction, [OWNER_ROLE]):
        return await interaction.response.send_message("❌ Owner only", ephemeral=True)

    if amount <= 0:
        return await interaction.response.send_message("❌ Amount must be positive", ephemeral=True)

    new_balance = update_balance(member.id, amount)

    await interaction.response.send_message(
        f"💰 Added **${amount}** to {member.mention}\nNew Balance: **${new_balance}**"
    )
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
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return

    # Add warning to database
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

    punishment = "None"

    try:

        # 1st warning → 10 min timeout
        if warn_count == 1:
            await member.timeout(timedelta(minutes=10), reason=reason)
            punishment = "10 minute timeout"

        # 2nd warning → 30 min timeout
        elif warn_count == 2:
            await member.timeout(timedelta(minutes=30), reason=reason)
            punishment = "30 minute mute"

        # 3rd warning → 2 hour timeout
        elif warn_count == 3:
            await member.timeout(timedelta(hours=2), reason=reason)
            punishment = "2 hour mute (EXTREME WARNING)"

        # 4th warning → 24 hour timeout
        elif warn_count == 4:
            await member.timeout(timedelta(hours=24), reason=reason)
            punishment = "24 hour mute (FINAL WARNING)"

        # 5th warning → ban
        elif warn_count >= 5:
            await member.ban(reason="5 warnings reached")
            punishment = "Permanent Ban"

    except:
        punishment = "Warning added but punishment failed"

    embed = discord.Embed(
        title="⚖️ User Warned",
        color=0xffcc00
    )

    embed.add_field(name="User", value=member.mention)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Total Warnings", value=warn_count)
    embed.add_field(name="Punishment", value=punishment)

    await interaction.response.send_message(embed=embed)

    await send_log(embed)
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
# PVP COINFLIP ACCEPT BUTTONS
class CoinFlipView(discord.ui.View):
    def __init__(self, challenger, opponent, challenger_side):
        super().__init__(timeout=30)
        self.challenger = challenger
        self.opponent = opponent
        self.challenger_side = challenger_side
        self.opponent_side = "Tails" if challenger_side == "Heads" else "Heads"

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):

        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "❌ Only the challenged user can accept.",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(content="🪙 Flipping the coin...", view=None)

        await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(seconds=2))

        result = random.choice(["Heads", "Tails"])

        if result == self.challenger_side:
            winner = self.challenger
            side = self.challenger_side
        else:
            winner = self.opponent
            side = self.opponent_side

        embed = discord.Embed(
            title="🪙 PvP Coin Flip",
            color=0xf1c40f
        )

        embed.add_field(name="Coin Landed On", value=result)
        embed.add_field(name="Winner", value=f"🏆 {winner.mention} won with **{side}**")

        await interaction.channel.send(embed=embed)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):

        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "❌ Only the challenged user can decline.",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="❌ Coin flip declined.",
            view=None
        )


# PVP COINFLIP COMMAND
@bot.tree.command(name="pvpcoinflip", description="Challenge someone to a coin flip")
@app_commands.describe(
    opponent="User you want to challenge",
    side="Choose Heads or Tails"
)
async def pvpcoinflip(
    interaction: discord.Interaction,
    opponent: discord.Member,
    side: str
):

    side = side.capitalize()

    if side not in ["Heads", "Tails"]:
        await interaction.response.send_message(
            "❌ Choose **Heads** or **Tails**.",
            ephemeral=True
        )
        return

    if opponent.bot:
        await interaction.response.send_message(
            "❌ You cannot challenge a bot.",
            ephemeral=True
        )
        return

    if opponent.id == interaction.user.id:
        await interaction.response.send_message(
            "❌ You can't challenge yourself.",
            ephemeral=True
        )
        return

    opponent_side = "Tails" if side == "Heads" else "Heads"

    embed = discord.Embed(
        title="🪙 Coin Flip Challenge",
        color=0xf1c40f
    )

    embed.add_field(name="Challenger", value=interaction.user.mention)
    embed.add_field(name="Opponent", value=opponent.mention)
    embed.add_field(name=f"{interaction.user.display_name}'s Side", value=side)
    embed.add_field(name=f"{opponent.display_name}'s Side", value=opponent_side)

    embed.set_footer(text=f"{opponent.display_name}, click Accept or Decline")

    view = CoinFlipView(interaction.user, opponent, side)

    await interaction.response.send_message(embed=embed, view=view)
@bot.tree.command(name="roll", description="Bet on high or low")
@app_commands.describe(
    choice="Choose high or low",
    amount="Amount to bet"
)
async def roll(
    interaction: discord.Interaction,
    choice: str,
    amount: int
):

    choice = choice.lower()

    # ✅ validate choice
    if choice not in ["high", "low"]:
        await interaction.response.send_message(
            "❌ Choose `high` or `low`",
            ephemeral=True
        )
        return

    # ✅ validate amount
    if amount <= 0:
        await interaction.response.send_message(
            "❌ Bet must be greater than 0",
            ephemeral=True
        )
        return

    user_id = str(interaction.user.id)

    # ✅ get or create balance
    cursor.execute("SELECT balance FROM balances WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if not row:
        cursor.execute(
            "INSERT INTO balances (user_id, balance) VALUES (?, ?)",
            (user_id, 100)
        )
        conn.commit()
        balance = 100
    else:
        balance = row[0]

    # ❌ not enough money
    if balance < amount:
        await interaction.response.send_message(
            f"❌ You only have **${balance}**",
            ephemeral=True
        )
        return

    # 🎲 roll
    number = random.randint(1, 100)
    result = "high" if number >= 51 else "low"

    win = choice == result

    # 💰 update balance
    if win:
        balance += amount
    else:
        balance -= amount

    # prevent negative (optional but smart)
    if balance < 0:
        balance = 0

    cursor.execute(
        "UPDATE balances SET balance=? WHERE user_id=?",
        (balance, user_id)
    )
    conn.commit()

    # 🎨 embed
    embed = discord.Embed(
        title="🎲 Roll Result",
        color=0x00ff00 if win else 0xff0000
    )

    embed.add_field(name="Your Choice", value=choice.capitalize())
    embed.add_field(name="Rolled Number", value=str(number))
    embed.add_field(name="Result", value=result.capitalize())

    if win:
        embed.add_field(name="Outcome", value=f"✅ You WON ${amount}")
    else:
        embed.add_field(name="Outcome", value=f"❌ You LOST ${amount}")

    embed.add_field(name="New Balance", value=f"${balance}")

    await interaction.response.send_message(embed=embed)
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

@bot.tree.command(name="pp", description="Measure pp size")
async def pp(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    member = member or interaction.user

    if member.id in [1267677795975303242, 1474851006977282310]:
        await interaction.response.send_message(
            "Error: too big to measure 😳"
        )
        return

    size = random.randint(1, 10)

    await interaction.response.send_message(
        f"{member.mention}'s pp size:\n8{'='*size}D"
    )
@bot.tree.command(name="clear", description="Delete messages")
async def clear(interaction: discord.Interaction, amount: int):

    if not has_role_interaction(interaction, [OWNER_ROLE, HEAD_MOD_ROLE, MOD_ROLE]):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    deleted = await interaction.channel.purge(limit=amount)

    await interaction.followup.send(
        f"🧹 Deleted {len(deleted)} messages.",
        ephemeral=True
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

@bot.tree.command(name="balance", description="Check your balance")
async def balance(interaction: discord.Interaction):

    bal = get_balance(interaction.user.id)

    await interaction.response.send_message(
        f"💰 Your balance: **${bal}**"
    )

@bot.tree.command(name="allin", description="Go all in")
@app_commands.describe(choice="high or low")
async def allin(interaction: discord.Interaction, choice: str):

    balance = get_balance(interaction.user.id)

    if balance <= 0:
        await interaction.response.send_message("❌ You have no money.", ephemeral=True)
        return

    # reuse your roll logic
    number = random.randint(1, 100)
    result = "high" if number >= 51 else "low"

    win = choice.lower() == result

    if win:
        balance *= 2
    else:
        balance = 0

    cursor.execute(
        "UPDATE balances SET balance=? WHERE user_id=?",
        (balance, str(interaction.user.id))
    )
    conn.commit()

    await interaction.response.send_message(
        f"🎲 Rolled {number} ({result})\n💰 New Balance: **${balance}**"
    )


@bot.tree.command(name="leaderboard", description="Top richest users")
async def leaderboard(interaction: discord.Interaction):

    cursor.execute(
        "SELECT user_id, balance FROM balances ORDER BY balance DESC LIMIT 10"
    )
    rows = cursor.fetchall()

    if not rows:
        return await interaction.response.send_message("❌ No data yet.")

    embed = discord.Embed(title="🏆 Richest Players", color=0xf1c40f)

    for i, (user_id, balance) in enumerate(rows, 1):

        member = interaction.guild.get_member(int(user_id))
        name = member.name if member else f"User {user_id}"

        embed.add_field(
            name=f"#{i} {name}",
            value=f"${balance}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

    
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
bot.run(TOKEN, reconnect=True)
