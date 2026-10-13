"""
Offline end-to-end test of Tickets v2 with a fake Discord (tests/fake_discord.py).
Run from the bot folder:  python tests/test_tickets.py
Uses a throwaway database in a temp folder.
"""
import asyncio
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

import fake_discord as fd  # noqa: E402

fd.install()
os.chdir(tempfile.mkdtemp())  # database.py creates warnings.db in the working folder
os.environ["ANTHROPIC_API_KEY"] = ""

import config  # noqa: E402
import database  # noqa: E402
from services import buttonland  # noqa: E402
import cogs.tickets as T  # noqa: E402


# ---------------- fake world ----------------
class FakeBot:
    def __init__(self):
        self.channels = {}
        self.guilds = {}
        self.user = fd.User(999, "Buttonland", bot=True)

    def get_channel(self, id):
        return self.channels.get(int(id))

    async def fetch_channel(self, id):
        if int(id) in self.channels:
            return self.channels[int(id)]
        raise fd.HTTPException("not found")

    def get_guild(self, id):
        return self.guilds.get(int(id))

    async def wait_until_ready(self):
        pass


bot = FakeBot()
guild = fd.Guild(bot, 1)
bot.guilds[1] = guild
guild.me = fd.Member(999, "Buttonland", guild)
guild.me.bot = True
for role_id in [config.SUPPORT_ROLE, config.MOD_ROLE, config.OWNER_ROLE, config.BUYER_ROLE, config.HEAD_MOD_ROLE]:
    guild.roles[role_id] = fd.Role(role_id)

support = fd.TextChannel(guild, config.SUPPORT_CHANNEL_ID, "support")
bot.channels[support.id] = support
guild.text_channels.append(support)

customer = fd.Member(50, "bo", guild)
other = fd.Member(51, "friend", guild)
staff = fd.Member(60, "Sam", guild, roles=[guild.roles[config.SUPPORT_ROLE]])
mod = fd.Member(61, "Mia", guild, roles=[guild.roles[config.MOD_ROLE]])
for m in (customer, other, staff, mod):
    guild.members[m.id] = m


class FakeAgent:
    available = True

    def __init__(self):
        self.calls = []
        self.memory = {}
        self.codes_sent = {}
        self.next = {"text": "hey! checking that for you."}

    def remember(self, cid, role, text):
        self.memory.setdefault(cid, []).append((role, text))

    def forget(self, cid):
        self.memory.pop(cid, None)

    async def respond(self, ctx, text):
        self.calls.append((ctx, text))
        result, self.next = self.next, {"text": "ok!"}
        return result


calls = []


async def fake_start(uid, name, email):
    calls.append(("start", email))
    return "If that email has orders, a code is on its way."


async def fake_confirm(uid, name, code):
    calls.append(("confirm", code))
    if code != "123456":
        raise buttonland.ButtonlandError("That code isn't right.", 400)
    linked.add(uid)
    return {"email": "bo•••@example.com", "orders": [{"orderNumber": "BL-1", "statusText": "Shipped", "total": 20}]}


linked = set()


async def fake_customer(uid):
    return {"linked": uid in linked, "orders": [{"orderNumber": "BL-1"}] if uid in linked else []}


async def fake_close():
    pass


buttonland.start_verification = fake_start
buttonland.confirm_verification = fake_confirm
buttonland.customer = fake_customer
buttonland.close = fake_close
T.buttonland = buttonland


def msg(channel, author, content):
    m = fd.Message(channel, author, content)
    channel.messages.append(m)
    return m


def texts(channel):
    out = []
    for m in channel.messages:
        out.append(m.content or (m.embeds[0].title if m.embeds else ""))
    return out


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def main():
    cog = T.Tickets(bot)
    cog.agent = FakeAgent()

    # ---- panel
    await cog.on_ready()
    panel = [m for m in support.messages if m.view][-1]
    assert panel.embeds[0].title == "How can we help?"
    ids = [b.custom_id for b in panel.view.children]
    assert ids == ["tk:open:order", "tk:open:product", "tk:open:returns", "tk:open:other"]
    queue = next(c for c in guild.text_channels if c.name == "ticket-queue")
    logs = next(c for c in guild.text_channels if c.name == "ticket-logs")

    # The server doesn't let this customer type in threads: the bot fixes it per ticket.
    support.blocked_thread_send = {customer.id}

    # ---- open: button -> form -> thread
    i = fd.Interaction(customer, guild, support, "tk:open:order")
    await cog.on_interaction(i)
    assert isinstance(i.modal, T.TicketForm)
    form = i.modal
    values = {"Email": "bo@example.com", "Order number": "BL-1", "What's going on": "tracking hasn't moved"}
    for key, field in form.inputs:
        field.value = values[key]
    i2 = fd.Interaction(customer, guild, support)
    await form.on_submit(i2)
    await settle()
    assert "your ticket is open" in i2.sent[-1].content
    thread = support.threads[-1]
    ticket = database.get_ticket_by_thread(thread.id)
    assert thread.name.startswith("📦 0001") and customer.id in thread.members
    card = thread.messages[0]
    assert card.pinned and "Ticket #0001" in card.embeds[0].title and "AI assistant is helping" in card.embeds[0].description
    assert ("start", "bo@example.com") in calls, "verification code sent from the form"
    assert support.permissions_for(customer).send_messages_in_threads, "customer can type in their ticket"
    assert database.get_setting(f"typing_grant_{ticket['id']}") == str(customer.id)
    ctx, first = cog.agent.calls[0]
    assert ctx["topic_label"] == "Order help" and ctx["form"]["Order number"] == "BL-1" and "tracking" in first
    assert "hey! checking" in texts(thread)[-1]
    qcard = [m for m in queue.messages if not m.deleted][-1]
    assert "#0001" in qcard.embeds[0].title and [b.label for b in qcard.view.children] == ["Claim", "Jump"]

    # ---- second ticket refused while one is open; blacklist refused
    i3 = fd.Interaction(customer, guild, support, "tk:open:product")
    await cog.on_interaction(i3)
    assert i3.modal is None and "already have a ticket" in i3.sent[-1].content

    # ---- verification code fast path
    await cog.on_message(msg(thread, customer, "123 456"))
    assert ("confirm", "123456") in calls and "verified" in texts(thread)[-1]
    assert guild.roles[config.BUYER_ROLE] in customer.roles

    # ---- already verified: sending the email again doesn't send another code
    starts = len([c for c in calls if c[0] == "start"])
    await cog.on_message(msg(thread, customer, "bo@example.com"))
    assert len([c for c in calls if c[0] == "start"]) == starts and "already verified" in texts(thread)[-1]

    # ---- customer asks for a human -> needs staff + one ping in queue
    await cog.on_message(msg(thread, customer, "can i talk to a human please"))
    t = database.get_ticket(ticket["id"])
    assert t["status"] == "needs_staff" and t["ai_paused"]
    pings = [m for m in queue.messages if m.content and "needs a person" in m.content and not m.deleted]
    assert len(pings) == 1
    n_calls = len(cog.agent.calls)
    await cog.on_message(msg(thread, customer, "hello??"))
    assert len(cog.agent.calls) == n_calls, "AI stays quiet while waiting for staff"

    # ---- non-staff can't claim; staff claims from the queue
    i4 = fd.Interaction(customer, guild, queue, f"tk:claim:{ticket['id']}")
    await cog.on_interaction(i4)
    assert "only staff" in i4.sent[-1].content
    i5 = fd.Interaction(staff, guild, queue, f"tk:claim:{ticket['id']}")
    await cog.on_interaction(i5)
    t = database.get_ticket(ticket["id"])
    assert t["claimed_by"] == str(staff.id) and t["status"] == "claimed" and staff.id in thread.members
    assert pings[0].deleted, "ping cleared once claimed"
    assert "Claimed by" in card.embeds[0].description and [b.label for b in card.view.children] == ["Add person", "Close"]
    i6 = fd.Interaction(mod, guild, queue, f"tk:claim:{ticket['id']}")
    await cog.on_interaction(i6)
    assert "already claimed" in i6.sent[-1].content

    # ---- staff reply recorded; shows "waiting on customer"
    await cog.on_message(msg(thread, staff, "opened a trace with USPS"))
    t = database.get_ticket(ticket["id"])
    assert t["staff_replied"] and t["first_staff_reply_at"]
    assert "Waiting on customer" in card.embeds[0].description

    # ---- add / remove people
    i7 = fd.Interaction(staff, guild, thread)
    out = await cog.add_person(ticket["id"], other, staff)
    assert other.id in thread.members and "added" in out
    await cog.cmd_remove(fd.Interaction(staff, guild, thread), other)
    assert other.id not in thread.members
    r = fd.Interaction(staff, guild, thread)
    await cog.cmd_remove(r, customer)
    assert "close the ticket instead" in r.sent[-1].content

    # ---- close -> transcript to logs + DM with rating, thread locked, queue card gone
    i8 = fd.Interaction(other, guild, thread, f"tk:close:{ticket['id']}")
    await cog.on_interaction(i8)
    assert "only the customer or staff" in i8.sent[-1].content
    i9 = fd.Interaction(customer, guild, thread, f"tk:close:{ticket['id']}")
    await cog.on_interaction(i9)
    assert isinstance(i9.modal, T.CloseForm)
    i9.modal.reason.value = "Solved"
    i10 = fd.Interaction(customer, guild, thread)
    await i9.modal.on_submit(i10)
    t = database.get_ticket(ticket["id"])
    assert t["status"] == "closed" and t["close_reason"] == "Solved"
    assert thread.archived and thread.locked
    assert qcard.deleted
    log_file = [m for m in logs.messages if m.file][-1]
    html_text = log_file.file.data.decode()
    assert log_file.file.filename == "ticket-0001-transcript.html"
    assert "opened a trace with USPS" in html_text and "STAFF" in html_text and "Ticket #0001" in html_text
    assert "<script" not in html_text
    dm = customer.dms[-1]
    assert dm.file.filename == "ticket-0001-transcript.html"
    assert [b.custom_id for b in dm.view.children] == [f"tk:rate:{ticket['id']}:{n}" for n in range(1, 6)]
    assert card.view is None, "buttons removed from the closed card"
    assert customer.id not in getattr(support, "overwrites", {}), "temporary permission removed on close"

    # ---- closing twice does nothing
    before = len(logs.messages)
    await cog.close_ticket(ticket["id"], staff, "again")
    assert len(logs.messages) == before

    # ---- rating: only the customer, once
    dm_msg = fd.Message(fd.TextChannel(guild, 7, "dm"), bot.user)
    i11 = fd.Interaction(other, None, None, f"tk:rate:{ticket['id']}:5", message=dm_msg)
    await cog.on_interaction(i11)
    assert "only the customer" in i11.sent[-1].content
    i12 = fd.Interaction(customer, None, None, f"tk:rate:{ticket['id']}:4", message=dm_msg)
    await cog.on_interaction(i12)
    assert database.get_ticket(ticket["id"])["rating"] == 4
    i12.modal.comment.value = "quick and helpful"
    i13 = fd.Interaction(customer, None, None, message=dm_msg)
    await i12.modal.on_submit(i13)
    assert database.get_ticket(ticket["id"])["rating_comment"] == "quick and helpful"
    assert any(m.embeds and "⭐⭐⭐⭐☆" in m.embeds[0].title for m in logs.messages)
    i14 = fd.Interaction(customer, None, None, f"tk:rate:{ticket['id']}:1", message=dm_msg)
    await cog.on_interaction(i14)
    assert "already rated" in i14.sent[-1].content and database.get_ticket(ticket["id"])["rating"] == 4

    # ---- blacklist (mods+), blocks opening
    r = fd.Interaction(staff, guild, support)
    await cog.cmd_blacklist(r, other, "spam")
    assert "mods and up" in r.sent[-1].content
    r = fd.Interaction(mod, guild, support)
    await cog.cmd_blacklist(r, staff, "x")
    assert "can't blacklist staff" in r.sent[-1].content
    await cog.cmd_blacklist(fd.Interaction(mod, guild, support), other, "spam tickets")
    i15 = fd.Interaction(other, guild, support, "tk:open:other")
    await cog.on_interaction(i15)
    assert i15.modal is None and "can't open tickets" in i15.sent[-1].content
    await cog.cmd_unblacklist(fd.Interaction(mod, guild, support), other)
    i16 = fd.Interaction(other, guild, support, "tk:open:other")
    await cog.on_interaction(i16)
    assert isinstance(i16.modal, T.TicketForm)

    # ---- inactivity: warn at 24h, keep-open button resets, close at 48h
    i17 = fd.Interaction(other, guild, support)
    for key, field in i16.modal.inputs:
        field.value = {"Subject": "hi", "Details": "question"}[key]
    await i16.modal.on_submit(i17)
    await settle()
    t2 = database.get_ticket_by_thread(support.threads[-1].id)
    thread2 = support.threads[-1]
    assert t2["topic"] == "other" and ("start", None) not in calls
    day = 24 * 3600
    database.update_ticket(t2["id"], last_customer_at=time.time() - day - 60, last_reply_at=time.time() - day - 30)
    await cog.housekeeping()
    t2 = database.get_ticket(t2["id"])
    assert t2["warned_at"] and "still need help" in texts(thread2)[-1]
    keep = fd.Interaction(other, guild, thread2, f"tk:keep:{t2['id']}")
    await cog.on_interaction(keep)
    assert database.get_ticket(t2["id"])["warned_at"] is None and "keeping" in keep.edited.content
    database.update_ticket(t2["id"], last_customer_at=time.time() - 2 * day, last_reply_at=time.time() - 2 * day + 10)
    await cog.housekeeping()
    database.update_ticket(t2["id"], warned_at=time.time() - day - 1)
    await cog.housekeeping()
    t2 = database.get_ticket(t2["id"])
    assert t2["status"] == "closed" and t2["close_reason"] == "No reply for 48 hours" and thread2.locked

    # ---- claimed tickets never auto-close
    i18 = fd.Interaction(other, guild, support, "tk:open:product")
    await cog.on_interaction(i18)
    for key, field in i18.modal.inputs:
        field.value = {"Product": "grip", "Needs to fit": "hi-capa", "Question": "fit?"}[key]
    await i18.modal.on_submit(fd.Interaction(other, guild, support))
    await settle()
    t3 = database.get_ticket_by_thread(support.threads[-1].id)
    await cog.claim(t3["id"], staff)
    database.update_ticket(t3["id"], last_customer_at=time.time() - 5 * day, last_reply_at=time.time() - 4 * day)
    await cog.housekeeping()
    assert database.get_ticket(t3["id"])["status"] == "claimed" and not database.get_ticket(t3["id"])["warned_at"]

    # ---- /ticket ai on/off and stats
    thread3 = support.threads[-1]
    a = fd.Interaction(staff, guild, thread3)
    await cog.cmd_ai(a, fd.Choice("on", "on"))
    assert not database.get_ticket(t3["id"])["ai_paused"]
    s = fd.Interaction(staff, guild, support)
    await cog.cmd_stats(s, None)
    stats = s.sent[-1].embed
    fields = {f.name: f.value for f in stats.fields}
    assert fields["Opened"] == "3" and fields["Rating"].startswith("4.0")
    assert "Open now" in fields
    s2 = fd.Interaction(customer, guild, support)
    await cog.cmd_stats(s2, None)
    assert "staff only" in s2.sent[-1].content

    # ---- panel status line
    assert "ticket" in cog.panel_status() and "Staff usually reply" in cog.panel_status()

    # ---- deleted thread gets cleaned up
    support.guild.client.channels.pop(thread3.id)
    await cog.housekeeping()
    assert database.get_ticket(t3["id"])["status"] == "closed"

    # ---- legacy buttons
    old = fd.Interaction(customer, guild, support, "open_ticket")
    await cog.on_interaction(old)
    assert "panel is old" in old.sent[-1].content

    print("tickets v2: all checks passed")


asyncio.run(main())
