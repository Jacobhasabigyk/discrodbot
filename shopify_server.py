from flask import Flask, redirect, request
import requests
import os

from database import save_shopify_token

app = Flask(__name__)

CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
STORE = os.getenv("SHOPIFY_STORE")
REDIRECT_URI = os.getenv("SHOPIFY_REDIRECT_URI")

SCOPES = "read_orders,read_fulfillments,write_refunds"


# ===============================
# 🔗 INSTALL
# ===============================
@app.route("/auth/install")
def install():

    url = f"https://{STORE}/admin/oauth/authorize"

    return redirect(
        f"{url}?client_id={CLIENT_ID}&scope={SCOPES}&redirect_uri={REDIRECT_URI}"
    )


# ===============================
# 🔁 CALLBACK
# ===============================
@app.route("/auth/callback")
def callback():

    code = request.args.get("code")

    if not code:
        return "❌ No code"

    token_url = f"https://{STORE}/admin/oauth/access_token"

    res = requests.post(token_url, json={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code
    })

    data = res.json()

    access_token = data.get("access_token")

    if not access_token:
        return f"❌ Failed: {data}"

    # 💾 SAVE TO DATABASE
    save_shopify_token(STORE, access_token)

    return "✅ Shopify connected successfully! You can close this."


# ===============================
# ▶️ RUN SERVER
# ===============================
def run_server():
    app.run(port=5000)