import requests
import os

SHOP = os.getenv("SHOPIFY_STORE")
CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

def get_access_token():
    url = f"https://{SHOP}/admin/oauth/access_token"

    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials"
    }

    res = requests.post(url, json=payload)

    if res.status_code != 200:
        print("Shopify auth failed:", res.text)
        return None

    return res.json().get("access_token")