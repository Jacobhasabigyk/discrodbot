import sqlite3
import time

conn = sqlite3.connect("warnings.db", check_same_thread=False)
cursor = conn.cursor()

# ===============================
# 💰 BALANCES
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS balances (
    user_id TEXT PRIMARY KEY,
    balance INTEGER
)
""")

# ===============================
# ⚠️ WARNINGS
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    reason TEXT
)
""")

# ===============================
# 🛍 SHOPIFY TOKENS
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS shopify (
    shop TEXT PRIMARY KEY,
    access_token TEXT
)
""")

# ===============================
# 🔒 VERIFIED USERS
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS verified (
    user_id TEXT PRIMARY KEY,
    email TEXT
)
""")

# ===============================
# 📦 ORDER CACHE (NEW 🔥)
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    order_number INTEGER PRIMARY KEY,
    email TEXT,
    fulfillment_status TEXT,
    tracking_company TEXT,
    tracking_number TEXT,
    tracking_url TEXT,
    updated_at REAL
)
""")

conn.commit()

# ===============================
# 🛍 SHOPIFY FUNCTIONS
# ===============================
def save_shopify_token(shop, token):
    cursor.execute(
        "INSERT OR REPLACE INTO shopify (shop, access_token) VALUES (?, ?)",
        (shop, token)
    )
    conn.commit()


def get_shopify_token(shop):
    cursor.execute(
        "SELECT access_token FROM shopify WHERE shop=?",
        (shop,)
    )
    row = cursor.fetchone()
    return row[0] if row else None


# ===============================
# 💰 BALANCE FUNCTIONS
# ===============================
def get_balance(user_id):
    cursor.execute("SELECT balance FROM balances WHERE user_id=?", (str(user_id),))
    row = cursor.fetchone()

    if not row:
        cursor.execute(
            "INSERT INTO balances (user_id, balance) VALUES (?, ?)",
            (str(user_id), 100)
        )
        conn.commit()
        return 100

    return row[0]


def update_balance(user_id, amount):
    current = get_balance(user_id)
    new_balance = current + amount

    cursor.execute(
        "UPDATE balances SET balance=? WHERE user_id=?",
        (new_balance, str(user_id))
    )
    conn.commit()

    return new_balance


# ===============================
# 🔒 VERIFIED USER FUNCTIONS
# ===============================
def save_verified_user(user_id, email):
    cursor.execute(
        "INSERT OR REPLACE INTO verified (user_id, email) VALUES (?, ?)",
        (str(user_id), email)
    )
    conn.commit()


def get_verified_user(user_id):
    cursor.execute(
        "SELECT email FROM verified WHERE user_id=?",
        (str(user_id),)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def remove_verified_user(user_id):
    cursor.execute(
        "DELETE FROM verified WHERE user_id=?",
        (str(user_id),)
    )
    conn.commit()


# ===============================
# 📦 ORDER CACHE FUNCTIONS (NEW 🔥)
# ===============================
def save_orders_to_db(orders, get_tracking_info):
    for o in orders:
        tracking = get_tracking_info(o) or {}

        cursor.execute("""
        INSERT OR REPLACE INTO orders (
            order_number, email, fulfillment_status,
            tracking_company, tracking_number, tracking_url, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            o.get("order_number"),
            o.get("email"),
            o.get("fulfillment_status"),
            tracking.get("company"),
            tracking.get("number"),
            tracking.get("url"),
            time.time()
        ))

    conn.commit()


def get_order_from_db(order_number):
    cursor.execute(
        "SELECT * FROM orders WHERE order_number=?",
        (order_number,)
    )
    row = cursor.fetchone()

    if not row:
        return None

    return {
        "order_number": row[0],
        "email": row[1],
        "fulfillment_status": row[2],
        "tracking": {
            "company": row[3],
            "number": row[4],
            "url": row[5]
        }
    }


def get_orders_by_email(email):
    cursor.execute(
        "SELECT * FROM orders WHERE email=?",
        (email,)
    )
    rows = cursor.fetchall()

    return [
        {
            "order_number": r[0],
            "email": r[1],
            "fulfillment_status": r[2],
            "tracking": {
                "company": r[3],
                "number": r[4],
                "url": r[5]
            }
        }
        for r in rows
    ]


def get_last_order_update():
    cursor.execute("SELECT MAX(updated_at) FROM orders")
    row = cursor.fetchone()
    return row[0] if row and row[0] else 0