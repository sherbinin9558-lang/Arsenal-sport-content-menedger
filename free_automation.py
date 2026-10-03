"""Free local automation helpers for Arsenal Sport MAX."""
import json
from pathlib import Path
from datetime import datetime, date, timedelta
import uuid
from saas_core import data_load, data_save, feature_allowed, tenant_plan
from inventory_core import stock_info

ORDERS_FILE = Path("orders.json")
ORDER_STATUSES = ("Новая", "Связались", "Ожидает оплаты", "Оплачен", "Собирается", "Отправлен", "Завершён", "Отменён")
ORDER_IMMUTABLE_FIELDS = frozenset({"id", "created_at"})

def load_orders():
    import streamlit as st
    key = "_app_orders_cache"
    if key not in st.session_state:
        st.session_state[key] = data_load("orders", [])
    return st.session_state[key]

def save_orders(orders):
    data_save("orders", orders)
    import streamlit as st
    st.session_state["_app_orders_cache"] = orders

def create_order(customer="", contact="", product="", product_id="", amount="", status="Новая", source="Manual", content_id="", lead_id=""):
    if status not in ORDER_STATUSES:
        raise ValueError(f"Недопустимый статус заказа: {status}")
    orders = load_orders()
    if not feature_allowed("orders", len(orders)):
        raise ValueError(f"Лимит заказов тарифа {tenant_plan().upper()} достигнут.")
    order = {
        "id": uuid.uuid4().hex,
        "customer": customer.strip(), "contact": contact.strip(),
        "product": product.strip(), "product_id": str(product_id or ""),
        "amount": str(amount or "").strip(), "status": status, "source": source, "content_id": str(content_id or ""), "lead_id": str(lead_id or ""),
        "created_at": datetime.now().isoformat(timespec="minutes"),
        "updated_at": datetime.now().isoformat(timespec="minutes"),
    }
    orders.append(order)
    save_orders(orders)
    return order

def update_order(order_id, **changes):
    protected = ORDER_IMMUTABLE_FIELDS.intersection(changes)
    if protected:
        field = sorted(protected)[0]
        raise ValueError(f"Поле заказа нельзя изменять: {field}.")
    if "status" in changes and changes["status"] not in ORDER_STATUSES:
        raise ValueError(f"Недопустимый статус заказа: {changes['status']}")
    orders = load_orders()
    for order in orders:
        if str(order.get("id")) == str(order_id):
            order.update(changes)
            order["updated_at"] = datetime.now().isoformat(timespec="minutes")
            save_orders(orders)
            return order
    return None

def order_metrics(orders, active_statuses):
    active = sum(o.get("status", "Новая") in active_statuses for o in orders)
    completed = sum(o.get("status") == "Завершён" for o in orders)
    cancelled = sum(o.get("status") == "Отменён" for o in orders)
    amount = 0.0
    for o in orders:
        if o.get("status") == "Отменён":
            continue
        try:
            amount += float(str(o.get("amount", "")).replace(" ", "").replace(",", "."))
        except Exception:
            pass
    return {"total": len(orders), "active": active, "completed": completed, "cancelled": cancelled, "amount": amount}

def stock_rows(products):
    rows = []
    for p in products:
        total, _, known = stock_info(p)
        rows.append((p, total if known and total is not None else 0))
    return rows

def low_stock(products, threshold=2):
    return [(p, qty) for p, qty in stock_rows(products) if qty <= threshold]

def customer_history(leads, orders, query):
    q = str(query or "").strip().lower()
    if not q: return []
    result = []
    for lead in leads:
        hay = " ".join(str(lead.get(k, "")) for k in ("name", "contact", "product", "message")).lower()
        if q in hay: result.append(("lead", lead))
    for order in orders:
        hay = " ".join(str(order.get(k, "")) for k in ("customer", "contact", "product")).lower()
        if q in hay: result.append(("order", order))
    return result

def content_bundle(product):
    title = f"{product.get('brand','')} {product.get('name','')}".strip()
    desc = str(product.get("description", "")).strip()
    sizes = str(product.get("sizes", "уточняйте")).strip()
    color = str(product.get("color", "уточняйте")).strip()
    category = str(product.get("category", "спорттовар")).strip().lower()
    return {
        "Instagram": f"🔥 {title}\n\n{desc}\n\n📏 Размеры: {sizes}\n🎨 Цвет: {color}\n\n📩 Напишите в Arsenal Sport — поможем подобрать вариант.\n#arsenal_sport #спорт",
        "Telegram": f"🏆 Arsenal Sport\n\n{title}\n{desc}\n\n📏 Размеры: {sizes}\n🎨 Цвет: {color}\n📩 Уточнить наличие можно в сообщениях.",
        "VK": f"🔥 {title}\n\n{desc}\n\nРазмеры: {sizes} · Цвет: {color}\n\n📩 Напишите нам для заказа.",
        "Reels": f"Хук: «Ищете {category}? Покажу вариант {title}.»\nКадры: товар → детали → CTA «Напишите Arsenal Sport».",
        "Stories": f"1. {title}\n2. Деталь товара\n3. «Какой размер нужен?»\n4. «Напишите нам — поможем подобрать».",
    }

def seven_day_plan(products):
    types = ["Пост", "Reels", "Stories", "Карусель", "Пост", "Reels", "Stories"]
    platforms = ["Instagram", "Telegram", "VK", "Instagram", "Telegram", "VK", "Instagram"]
    return [{
        "date": str(date.today() + timedelta(days=i)),
        "platform": platforms[i], "product": f"{p.get('brand','')} {p.get('name','')}".strip(),
        "type": types[i],
        "idea": f"Показать {p.get('name','товар')}: польза, детали, кому подходит и CTA.",
        "status": "Идея", "priority": "Высокий" if i < 2 else "Обычный",
    } for i, p in enumerate(products[:7])]

def conversion_metrics(leads, orders):
    total = len(leads)
    converted = sum(1 for l in leads if l.get("status") == "Заказ оформлен")
    return {"leads": total, "lead_orders": converted,
            "conversion": (converted / total * 100) if total else 0.0,
            "orders": len(orders)}
