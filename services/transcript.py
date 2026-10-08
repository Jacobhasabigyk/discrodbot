"""
Ticket transcripts: one self-contained HTML file per closed ticket.
Readable in any browser, no outside resources, everything escaped.
"""

import datetime
import html

STYLE = """
body{margin:0;background:#f4f4f6;color:#1c1d21;font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:28px 18px 60px}
header{background:#0c0c0e;color:#fff;border-radius:14px;padding:20px 22px;margin-bottom:18px}
header h1{margin:0 0 6px;font-size:21px}
header .brand{font-weight:800;letter-spacing:2px;font-size:12px;color:#e5231f;margin-bottom:8px}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px 18px;font-size:13px;color:#c9cad1}
.meta b{display:block;color:#8c8f99;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.msg{display:grid;grid-template-columns:38px 1fr;gap:12px;padding:10px 6px;border-bottom:1px solid #e4e4e8}
.av{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:800}
.who{font-weight:700}.who small{color:#7b7e88;font-weight:400;margin-left:6px}
.tag{font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;margin-left:6px;vertical-align:2px}
.tag.bot{background:#5865f2;color:#fff}.tag.staff{background:#2f8f6a;color:#fff}
.text{white-space:pre-wrap;overflow-wrap:anywhere}
.embed{border-left:4px solid #e5231f;background:#fff;border-radius:6px;padding:8px 12px;margin-top:6px;font-size:14px}
.embed b{display:block}
.att a{color:#0b6bcb;font-size:13px}
footer{margin-top:18px;color:#7b7e88;font-size:12px;text-align:center}
"""

PALETTE = ["#6d5bd0", "#2f8f6a", "#c2410c", "#0e7490", "#be185d", "#4d7c0f"]


def _fmt(ts):
    if not ts:
        return "—"
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%b %d, %Y %H:%M UTC")


def build_transcript(ticket, messages, *, topic_label, owner_name, claimed_name, closed_name, staff_ids, bot_id):
    """
    messages: list of dicts {author_id, author_name, created_at (ts), content,
              embeds: [{title, description, fields: [(name, value)]}], attachments: [{filename, url}]}
    """
    esc = html.escape
    colors = {}

    rows = []
    for m in messages:
        aid = str(m["author_id"])
        if aid not in colors:
            colors[aid] = "#e5231f" if aid == str(bot_id) else PALETTE[len(colors) % len(PALETTE)]
        tag = ""
        if aid == str(bot_id):
            tag = '<span class="tag bot">BOT</span>'
        elif aid in staff_ids:
            tag = '<span class="tag staff">STAFF</span>'

        parts = []
        if m.get("content"):
            parts.append(f'<div class="text">{esc(m["content"])}</div>')
        for e in m.get("embeds") or []:
            inner = []
            if e.get("title"):
                inner.append(f"<b>{esc(e['title'])}</b>")
            if e.get("description"):
                inner.append(f'<div class="text">{esc(e["description"])}</div>')
            for name, value in e.get("fields") or []:
                inner.append(f'<div><b>{esc(name)}</b><span class="text">{esc(value)}</span></div>')
            if inner:
                parts.append(f'<div class="embed">{"".join(inner)}</div>')
        for a in m.get("attachments") or []:
            parts.append(f'<div class="att">📎 <a href="{esc(a["url"])}">{esc(a["filename"])}</a></div>')
        if not parts:
            continue

        initial = esc((m.get("author_name") or "?")[:1].upper())
        rows.append(
            f'<div class="msg"><div class="av" style="background:{colors[aid]}">{initial}</div>'
            f'<div><div class="who">{esc(m.get("author_name") or "Unknown")}{tag}<small>{_fmt(m.get("created_at"))}</small></div>'
            f'{"".join(parts)}</div></div>'
        )

    number = f"#{int(ticket['id']):04d}"
    meta = [
        ("Topic", topic_label),
        ("Customer", owner_name),
        ("Opened", _fmt(ticket.get("created_at"))),
        ("Closed", _fmt(ticket.get("closed_at"))),
        ("Claimed by", claimed_name or "—"),
        ("Closed by", closed_name or "—"),
        ("Reason", ticket.get("close_reason") or "—"),
        ("Messages", str(len(rows))),
    ]
    meta_html = "".join(f"<div><b>{esc(k)}</b>{esc(v)}</div>" for k, v in meta)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ticket {number} · Buttonland</title><style>{STYLE}</style></head>
<body><div class="wrap">
<header><div class="brand">BUTTONLAND SUPPORT</div><h1>Ticket {number}</h1><div class="meta">{meta_html}</div></header>
{''.join(rows) or '<p>No messages.</p>'}
<footer>Transcript generated {_fmt(ticket.get('closed_at'))}</footer>
</div></body></html>"""
