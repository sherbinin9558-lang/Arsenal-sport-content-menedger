"""CRM workflow helpers shared by the UI and sales assistant."""
import json
from pathlib import Path
from datetime import datetime
import uuid
from saas_core import data_load, data_save

LEADS_FILE = Path("leads.json")
STATUSES = ["Новый", "В работе", "Ожидает ответа", "Заказ оформлен", "Завершён", "Отменён"]
LEAD_IMMUTABLE_FIELDS = frozenset({"id", "created_at"})

def load_leads():
    import streamlit as st
    key = "_app_leads_cache"
    if key not in st.session_state:
        st.session_state[key] = data_load("leads", [])
    return st.session_state[key]

def save_leads(leads):
    data_save("leads", leads)
    import streamlit as st
    st.session_state["_app_leads_cache"] = leads

def create_lead(name="", contact="", source="Website", message="", product="", product_id="", content_id="", order_id=""):
    leads = load_leads()
    lead = {
        "id": uuid.uuid4().hex, "name": name, "contact": contact, "source": source,
        "message": message, "product": product, "product_id": product_id,
        "status": "Новый", "content_id": str(content_id or ""), "order_id": str(order_id or ""), "created_at": datetime.now().isoformat(timespec="minutes")
    }
    leads.append(lead)
    save_leads(leads)
    return lead

def update_lead(lead_id, **changes):
    protected = LEAD_IMMUTABLE_FIELDS.intersection(changes)
    if protected:
        field = sorted(protected)[0]
        raise ValueError(f"Поле лида нельзя изменять: {field}.")
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError(f"Недопустимый статус лида: {changes['status']}")
    leads = load_leads()
    for lead in leads:
        if lead.get("id") == lead_id:
            lead.update(changes)
            lead["updated_at"] = datetime.now().isoformat(timespec="minutes")
            save_leads(leads)
            return lead
    return None

def convert_lead_to_order(lead_id, order_id):
    return update_lead(lead_id, status="Заказ оформлен", order_id=order_id)

def crm_metrics(leads):
    return {
        "total": len(leads),
        "new": sum(x.get("status") == "Новый" for x in leads),
        "in_work": sum(x.get("status") == "В работе" for x in leads),
        "orders": sum(x.get("status") == "Заказ оформлен" for x in leads),
        "completed": sum(x.get("status") == "Завершён" for x in leads),
    }


def add_lead_interaction(lead_id, message, direction="manager"):
    leads = load_leads()
    for lead in leads:
        if lead.get("id") == lead_id:
            history = lead.setdefault("history", [])
            history.append({
                "time": datetime.now().isoformat(timespec="minutes"),
                "direction": direction,
                "message": message,
            })
            lead["updated_at"] = datetime.now().isoformat(timespec="minutes")
            save_leads(leads)
            return lead
    return None

def set_customer_profile(lead_id, notes="", interested_products=None, customer_status=None):
    changes = {"notes": notes}
    if interested_products is not None:
        changes["interested_products"] = interested_products
    if customer_status:
        changes["customer_status"] = customer_status
    return update_lead(lead_id, **changes)
