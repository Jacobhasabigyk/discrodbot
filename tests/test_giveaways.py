"""
Offline test of the giveaway cog with a fake Discord (tests/fake_discord.py)
and a fake store API. Run from the bot folder:  python tests/test_giveaways.py
"""
import asyncio
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

import fake_discord as fd  # noqa: E402

fd.install()
os.chdir(tempfile.mkdtemp())

import config  # noqa: E402
import database  # noqa: E402
from services import buttonland  # noqa: E402
import cogs.giveaways as G  # noqa: E402


class FakeBot:
    def __init__(self):
        self.channels, self.guilds, self.users = {}, {}, {}
        self.user = fd.User(999, "Buttonland", bot=True)

    def get_channel(self, id):
        return self.channels.get(int(id))

    async def fetch_channel(self, id):
        raise fd.HTTPException("not found")

    def get_guild(self, id):
        return self.guilds.get(int(id))

    def get_user(self, id):
        return self.users.get(int(id))

    async def fetch_user(self, id):
        raise fd.HTTPException("unknown user")

    async def wait_until_ready(self):
        pass


bot = FakeBot()
guild = fd.Guild(bot, 1)
bot.guilds[1] = guild
guild.me = fd.Member(999, "Buttonland", guild)
for role_id in [config.OWNER_ROLE, config.HEAD_MOD_ROLE, config.MOD_ROLE, 777]:
    guild.roles[role_id] = fd.Role(role_id)

channel = fd.TextChannel(guild, 500, "giveaways")
bot.channels[channel.id] = channel
guild.text_channels.append(channel)

owner = fd.Member(10, "boss", guild, roles=[guild.roles[config.OWNER_ROLE]])
headmod = fd.Member(11, "hm", guild, roles=[guild.roles[config.HEAD_MOD_ROLE]])
mod = fd.Member(12, "mod", guild, roles=[guild.roles[config.MOD_ROLE]])
alice = fd.Member(20, "alice", guild, roles=[guild.roles[777]])
bob = fd.Member(21, "bob", guild)
for m in (owner, headmod, mod, alice, bob):
    guild.members[m.id] = m
    bot.users[m.id] = m

GID = "a" * 24
WID = "b" * 24
calls = []
state = {"entries": set(), "work": {}, "claim": {"status": "not_winner"}, "status": "active", "required": ""}


def gview(**extra):
    base = {
        "id": GID, "title": "Grip giveaway", "productName": "Hi-Capa Grip", "winnersCount": 1, "shippingPaidBy": "store",
        "source": "discord", "status": state["status"], "guildId": "1", "channelId": "500", "messageId": state.get("message_id", ""),
        "requiredRoleId": state["required"], "endsAt": "2030-01-01T00:00:00.000Z", "entryCount": len(state["entries"]), "winners": [],
    }
    base.update(extra)
    return base


async def f_prizes(q=""):
    return [{"id": "c" * 24, "name": "Hi-Capa Grip", "variants": [{"id": "d" * 24, "title": "Black", "inStock": True}]}]


async def f_create(**fields):
    calls.append(("create", fields))
    return gview()


async def f_set_message(gid, mid):
    calls.append(("message", mid))
    state["message_id"] = str(mid)


async def f_get(gid):
    return gview(winners=state.get("winners", []))


async def f_enter(gid, uid, name):
    already = str(uid) in state["entries"]
    state["entries"].add(str(uid))
    return {"already": already, "entries": len(state["entries"])}


async def f_leave(gid, uid):
    state["entries"].discard(str(uid))
    return {"left": True, "entries": len(state["entries"])}


async def f_work():
    work, state["work"] = state["work"], {}
    return work


async def f_announced(gid, mid=None):
    calls.append(("announced", gid, mid))


async def f_dm_done(wid, ok, kind):
    calls.append(("dm_done", wid, ok, kind))


async def f_expired_noted(wid):
    calls.append(("expired_noted", wid))


async def f_claim_noted(wid):
    calls.append(("claim_noted", wid))


async def f_reroll_announced(wid):
    calls.append(("reroll_announced", wid))


async def f_reroll(wid, actor=""):
    calls.append(("reroll", wid))
    return {"winner": {"discordUserId": "21", "id": "e" * 24}}


async def f_extend(wid, actor=""):
    calls.append(("extend", wid))
    return {"winner": {"discordUserId": "20"}}


async def f_claim_link(gid, uid):
    return state["claim"] if str(uid) == "20" else {"status": "not_winner"}


async def f_sendlink(uid, gid="", actor=""):
    calls.append(("sendlink", str(uid)))
    if str(uid) == "404":
        raise buttonland.ButtonlandError("That person has no unclaimed prize", 404)
    return {"claimUrl": "https://buttonland.store/giveaway/gw_abc", "winner": {"id": WID, "claimExpiresAt": "2030-01-03T00:00:00.000Z"}, "giveaway": gview()}


async def f_award(**fields):
    calls.append(("award", fields))
    return {"claimUrl": "https://buttonland.store/giveaway/gw_award", "winner": {"id": WID, "claimExpiresAt": "2030-01-03T00:00:00.000Z"}, "giveaway": gview(productName="Hi-Capa Grip")}


async def f_end(gid, actor=""):
    calls.append(("end", gid))
    return {}


buttonland.is_configured = lambda: True
for name, fn in dict(
    giveaway_prizes=f_prizes, create_giveaway=f_create, set_giveaway_message=f_set_message, get_giveaway=f_get,
    enter_giveaway=f_enter, leave_giveaway=f_leave, giveaway_work=f_work, giveaway_announced=f_announced,
    giveaway_dm_done=f_dm_done, giveaway_expired_noted=f_expired_noted, giveaway_claim_noted=f_claim_noted,
    giveaway_reroll_announced=f_reroll_announced, reroll_winner=f_reroll, extend_winner=f_extend,
    giveaway_claim_link=f_claim_link, send_giveaway_link=f_sendlink, award_prize=f_award, end_giveaway=f_end,
).items():
    setattr(buttonland, name, fn)
G.buttonland = buttonland

shipping_free = types.SimpleNamespace(value="store", name="Free for the winner")


def button_ids(view):
    return [getattr(b, "custom_id", None) or getattr(b, "url", None) for b in (view.children if view else [])]


async def main():
    G.ENTRY_EDIT_DELAY = 0
    cog = G.Giveaways(bot)

    # ---- durations
    assert G.parse_duration("30m") == 30
    assert G.parse_duration("2h") == 120
    assert G.parse_duration("1d12h") == 2160
    assert G.parse_duration("1w") == 10080
    assert G.parse_duration("45") == 45
    assert G.parse_duration("soon") is None and G.parse_duration("2x") is None and G.parse_duration("") is None

    # ---- only owner / head mod / admin can start
    i = fd.Interaction(mod, guild, channel)
    await cog.start(i, "c" * 24, "1h", shipping_free)
    assert "only the owner" in i.sent[-1].content
    i = fd.Interaction(owner, guild, channel)
    await cog.start(i, "c" * 24, "nope", shipping_free)
    assert "duration" in i.sent[-1].content

    i = fd.Interaction(headmod, guild, channel)
    await cog.start(i, "c" * 24, "1d", shipping_free, winners=1, option="choose")
    create = next(c for c in calls if c[0] == "create")[1]
    assert create["durationMinutes"] == 1440 and create["variantId"] == "" and create["shippingPaidBy"] == "store"
    post = channel.messages[-1]
    assert post.embeds[0].title.startswith("🎁") and button_ids(post.view) == [f"gw:enter:{GID}"]
    assert ("message", post.id) in calls
    assert "giveaway posted" in i.sent[-1].content

    # ---- entering
    e = fd.Interaction(alice, guild, channel, f"gw:enter:{GID}", message=post)
    await cog.on_interaction(e)
    assert "you're in" in e.sent[-1].content
    await asyncio.sleep(0.01)
    fields = {f.name: f.value for f in post.embeds[0].fields}
    assert fields["Entries"] == "1", fields
    e2 = fd.Interaction(alice, guild, channel, f"gw:enter:{GID}", message=post)
    await cog.on_interaction(e2)
    assert "already in" in e2.sent[-1].content and button_ids(e2.sent[-1].view) == [f"gw:leave:{GID}"]
    leave = fd.Interaction(alice, guild, channel, f"gw:leave:{GID}", message=post)
    await cog.on_interaction(leave)
    assert "left" in leave.sent[-1].content
    await cog.on_interaction(fd.Interaction(alice, guild, channel, f"gw:enter:{GID}", message=post))

    # role-locked giveaway
    state["required"] = "777"
    rb = fd.Interaction(bob, guild, channel, f"gw:enter:{GID}", message=post)
    await cog.on_interaction(rb)
    assert "only <@&777>" in rb.sent[-1].content
    state["required"] = ""

    # ---- giveaway ends: post edited, winners announced, log posted, DMs
    state["status"] = "ended"
    state["work"] = {
        "announcements": [{"giveaway": gview(status="ended"), "winners": [{"id": WID, "discordUserId": "20", "discordName": "alice"}]}],
    }
    await cog.run_work()
    assert post.embeds[0].fields[0].value == "<@20>"
    assert button_ids(post.view) == [f"gw:claim:{GID}"], "Enter button replaced by Claim prize"
    announcement = channel.messages[-1]
    assert "<@20>" in announcement.content and "48 hours" in announcement.content
    assert ("announced", GID, announcement.id) in calls
    logs = next(c for c in guild.text_channels if c.name == "giveaway-logs")
    assert "ended with" in logs.messages[-1].content

    state["work"] = {"dms": [
        {"winnerId": WID, "kind": "won", "discordUserId": "20", "claimUrl": "https://buttonland.store/giveaway/gw_x", "expiresAt": "2030-01-03T00:00:00.000Z", "giveaway": gview()},
    ]}
    await cog.run_work()
    dm = alice.dms[-1]
    assert dm.embed.title == "🎉 You won!" and button_ids(dm.view) == ["https://buttonland.store/giveaway/gw_x"]
    assert ("dm_done", WID, True, "won") in calls

    # DMs closed: post in the channel instead
    bob.dm_closed = True
    state["work"] = {"dms": [{"winnerId": "f" * 24, "kind": "won", "discordUserId": "21", "claimUrl": "https://x/giveaway/gw_y", "expiresAt": None, "giveaway": gview()}]}
    await cog.run_work()
    assert ("dm_done", "f" * 24, False, "won") in calls
    assert "couldn't DM you" in channel.messages[-1].content and button_ids(channel.messages[-1].view) == [f"gw:claim:{GID}"]

    # ---- Claim prize button
    state["claim"] = {"status": "pending", "claimUrl": "https://buttonland.store/giveaway/gw_x", "expiresAt": "2030-01-03T00:00:00.000Z"}
    c = fd.Interaction(alice, guild, channel, f"gw:claim:{GID}")
    await cog.on_interaction(c)
    assert c.sent[-1].ephemeral and button_ids(c.sent[-1].view) == ["https://buttonland.store/giveaway/gw_x"]
    c2 = fd.Interaction(bob, guild, channel, f"gw:claim:{GID}")
    await cog.on_interaction(c2)
    assert "isn't yours" in c2.sent[-1].content
    state["claim"] = {"status": "claimed", "orderNumber": "BL-1"}
    c3 = fd.Interaction(alice, guild, channel, f"gw:claim:{GID}")
    await cog.on_interaction(c3)
    assert "already claimed" in c3.sent[-1].content and "BL-1" in c3.sent[-1].content

    # ---- expired link: owner gets Reroll / More time buttons
    state["work"] = {"expired": [{"winnerId": WID, "discordUserId": "20", "discordName": "alice", "canReroll": True, "giveaway": gview()}]}
    await cog.run_work()
    note = logs.messages[-1]
    assert "didn't claim" in note.content and button_ids(note.view) == [f"gw:reroll:{WID}", f"gw:extend:{WID}"]
    assert ("expired_noted", WID) in calls

    denied = fd.Interaction(mod, guild, logs, f"gw:reroll:{WID}", message=note)
    await cog.on_interaction(denied)
    assert "only the owner" in denied.sent[-1].content and ("reroll", WID) not in calls
    rr = fd.Interaction(owner, guild, logs, f"gw:reroll:{WID}", message=note)
    await cog.on_interaction(rr)
    assert ("reroll", WID) in calls and "new winner <@21>" in note.content and note.view is None

    state["work"] = {"rerolls": [{"winnerId": "e" * 24, "discordUserId": "21", "replacedDiscordUserId": "20", "giveaway": gview()}]}
    await cog.run_work()
    assert "new winner" in channel.messages[-1].content and "replacing <@20>" in channel.messages[-1].content
    assert ("reroll_announced", "e" * 24) in calls

    # ---- claimed: owner is told where the order is
    state["work"] = {"claims": [{"winnerId": WID, "discordUserId": "20", "orderNumber": "BL-20261015-ABC123", "giveaway": gview()}]}
    await cog.run_work()
    assert "BL-20261015-ABC123" in logs.messages[-1].content and "Needs label" in logs.messages[-1].content
    assert ("claim_noted", WID) in calls

    # ---- /giveaway sendlink
    s = fd.Interaction(owner, guild, channel)
    await cog.sendlink(s, alice)
    assert alice.dms[-1].embed.title == "🎁 Here's your claim link"
    assert "sent" in s.sent[-1].content
    s2 = fd.Interaction(owner, guild, channel)
    await cog.sendlink(s2, bob)  # DMs closed: owner gets the link to send by hand
    assert "DMs closed" in s2.sent[-1].content and "gw_abc" in s2.sent[-1].content
    nobody = fd.Member(404, "nobody", guild)
    s3 = fd.Interaction(owner, guild, channel)
    await cog.sendlink(s3, nobody)
    assert "no unclaimed prize" in s3.sent[-1].content
    s4 = fd.Interaction(mod, guild, channel)
    await cog.sendlink(s4, alice)
    assert "only the owner" in s4.sent[-1].content

    # ---- /giveaway award: owner only
    a = fd.Interaction(headmod, guild, channel)
    await cog.award(a, alice, "c" * 24, shipping_free)
    assert "only the owner" in a.sent[-1].content
    a2 = fd.Interaction(owner, guild, channel)
    await cog.award(a2, alice, "c" * 24, types.SimpleNamespace(value="winner"), option="d" * 24, title="Old bot winner")
    award = next(c for c in calls if c[0] == "award")[1]
    assert award["shippingPaidBy"] == "winner" and award["variantId"] == "d" * 24 and award["discordUserId"] == "20"
    assert alice.dms[-1].embed.title == "🎉 You won!" and "gw_award" in button_ids(alice.dms[-1].view)[0]
    assert "gave **Hi-Capa Grip**" in logs.messages[-1].content

    # ---- autocomplete
    choices = await cog.prize_choices(fd.Interaction(owner, guild, channel), "grip")
    assert choices[0].value == "c" * 24

    # store can't be reached / old backend: say why instead of an empty list
    async def broken(q=""):
        raise buttonland.ButtonlandError("Not found", 404)
    buttonland.giveaway_prizes = broken
    cog.prize_cache = {"at": 0, "query": None, "items": []}
    choices = await cog.prize_choices(fd.Interaction(owner, guild, channel), "kriss")
    assert choices[0].value == "error" and "restart the backend" in choices[0].name
    typed = fd.Interaction(owner, guild, channel)
    await cog.award(typed, alice, "WE/TM KRISS Vector Style Mag Plate", shipping_free)
    assert "pick the prize from the list" in typed.sent[-1].content
    buttonland.giveaway_prizes = f_prizes
    cog.prize_cache = {"at": 0, "query": None, "items": []}
    ns = fd.Interaction(owner, guild, channel)
    ns.namespace = types.SimpleNamespace(prize="c" * 24)
    options = await cog.option_choices(ns, "")
    assert [o.value for o in options] == ["choose", "d" * 24]

    # ---- /giveaway end runs the work right away
    state["status"] = "active"
    en = fd.Interaction(owner, guild, channel)
    await cog.end(en, GID)
    assert ("end", GID) in calls and "ended" in en.sent[-1].content

    print("giveaways: all checks passed")


asyncio.run(main())
