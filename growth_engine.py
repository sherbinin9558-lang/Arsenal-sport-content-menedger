"""Local AI-style growth engine for Arsenal Sport.

Free-first, explainable and deterministic: analyzes products, content, leads and orders,
then scores products/channels/formats and generates the next content actions.
No paid API, no invented metrics, and no claim that a recommendation is a measured fact
when the available data is insufficient.
"""
from collections import Counter, defaultdict
import re
from inventory_core import stock_info

CHANNELS = ("Instagram", "Telegram", "VK")
FORMATS = ("Reels", "Пост", "Stories", "Карусель")

def _amount(value):
    try:
        return float(str(value or "").replace(" ", "").replace(",", ".").replace("₽", ""))
    except Exception:
        return 0.0

def _title(p):
    return f"{p.get('brand','')} {p.get('name','')}".strip()

def _norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())

def _match(text, product):
    hay = _norm(text)
    pid = _norm(product.get("id") or product.get("article"))
    title = _norm(_title(product))
    name = _norm(product.get("name"))
    article = _norm(product.get("article"))
    return bool((pid and pid in hay) or (article and article in hay) or
                (name and name in hay) or (title and title in hay))

def _stock(product):
    qty, _, known = stock_info(product)
    return qty if known and qty is not None else 0

def funnel(leads, orders, plan, products):
    published = sum(1 for x in plan if x.get("status") == "Опубликовано")
    completed = sum(1 for x in orders if x.get("status") == "Завершён")
    revenue = sum(_amount(x.get("amount")) for x in orders if x.get("status") != "Отменён")
    return {
        "content": len(plan), "published": published, "leads": len(leads),
        "orders": len(orders), "completed_orders": completed, "revenue": revenue,
        "lead_to_order": (len(orders) / len(leads) * 100) if leads else 0.0,
        "published_to_lead": (len(leads) / published * 100) if published else 0.0,
    }

def _build_product_match_index(products):
    """Token index prevents O(products × leads/orders/content) scans at 10k products."""
    index = defaultdict(set)
    for i, product in enumerate(products):
        values = (
            product.get("id"), product.get("article"), product.get("name"),
            _title(product),
        )
        for value in values:
            for token in re.findall(r"[\\wа-яё]+", _norm(value)):
                if len(token) > 1:
                    index[token].add(i)
    return index


def _candidate_product_indexes(text, index):
    tokens = re.findall(r"[\\wа-яё]+", _norm(text))
    candidates = set()
    for token in tokens:
        candidates.update(index.get(token, ()))
    return candidates


def product_performance(products, leads, orders, plan):
    """Build product metrics in linear event passes with a small candidate set."""
    match_index = _build_product_match_index(products)
    content_counts = Counter()
    lead_counts = Counter()
    order_counts = Counter()
    revenue_by_product = defaultdict(float)

    for item in plan:
        text = item.get("product")
        for i in _candidate_product_indexes(text, match_index):
            if _match(text, products[i]):
                content_counts[i] += 1

    for item in leads:
        text = item.get("product") or item.get("message")
        for i in _candidate_product_indexes(text, match_index):
            if _match(text, products[i]):
                lead_counts[i] += 1

    for item in orders:
        text = item.get("product")
        for i in _candidate_product_indexes(text, match_index):
            if _match(text, products[i]):
                order_counts[i] += 1
                if item.get("status") != "Отменён":
                    revenue_by_product[i] += _amount(item.get("amount"))

    rows = []
    for i, p in enumerate(products):
        rows.append({
            "product": _title(p) or "Товар",
            "content": content_counts[i],
            "leads": lead_counts[i],
            "orders": order_counts[i],
            "revenue": revenue_by_product[i],
            "stock": _stock(p),
        })
    return sorted(rows, key=lambda x: (-x["orders"], -x["revenue"], -x["leads"], -x["content"], x["product"]))

def content_performance(plan):
    platform, types, statuses = Counter(), Counter(), Counter()
    for x in plan:
        statuses[x.get("status", "Идея")] += 1
        if x.get("status") == "Опубликовано":
            platform[x.get("platform", "Другое")] += 1
            types[x.get("type", "Пост")] += 1
    return {"platforms": platform, "types": types, "statuses": statuses}

def _observed_channel_stats(plan, leads, orders):
    stats = {c: {"published": 0, "leads": 0, "orders": 0, "revenue": 0.0} for c in CHANNELS}
    for item in plan:
        if item.get("status") == "Опубликовано":
            c = item.get("platform", "Другое")
            if c in stats:
                stats[c]["published"] += 1
    # Lead/order source is the strongest available channel signal.
    for lead in leads:
        c = lead.get("source")
        if c in stats:
            stats[c]["leads"] += 1
    for order in orders:
        c = order.get("source")
        if c in stats:
            stats[c]["orders"] += 1
            if order.get("status") != "Отменён":
                stats[c]["revenue"] += _amount(order.get("amount"))
    return stats

def _observed_format_stats(plan, leads, orders):
    stats = {f: {"published": 0, "leads": 0, "orders": 0, "revenue": 0.0} for f in FORMATS}
    for item in plan:
        if item.get("status") == "Опубликовано" and item.get("type") in stats:
            stats[item["type"]]["published"] += 1
    return stats

def _score_signal(published, leads, orders, revenue):
    # Conservative score: outcomes matter more than volume; sparse data is penalized.
    if not published and not leads and not orders:
        return 0.0
    outcome = leads * 4 + orders * 10 + min(revenue / 10000.0, 10)
    exposure = max(published, 1)
    confidence = min(1.0, (published + leads + orders) / 10.0)
    return round((outcome / exposure) * confidence, 2)

def recommendation_matrix(products, leads, orders, plan):
    pp = product_performance(products, leads, orders, plan)
    channels = _observed_channel_stats(plan, leads, orders)
    formats = _observed_format_stats(plan, leads, orders)
    product_rows = []
    for row in pp:
        score = row["leads"] * 4 + row["orders"] * 10 + min(row["revenue"] / 10000.0, 10)
        if row["stock"] <= 0:
            score *= 0.25
        elif row["stock"] <= 2:
            score *= 0.65
        score = round(score, 2)
        product_rows.append({**row, "score": score})
    channel_rows = []
    for name, s in channels.items():
        channel_rows.append({**s, "channel": name,
                             "score": _score_signal(s["published"], s["leads"], s["orders"], s["revenue"])})
    format_rows = []
    for name, s in formats.items():
        format_rows.append({**s, "format": name,
                            "score": _score_signal(s["published"], s["leads"], s["orders"], s["revenue"])})
    product_rows.sort(key=lambda x: (-x["score"], -x["orders"], -x["leads"]))
    channel_rows.sort(key=lambda x: (-x["score"], x["channel"]))
    format_rows.sort(key=lambda x: (-x["score"], x["format"]))
    return {"products": product_rows, "channels": channel_rows, "formats": format_rows}

def recommendations(products, leads, orders, plan):
    f = funnel(leads, orders, plan, products)
    matrix = recommendation_matrix(products, leads, orders, plan)
    out = []
    if not products:
        return ["Добавьте товары: AI не будет придумывать ассортимент."]
    if f["published"] == 0:
        out.append("Нет опубликованных материалов: сначала запустите тестовый цикл контента.")
    elif f["leads"] == 0:
        out.append("Публикации есть, но лидов нет: усилить CTA и контент с конкретным товаром.")
    if f["leads"] and not orders:
        out.append("Есть лиды, но нет заказов: проверить ответ менеджера, наличие, размеры и следующий шаг.")
    if matrix["products"]:
        p = matrix["products"][0]
        if p["orders"] or p["leads"]:
            out.append(f"Фокус на товаре «{p['product']}»: он набрал максимальный сигнал по текущим данным.")
    active_channels = [x for x in matrix["channels"] if x["published"] or x["leads"] or x["orders"]]
    if active_channels:
        c = active_channels[0]
        out.append(f"Канал для следующего теста: {c['channel']} — сигнал {c['score']:.2f}; сравнивайте по лидам и заказам, не только по охвату.")
    active_formats = [x for x in matrix["formats"] if x["published"]]
    if active_formats:
        fm = active_formats[0]
        out.append(f"Формат для следующего теста: {fm['format']} — сигнал {fm['score']:.2f}.")
    if len(out) < 4:
        out.append("Следующий цикл: товар → формат → канал → CTA → лид → заказ → анализ.")
    return out[:6]

def next_content(products, leads, orders, plan, limit=7):
    matrix = recommendation_matrix(products, leads, orders, plan)
    used = Counter((x.get("product"), x.get("platform"), x.get("type")) for x in plan)
    ranked_products = [x["product"] for x in matrix["products"] if x["stock"] > 0]
    ranked_products += [x["product"] for x in matrix["products"] if x["product"] not in ranked_products]
    ranked_channels = [x["channel"] for x in matrix["channels"] if x["published"] or x["leads"] or x["orders"]] or list(CHANNELS)
    ranked_formats = [x["format"] for x in matrix["formats"] if x["published"]] or list(FORMATS)
    result = []
    for product in ranked_products:
        if len(result) >= limit:
            break
        for channel in ranked_channels:
            for fmt in ranked_formats:
                if used[(product, channel, fmt)] < 1:
                    row = next((x for x in matrix["products"] if x["product"] == product), {})
                    result.append({
                        "product": product, "channel": channel, "type": fmt,
                        "idea": f"{fmt}: показать {product} через пользу, детали, сценарий использования и CTA.",
                        "priority": "Высокий" if row.get("leads") or row.get("orders") else "Тест",
                        "reason": f"Товар: сигнал {row.get('score', 0):.2f}; канал: {channel}; формат: {fmt}.",
                    })
                    break
            if len(result) >= limit:
                break
    return result


def attribution_performance(plan, leads, orders):
    """Measure only explicit publication-to-lead/order links; never guess causality."""
    by_id = {str(x.get("content_id")): x for x in plan if x.get("content_id")}
    rows = []
    lead_by_content = Counter(str(x.get("content_id")) for x in leads if x.get("content_id"))
    order_by_content = Counter(str(x.get("content_id")) for x in orders if x.get("content_id"))
    revenue_by_content = defaultdict(float)
    for order in orders:
        cid = str(order.get("content_id") or "")
        if cid and order.get("status") != "Отменён":
            revenue_by_content[cid] += _amount(order.get("amount"))
    for cid, item in by_id.items():
        rows.append({
            "content_id": cid,
            "publication_id": str(item.get("publication_id") or cid),
            "date": item.get("date", ""),
            "product": item.get("product", ""),
            "channel": item.get("platform", ""),
            "format": item.get("type", ""),
            "status": item.get("status", "Идея"),
            "leads": lead_by_content.get(cid, 0),
            "orders": order_by_content.get(cid, 0),
            "revenue": revenue_by_content.get(cid, 0.0),
        })
    return sorted(rows, key=lambda x: (-x["orders"], -x["revenue"], -x["leads"], x["date"]))

def ai_summary(products, leads, orders, plan):
    matrix = recommendation_matrix(products, leads, orders, plan)
    return {
        "headline": "AI-слой: анализ → рекомендация → следующий контент",
        "funnel": funnel(leads, orders, plan, products),
        "recommendations": recommendations(products, leads, orders, plan),
        "next_content": next_content(products, leads, orders, plan),
        "products": matrix["products"],
        "channels": matrix["channels"],
        "formats": matrix["formats"],
        "content": content_performance(plan),
        "attribution": attribution_performance(plan, leads, orders),
        "ai_mode": "local_explainable",
    }
