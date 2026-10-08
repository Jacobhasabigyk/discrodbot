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
# 🎟 TICKETS v2 (private threads; survive restarts)
# The first version's table ("tickets") is replaced.
# ===============================
cursor.execute("DROP TABLE IF EXISTS tickets")
cursor.execute("""
CREATE TABLE IF NOT EXISTS tickets_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT,
    thread_id TEXT UNIQUE,
    owner_id TEXT,
    topic TEXT,
    form TEXT,
    status TEXT DEFAULT 'ai',
    claimed_by TEXT,
    handoff_reason TEXT,
    ai_paused INTEGER DEFAULT 0,
    card_message_id TEXT,
    queue_message_id TEXT,
    ping_message_id TEXT,
    created_at REAL,
    last_customer_at REAL,
    last_reply_at REAL,
    first_staff_reply_at REAL,
    staff_replied INTEGER DEFAULT 0,
    warned_at REAL,
    closed_at REAL,
    closed_by TEXT,
    close_reason TEXT,
    rating INTEGER,
    rating_comment TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS ticket_blacklist (
    user_id TEXT PRIMARY KEY,
    reason TEXT,
    by_id TEXT,
    at REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
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
# ⚙️ SETTINGS
# ===============================
def get_setting(key, default=None):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default


def set_setting(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()


# ===============================
# 🎟 TICKET FUNCTIONS
# ===============================
TICKET_FIELDS = [
    "id", "guild_id", "thread_id", "owner_id", "topic", "form", "status", "claimed_by",
    "handoff_reason", "ai_paused", "card_message_id", "queue_message_id", "ping_message_id",
    "created_at", "last_customer_at", "last_reply_at", "first_staff_reply_at", "staff_replied",
    "warned_at", "closed_at", "closed_by", "close_reason", "rating", "rating_comment",
]

_UPDATABLE = set(TICKET_FIELDS) - {"id"}


def _row(row):
    if not row:
        return None
    ticket = dict(zip(TICKET_FIELDS, row))
    ticket["ai_paused"] = bool(ticket["ai_paused"])
    ticket["staff_replied"] = bool(ticket["staff_replied"])
    return ticket


def _select(where, params=()):
    cursor.execute(f"SELECT {', '.join(TICKET_FIELDS)} FROM tickets_v2 WHERE {where}", params)
    return cursor.fetchall()


def create_ticket(guild_id, owner_id, topic, form_json, now):
    cursor.execute(
        "INSERT INTO tickets_v2 (guild_id, owner_id, topic, form, status, created_at, last_customer_at) VALUES (?, ?, ?, ?, 'ai', ?, ?)",
        (str(guild_id), str(owner_id), topic, form_json, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def get_ticket(ticket_id):
    rows = _select("id=?", (int(ticket_id),))
    return _row(rows[0]) if rows else None


def get_ticket_by_thread(thread_id):
    rows = _select("thread_id=?", (str(thread_id),))
    return _row(rows[0]) if rows else None


def open_tickets_for(owner_id):
    return [_row(r) for r in _select("owner_id=? AND status!='closed'", (str(owner_id),))]


def open_tickets(guild_id=None):
    if guild_id is None:
        return [_row(r) for r in _select("status!='closed'")]
    return [_row(r) for r in _select("status!='closed' AND guild_id=?", (str(guild_id),))]


def update_ticket(ticket_id, **fields):
    fields = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
    cursor.execute(f"UPDATE tickets_v2 SET {sets} WHERE id=?", (*values, int(ticket_id)))
    conn.commit()


def claim_ticket(ticket_id, staff_id):
    """Only one person can claim a ticket (two clicks at once: one wins)."""
    cursor.execute(
        "UPDATE tickets_v2 SET claimed_by=?, status='claimed', ai_paused=1 WHERE id=? AND claimed_by IS NULL AND status!='closed'",
        (str(staff_id), int(ticket_id)),
    )
    conn.commit()
    return cursor.rowcount == 1


def close_ticket_row(ticket_id, closed_by, reason, now):
    cursor.execute(
        "UPDATE tickets_v2 SET status='closed', closed_at=?, closed_by=?, close_reason=? WHERE id=? AND status!='closed'",
        (now, str(closed_by), reason, int(ticket_id)),
    )
    conn.commit()
    return cursor.rowcount == 1


def tickets_since(since):
    return [_row(r) for r in _select("created_at>=?", (since,))]


# ===============================
# 🚫 TICKET BLACKLIST
# ===============================
def blacklist_add(user_id, reason, by_id, now):
    cursor.execute(
        "INSERT OR REPLACE INTO ticket_blacklist (user_id, reason, by_id, at) VALUES (?, ?, ?, ?)",
        (str(user_id), reason, str(by_id), now),
    )
    conn.commit()


def blacklist_remove(user_id):
    cursor.execute("DELETE FROM ticket_blacklist WHERE user_id=?", (str(user_id),))
    conn.commit()
    return cursor.rowcount == 1


def blacklist_get(user_id):
    cursor.execute("SELECT reason, by_id, at FROM ticket_blacklist WHERE user_id=?", (str(user_id),))
    row = cursor.fetchone()
    return {"reason": row[0], "by_id": row[1], "at": row[2]} if row else None
