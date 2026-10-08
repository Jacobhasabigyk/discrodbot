import os
from dotenv import load_dotenv

# Settings on the host (e.g. Render's Environment tab) win over .env.
load_dotenv()

# ================================
# Discord
# ================================
TOKEN = os.getenv("DISCORD_TOKEN")

OWNER_ROLE = 1459718191344259155
HEAD_MOD_ROLE = 1471268870885998632
MOD_ROLE = 1468440661153026059
SUPPORT_ROLE = 1475784384404783176
BUYER_ROLE = 1459718958931513354

LOG_CHANNEL = 1480454191666429952

# #support: the ticket panel lives here and tickets are private threads in it.
SUPPORT_CHANNEL_ID = 1476797889509593211

# ================================
# Buttonland store API (replaces Shopify)
# ================================
# Base URL of the Buttonland backend, e.g. https://api.buttonland.store/api
BUTTONLAND_API_URL = os.getenv("BUTTONLAND_API_URL", "").rstrip("/")
# Same value as BOT_API_KEY in the backend's .env (at least 32 characters).
BUTTONLAND_BOT_KEY = os.getenv("BUTTONLAND_BOT_KEY", "")

# ================================
# Support AI (Claude)
# ================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-haiku-5-5")
