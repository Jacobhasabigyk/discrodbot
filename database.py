import sqlite3

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
# 🎟 TICKETS (survive bot restarts)
# ===============================
cursor.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    channel_id TEXT PRIMARY KEY,
    owner_id TEXT,
    ai_paused INTEGER DEFAULT 0,
    created_at REAL
)
""")

# ===============================
# 🧹 Shopify is gone. Remove its old data: the saved Shopify access token,
# the cached Shopify orders, and emails "verified" against Shopify orders.
# Customers now verify against the Buttonland store itself.
# ===============================
cursor.execute("DROP TABLE IF EXISTS shopify")
cursor.execute("DROP TABLE IF EXISTS orders")
cursor.execute("DROP TABLE IF EXISTS verified")

conn.commit()


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
# 🎟 TICKET FUNCTIONS
# ===============================
def save_ticket(channel_id, owner_id, created_at):
    cursor.execute(
        "INSERT OR REPLACE INTO tickets (channel_id, owner_id, ai_paused, created_at) VALUES (?, ?, 0, ?)",
        (str(channel_id), str(owner_id), created_at)
    )
    conn.commit()


def get_ticket(channel_id):
    cursor.execute(
        "SELECT owner_id, ai_paused FROM tickets WHERE channel_id=?",
        (str(channel_id),)
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {"owner_id": int(row[0]), "ai_paused": bool(row[1])}


def get_open_ticket_for(owner_id):
    cursor.execute(
        "SELECT channel_id FROM tickets WHERE owner_id=?",
        (str(owner_id),)
    )
    return [int(row[0]) for row in cursor.fetchall()]


def set_ticket_paused(channel_id, paused):
    cursor.execute(
        "UPDATE tickets SET ai_paused=? WHERE channel_id=?",
        (1 if paused else 0, str(channel_id))
    )
    conn.commit()


def delete_ticket(channel_id):
    cursor.execute("DELETE FROM tickets WHERE channel_id=?", (str(channel_id),))
    conn.commit()
