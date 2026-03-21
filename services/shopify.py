import requests
import os
import time

SHOP = os.getenv("SHOPIFY_STORE")  # JUST store name, no .myshopify.com
CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

BASE_URL = f"https://{SHOP}.myshopify.com/admin/api/2024-01"

shopify_token = None
token_expires_at = 0


# ===============================
# 🔐 TOKEN (CLIENT CREDENTIALS)
# ===============================
def get_shopify_token():
    global shopify_token, token_expires_at

    if shopify_token and time.time() < token_expires_at:
        return shopify_token

    print("🔄 Fetching Shopify token...")

    url = f"https://{SHOP}.myshopify.com/admin/oauth/access_token"

    res = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )

    if res.status_code != 200:
        print("❌ TOKEN ERROR:", res.text)
        raise Exception("Failed to get Shopify token")

    data = res.json()

    shopify_token = data["access_token"]
    token_expires_at = time.time() + (data.get("expires_in", 3600) - 60)

    print("✅ Shopify token refreshed")

    return shopify_token


# ===============================
# 🔄 SYNC ORDERS
# ===============================
def sync_orders():
    print("🔄 Syncing orders...")

    token = get_shopify_token()

    all_orders = []
    url = f"{BASE_URL}/orders.json?limit=50&status=any"

    while url:
        res = requests.get(url, headers={
            "X-Shopify-Access-Token": token
        })

        if res.status_code != 200:
            print("❌ ORDER ERROR:", res.text)
            return []

        data = res.json()
        orders = data.get("orders", [])
        all_orders.extend(orders)

        link = res.headers.get("link")

        if link and 'rel="next"' in link:
            import re
            match = re.search(r"<([^>]+)>; rel=\"next\"", link)
            url = match.group(1) if match else None
        else:
            url = None

    print(f"✅ Synced {len(all_orders)} orders")

    return all_orders


# ===============================
# 📦 TRACKING
# ===============================
def get_tracking_info(order):
    fulfillments = order.get("fulfillments", [])

    for f in fulfillments:
        if f.get("tracking_number"):
            return {
                "company": f.get("tracking_company"),
                "number": f.get("tracking_number"),
                "url": f.get("tracking_url")
            }

        if f.get("tracking_numbers"):
            return {
                "company": f.get("tracking_company"),
                "number": f["tracking_numbers"][0],
                "url": f.get("tracking_url")
            }

    return None


# ===============================
# ⚡ INCREMENTAL SYNC (NEW)
# ===============================
def sync_recent_orders(minutes=10):
    print(f"⚡ Syncing recent orders (last {minutes} min)...")

    token = get_shopify_token()

    import time
    import re

    since_time = time.strftime(
        "%Y-%m-%dT%H:%M:%S",
        time.gmtime(time.time() - (minutes * 60))
    )

    url = f"{BASE_URL}/orders.json?limit=50&status=any&updated_at_min={since_time}"

    all_orders = []

    while url:
        try:
            res = requests.get(
                url,
                headers={"X-Shopify-Access-Token": token},
                timeout=10
            )

            if res.status_code != 200:
                print("❌ RECENT SYNC ERROR:", res.text)
                return []

            data = res.json()
            orders = data.get("orders", [])
            all_orders.extend(orders)

            link = res.headers.get("link")

            if link and 'rel="next"' in link:
                match = re.search(r"<([^>]+)>; rel=\"next\"", link)
                url = match.group(1) if match else None
            else:
                url = None

        except Exception as e:
            print("❌ RECENT SYNC EXCEPTION:", e)
            return []

    print(f"⚡ Synced {len(all_orders)} recent orders")
    return all_orders