import discord

BRAND_RED = 0xE5231F


def money(value):
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def order_embed(order, title=None):
    embed = discord.Embed(
        title=title or f"📦 {order.get('orderNumber')}",
        description=f"**{order.get('statusText', 'Unknown')}**",
        color=BRAND_RED,
    )

    items = order.get("items") or []
    if items:
        lines = [f"{i.get('quantity')}× {i.get('name')}" + (f" ({i.get('options')})" if i.get("options") else "") for i in items]
        embed.add_field(name="Items", value="\n".join(lines)[:1000], inline=False)

    embed.add_field(name="Total", value=money(order.get("total")), inline=True)
    if order.get("shippingMethod"):
        embed.add_field(name="Shipping", value=order["shippingMethod"], inline=True)

    for shipment in (order.get("shipments") or [])[:3]:
        parts = [f"**{shipment.get('statusText')}**"]
        carrier = " ".join(x for x in [shipment.get("carrier"), shipment.get("service")] if x)
        if carrier:
            parts.append(carrier)
        if shipment.get("trackingNumber"):
            number = shipment["trackingNumber"]
            parts.append(f"[{number}]({shipment['trackingUrl']})" if shipment.get("trackingUrl") else number)
        latest = shipment.get("latestCarrierUpdate") or {}
        if latest.get("text"):
            parts.append(f"Latest: {latest['text']}")
        embed.add_field(name="Package", value="\n".join(parts)[:1000], inline=False)

    for refund in (order.get("refunds") or [])[:3]:
        embed.add_field(name="Refund", value=f"{money(refund.get('amount'))} · {refund.get('status')}", inline=True)

    return embed
