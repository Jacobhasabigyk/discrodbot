"""Offline test of the store API client (fake HTTP session)."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BUTTONLAND_API_URL"] = "https://api.example.com/api/"
os.environ["BUTTONLAND_BOT_KEY"] = "k" * 40

seen = []


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def json(self, content_type=None):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    closed = False

    def __init__(self, *a, **kw):
        pass

    def request(self, method, url, json=None, params=None, headers=None):
        seen.append((method, url, json, params, headers))
        if url.endswith("/bot/store"):
            return FakeResponse(200, {"success": True, "store": {"returnWindowDays": 14}})
        if "/verify/confirm" in url:
            return FakeResponse(400, {"success": False, "message": "That code isn't right."})
        return FakeResponse(200, {"success": True, "message": "ok"})

    async def close(self):
        self.closed = True


stub = types.ModuleType("aiohttp")
stub.ClientSession = FakeSession
stub.ClientTimeout = lambda **kw: None
sys.modules["aiohttp"] = stub

from services import buttonland  # noqa: E402


async def main():
    store = await buttonland.store_info()
    assert store["returnWindowDays"] == 14
    await buttonland.store_info()
    assert len(seen) == 1, "store info is cached"
    method, url, _, _, headers = seen[0]
    assert url == "https://api.example.com/api/bot/store" and headers["X-Bot-Key"] == "k" * 40

    try:
        await buttonland.confirm_verification(1, "bo", "000000")
        raise AssertionError("expected error")
    except buttonland.ButtonlandError as error:
        assert error.status == 400 and "isn't right" in error.message

    await buttonland.create_request(5, "bo", "ticket-bo", "BL-1", "cancellation", "wrong size")
    assert seen[-1][2]["type"] == "cancel" and "amount" not in seen[-1][2]
    print("buttonland client: all checks passed")


asyncio.run(main())
