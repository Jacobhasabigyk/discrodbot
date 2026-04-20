import discord

class RefundView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def ask_for_reason(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "✍️ type your message to send to the user (30s timeout)",
            ephemeral=True
        )

        def check(m):
            return (
                m.author.id == interaction.user.id and
                m.channel == interaction.channel
            )

        try:
            msg = await interaction.client.wait_for("message", timeout=30, check=check)
            return msg.content
        except:
            return None

    async def dm_user(self, interaction: discord.Interaction, status: str, message: str):
        embed = interaction.message.embeds[0]
        footer = embed.footer.text or ""

        # extract user id from footer
        try:
            user_id = int(footer.split(":")[1].strip())
            user = await interaction.client.fetch_user(user_id)
        except:
            return False

        try:
            dm_embed = discord.Embed(
                title=f"💸 Refund {status}",
                color=0x00ff00 if status == "Approved" else 0xff0000
            )

            dm_embed.add_field(name="📦 Order", value=embed.fields[1].value)
            dm_embed.add_field(name="💬 Message", value=message or "No message provided")

            await user.send(embed=dm_embed)
            return True
        except:
            return False

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):

        reply = await self.ask_for_reason(interaction)

        if reply is None:
            return await interaction.followup.send("❌ timed out", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = 0x00ff00
        embed.title = "✅ Refund Approved"
        embed.add_field(name="🧑‍💼 Staff Reply", value=reply, inline=False)

        await interaction.message.edit(embed=embed, view=None)

        success = await self.dm_user(interaction, "Approved", reply)

        await interaction.followup.send(
            "✅ refund approved + user notified" if success else "⚠️ approved but user DMs are closed",
            ephemeral=True
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):

        reply = await self.ask_for_reason(interaction)

        if reply is None:
            return await interaction.followup.send("❌ timed out", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = 0xff0000
        embed.title = "❌ Refund Denied"
        embed.add_field(name="🧑‍💼 Staff Reply", value=reply, inline=False)

        await interaction.message.edit(embed=embed, view=None)

        success = await self.dm_user(interaction, "Denied", reply)

        await interaction.followup.send(
            "❌ refund denied + user notified" if success else "⚠️ denied but user DMs are closed",
            ephemeral=True
        )