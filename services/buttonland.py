"""
Client for the Buttonland store backend (replaces Shopify).

Every call goes to the backend's /api/bot endpoints with the shared bot key.
Customers only ever see orders for an email they proved they own (code sent
to that email); refund/cancel requests go to the owner's review queue and
never move money by themselves.
"""

import time

import aiohttp

from config import BUTTONLAND_API_URL, BUTTONLAND_BOT_KEY


class ButtonlandError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.message = message
        self.status = status


_session = None
_store_cache = {"at": 0, "data": None}


def is_configured():
    return bool(BUTTONLAND_API_URL and len(BUTTONLAND_BOT_KEY) >= 32)


async def _get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    return _session


async def close():
    if _session and not _session.closed:
        await _session.close()


async def _call(method, path, *, json=None, params=None):
    if not is_configured():
        raise ButtonlandError("The store connection isn't set up (BUTTONLAND_API_URL / BUTTONLAND_BOT_KEY).", 503)

    session = await _get_session()
    url = f"{BUTTONLAND_API_URL}/bot{path}"
    headers = {"X-Bot-Key": BUTTONLAND_BOT_KEY, "ngrok-skip-browser-warning": "1"}

    try:
        async with session.request(method, url, json=json, params=params, headers=headers) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                data = {}

            data = data if isinstance(data, dict) else {}

            if response.status >= 400:
                raise ButtonlandError(data.get("message") or f"Store API error {response.status}", response.status)

            return data
    except ButtonlandError:
        raise
    except Exception as error:
        raise ButtonlandError("Couldn't reach the store right now.", 0) from error


# ------------------------------------------------------
# Store facts (live, cached 5 minutes)
# ------------------------------------------------------

async def store_info():
    if _store_cache["data"] and time.time() - _store_cache["at"] < 300:
        return _store_cache["data"]
    data = await _call("GET", "/store")
    _store_cache.update(at=time.time(), data=data.get("store") or {})
    return _store_cache["data"]


async def search_products(query):
    data = await _call("GET", "/products", params={"q": str(query or "")[:80]})
    return data.get("products") or []


# ------------------------------------------------------
# Customer verification + their own orders
# ------------------------------------------------------

async def start_verification(discord_user_id, discord_name, email):
    data = await _call("POST", "/verify/start", json={
        "discordUserId": str(discord_user_id),
        "discordName": str(discord_name)[:80],
        "email": email,
    })
    return data.get("message") or "If that email has orders, a code is on its way."


async def confirm_verification(discord_user_id, discord_name, code):
    return await _call("POST", "/verify/confirm", json={
        "discordUserId": str(discord_user_id),
        "discordName": str(discord_name)[:80],
        "code": code,
    })


async def customer(discord_user_id):
    return await _call("GET", f"/customers/{discord_user_id}")


async def unlink(discord_user_id):
    return await _call("DELETE", f"/customers/{discord_user_id}/link")


async def create_request(discord_user_id, discord_name, channel_name, order_number, kind, reason, amount=None):
    body = {
        "orderNumber": order_number,
        "type": "cancel" if kind in ("cancel", "cancellation") else "refund",
        "reason": reason,
        "discordName": str(discord_name)[:80],
        "channelName": str(channel_name)[:80],
    }
    if amount is not None:
        body["amount"] = amount
    return await _call("POST", f"/customers/{discord_user_id}/requests", json=body)


# ------------------------------------------------------
# Staff commands
# ------------------------------------------------------

async def staff_order(order_number):
    data = await _call("GET", f"/staff/orders/{order_number}")
    return data.get("order")


async def staff_search(query):
    data = await _call("GET", "/staff/search", params={"q": query})
    return data.get("orders") or []


# ------------------------------------------------------
# Giveaways (owner / head mod commands + Enter / Claim buttons)
# ------------------------------------------------------

async def giveaway_prizes(query=""):
    data = await _call("GET", "/giveaways/prizes", params={"q": query[:60]})
    return data.get("prizes", [])


async def create_giveaway(**fields):
    data = await _call("POST", "/giveaways", json=fields)
    return data.get("giveaway")


async def list_giveaways(status=""):
    data = await _call("GET", "/giveaways", params={"status": status} if status else None)
    return data.get("giveaways", [])


async def get_giveaway(giveaway_id):
    data = await _call("GET", f"/giveaways/{giveaway_id}")
    return data.get("giveaway")


async def set_giveaway_message(giveaway_id, message_id):
    await _call("PATCH", f"/giveaways/{giveaway_id}/message", json={"messageId": str(message_id)})


async def enter_giveaway(giveaway_id, discord_user_id, discord_name):
    return await _call("POST", f"/giveaways/{giveaway_id}/entries", json={"discordUserId": str(discord_user_id), "discordName": str(discord_name)[:80]})


async def leave_giveaway(giveaway_id, discord_user_id):
    return await _call("DELETE", f"/giveaways/{giveaway_id}/entries/{discord_user_id}")


async def end_giveaway(giveaway_id, actor_name=""):
    return await _call("POST", f"/giveaways/{giveaway_id}/end", json={"actorName": actor_name})


async def cancel_giveaway(giveaway_id, actor_name=""):
    return await _call("POST", f"/giveaways/{giveaway_id}/cancel", json={"actorName": actor_name})


async def giveaway_announced(giveaway_id, message_id=None):
    await _call("POST", f"/giveaways/{giveaway_id}/announced", json={"messageId": str(message_id or "")})


async def giveaway_claim_link(giveaway_id, discord_user_id):
    return await _call("POST", f"/giveaways/{giveaway_id}/claim-link", json={"discordUserId": str(discord_user_id)})


async def giveaway_work():
    return await _call("POST", "/giveaways/work", json={})


async def giveaway_dm_done(winner_id, ok, kind):
    await _call("POST", f"/giveaways/winners/{winner_id}/dm", json={"ok": bool(ok), "kind": kind})


async def giveaway_expired_noted(winner_id):
    await _call("POST", f"/giveaways/winners/{winner_id}/expired-noted", json={})


async def giveaway_claim_noted(winner_id):
    await _call("POST", f"/giveaways/winners/{winner_id}/claim-noted", json={})


async def giveaway_reroll_announced(winner_id):
    await _call("POST", f"/giveaways/winners/{winner_id}/reroll-announced", json={})


async def reroll_winner(winner_id, actor_name=""):
    return await _call("POST", f"/giveaways/winners/{winner_id}/reroll", json={"actorName": actor_name})


async def extend_winner(winner_id, actor_name=""):
    return await _call("POST", f"/giveaways/winners/{winner_id}/extend", json={"actorName": actor_name})


async def send_giveaway_link(discord_user_id, giveaway_id="", actor_name=""):
    return await _call("POST", "/giveaways/sendlink", json={"discordUserId": str(discord_user_id), "giveawayId": giveaway_id or "", "actorName": actor_name})


async def award_prize(**fields):
    return await _call("POST", "/giveaways/award", json=fields)
