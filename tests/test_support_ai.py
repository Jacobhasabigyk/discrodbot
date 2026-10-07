"""
Offline tests for the ticket assistant (no Discord, no network).
Run: python -m tests.test_support_ai   (from the bot folder)
"""
import asyncio
import os
import sys
import types
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# aiohttp is only needed for real HTTP; stub it so this runs anywhere.
if "aiohttp" not in sys.modules:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        stub = types.ModuleType("aiohttp")
        stub.ClientSession = object
        stub.ClientTimeout = lambda **kw: None
        sys.modules["aiohttp"] = stub

from services.buttonland import ButtonlandError  # noqa: E402
from services.support_ai import SupportAgent, build_system_prompt  # noqa: E402

STORE = {
    "returnPolicy": "Returns within 14 days of delivery for unused items in their original condition.",
    "shippingPolicy": "Ships to the 50 US states only. Orders ship in 2–3 business days.",
    "shippingMethods": [{"name": "Standard Shipping", "price": 5.99, "estimatedDays": "5–8 business days"}],
    "freeShippingThreshold": 100,
    "supportEmail": "management@buttonland.store",
    "website": "https://buttonland.store",
}

ORDER = {"orderNumber": "BL-20261007-7F3A2C", "statusText": "Shipped", "shipments": [{"trackingNumber": "9400", "trackingUrl": "https://t"}]}


class FakeAPI:
    def __init__(self):
        self.linked = False
        self.calls = []

    async def store_info(self):
        return STORE

    async def search_products(self, query):
        self.calls.append(("search", query))
        return []

    async def customer(self, user_id):
        self.calls.append(("customer", user_id))
        return {"linked": self.linked, "email": "bo•••@example.com", "orders": [ORDER] if self.linked else []}

    async def start_verification(self, user_id, name, email):
        self.calls.append(("start", email))
        return "If that email has orders, a code is on its way."

    async def confirm_verification(self, user_id, name, code):
        self.calls.append(("confirm", code))
        if code != "123456":
            raise ButtonlandError("That code isn't right.", 400)
        self.linked = True
        return {"email": "bo•••@example.com", "orders": [ORDER]}

    async def create_request(self, user_id, name, channel, order_number, kind, reason, amount=None):
        self.calls.append(("request", order_number, kind, reason, amount))
        return {"message": "sent to the owner", "request": {"orderNumber": order_number, "type": kind, "amount": 15.99}}


def text(t):
    return NS(type="text", text=t)


def tool(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


class FakeClaude:
    """Returns scripted responses and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        blocks, stop = self.script.pop(0)
        return NS(content=blocks, stop_reason=stop)


def check_alternation(messages):
    assert messages[0]["role"] == "user", messages[0]
    for a, b in zip(messages, messages[1:]):
        assert a["role"] != b["role"], (a, b)


async def main():
    ticket = {"channel_id": 1, "user_id": 42, "user_name": "bo", "channel_name": "ticket-bo"}

    # 1. Policies come from the live store, not a stale file.
    prompt = build_system_prompt(STORE, ticket)
    assert "14 days" in prompt and "2–3 business days" in prompt and "management@buttonland.store" in prompt
    assert "30 day" not in prompt and "josh@" not in prompt
    assert "Label created" in prompt and "NOT shipped" in prompt

    # 2. "where's my order" -> must look it up; unverified -> asks for email.
    api = FakeAPI()
    claude = FakeClaude([
        ([tool("t1", "get_my_orders", {})], "tool_use"),
        ([text("what email did you order with? i'll send you a code to confirm it's you.")], "end_turn"),
    ])
    agent = SupportAgent(client=claude, api=api)
    out = await agent.respond(ticket, "where is my order??")
    assert "email" in out["text"]
    assert ("customer", 42) in api.calls
    tool_result = claude.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and '"verified": false' in tool_result["content"]
    check_alternation(claude.requests[1]["messages"])

    # 3. Email -> code -> verified -> order status from real data.
    claude.script = [
        ([tool("t2", "send_verification_code", {"email": "bo@example.com"})], "tool_use"),
        ([text("sent! paste the 6 digit code here.")], "end_turn"),
        ([tool("t3", "check_verification_code", {"code": "000000"})], "tool_use"),
        ([text("that code didn't work, double check it?")], "end_turn"),
        ([tool("t4", "check_verification_code", {"code": "123456"})], "tool_use"),
        ([text("you're verified! BL-20261007-7F3A2C has shipped, tracking: https://t")], "end_turn"),
    ]
    out = await agent.respond(ticket, "bo@example.com")
    assert out.get("verification_started")
    out = await agent.respond(ticket, "it's 000000")
    assert not out.get("verified")
    assert "isn't right" in claude.requests[-1]["messages"][-1]["content"][0]["content"]
    out = await agent.respond(ticket, "oh sorry 123456")
    assert out.get("verified") and "BL-20261007-7F3A2C" in out["text"]
    check_alternation(claude.requests[-1]["messages"])

    # 4. Refund: files a real request (owner reviews), tool results reach the model.
    claude.script = [
        ([text("checking"), tool("t5", "file_refund_or_cancel_request", {"order_number": "BL-20261007-7F3A2C", "type": "refund", "reason": "arrived broken"})], "tool_use"),
        ([text("done, i sent your refund request to the owner. nothing's been refunded yet, they'll review it.")], "end_turn"),
    ]
    out = await agent.respond(ticket, "yes please refund it, it arrived broken")
    assert out["request_filed"]["orderNumber"] == "BL-20261007-7F3A2C"
    assert ("request", "BL-20261007-7F3A2C", "refund", "arrived broken", None) in api.calls

    # 5. Hand-off.
    claude.script = [
        ([tool("t6", "hand_off_to_staff", {"reason": "package marked delivered but not received"})], "tool_use"),
        ([text("i'm getting a team member to look into this with you.")], "end_turn"),
    ]
    out = await agent.respond(ticket, "it says delivered but i never got it")
    assert out["handoff"] == "package marked delivered but not received"

    # 6. Store errors become tool errors (the model can explain), not crashes.
    class DownAPI(FakeAPI):
        async def customer(self, user_id):
            raise ButtonlandError("Couldn't reach the store right now.", 0)

    claude2 = FakeClaude([
        ([tool("x", "get_my_orders", {})], "tool_use"),
        ([text("the store's not answering right now, try again in a bit or ask for a human.")], "end_turn"),
    ])
    agent2 = SupportAgent(client=claude2, api=DownAPI())
    out = await agent2.respond({**ticket, "channel_id": 2}, "order status?")
    assert "Couldn't reach" in claude2.requests[1]["messages"][-1]["content"][0]["content"]

    # 7. Endless tool loop is cut off and handed to staff.
    loop = FakeClaude([([tool(f"l{i}", "get_store_info", {})], "tool_use") for i in range(10)])
    agent3 = SupportAgent(client=loop, api=FakeAPI())
    out = await agent3.respond({**ticket, "channel_id": 3}, "hi")
    assert out.get("handoff") and out["text"]
    assert len(loop.requests) == 6

    # 8. History stays bounded and valid.
    for i in range(40):
        agent.remember(9, "user" if i % 2 == 0 else "assistant", f"m{i}")
    history = agent.history(9)
    assert len(history) <= 24 and history[0]["role"] == "user"
    check_alternation(history)

    # 9. No API key -> no crash, hands off.
    agent4 = SupportAgent(client=None, api=FakeAPI())
    agent4.client = None
    out = await agent4.respond(ticket, "hello")
    assert out["handoff"]

    print("support_ai: all checks passed")


asyncio.run(main())
