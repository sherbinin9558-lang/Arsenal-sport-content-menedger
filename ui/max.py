"""MAX UI module. Business/data logic stays in existing core modules."""

import base64
import datetime
import io
from pathlib import Path

import streamlit as st
from PIL import Image

from card_generator import generate_card

from automation_suite import (
    analytics as automation_analytics,
    bulk_update,
    content_for_product,
    low_stock_products,
    make_30_day_plan,
    product_key,
    save_uploaded_photo,
    stock_info,
)
from content_manager import recommendations
from crm_core import (
    add_lead_interaction,
    crm_metrics,
    load_leads,
    update_lead,
    create_lead,
)
from free_automation import (
    conversion_metrics,
    create_order,
    customer_history,
    load_orders,
    low_stock,
    order_metrics,
    seven_day_plan,
    update_order,
)
from growth_engine import ai_summary, recommendations as growth_recommendations
from max_features import product_search
from ai_seller import ai_sales_reply, sales_followup
from saas_core import (
    activate_paid_subscription,
    can,
    data_load,
    data_save,
    limit,
    plan_catalog,
    require_saas_access,
    subscription_snapshot,
    tenant_plan,
    usage_snapshot,
    current_role,
    request_plan_change,
    set_auto_renew,
    team_invitations,
    team_members,
    create_team_invitation,
    set_member_role,
)

CATEGORIES = ["Футболка", "Кроссовки", "Спортивный костюм", "Аксессуары", "Инвентарь", "Другое"]


def load_products():
    return data_load("products", [])


def save_products(products):
    data_save("products", products)


def load_plan():
    return data_load("content_plan", [])


def save_plan(plan):
    data_save("content_plan", plan)


def get_logo():
    path = Path("tenant_assets") / str(st.session_state.get("saas_tenant_id", "unknown")) / "logo.png"
    if path.exists():
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            return None
    return None


ORDER_STATUSES = ["Новая", "Связались", "Ожидает оплаты", "Оплачен", "Собирается", "Отправлен", "Завершён", "Отменён"]
ACTIVE_ORDER_STATUSES = {"Новая", "Связались", "Ожидает оплаты", "Оплачен", "Собирается", "Отправлен"}



def _record_id(product):
    return str(product.get("_saas_record_id", "")).strip()


def _current_product_index(products, record_id, fallback_index=None):
    if record_id:
        for i, product in enumerate(products):
            if _record_id(product) == record_id:
                return i
    if fallback_index is not None and 0 <= fallback_index < len(products):
        return fallback_index
    return None

def max_stock(product):
    by_size = product.get("stock_by_size") or {}
    qty, _, known = stock_info(product)
    return qty if known and qty is not None else 0

def max_product_title(product):
    return f"{product.get('brand','')} {product.get('name','')}".strip() or "Товар"

def max_due_content(plan):
    today = datetime.date.today()
    due, overdue = [], []
    for item in plan:
        try:
            d = datetime.date.fromisoformat(str(item.get("date","")))
        except Exception:
            continue
        if item.get("status") == "Опубликовано":
            continue
        if d < today:
            overdue.append(item)
        elif d == today:
            due.append(item)
    return due, overdue


@st.dialog("⚡ AI Agent Content Manager MAX", width="large")
def render_max():
    snap = st.session_state.get("max_data_snapshot")
    if snap is None:
        snap = {
            "products": load_products(),
            "plan": load_plan(),
            "leads": load_leads(),
            "orders": load_orders(),
        }
        st.session_state["max_data_snapshot"] = snap
    products = snap.get("products", [])
    plan = snap.get("plan", [])
    leads = snap.get("leads", [])
    orders = snap.get("orders", [])
    due, overdue = max_due_content(plan)
    crm = crm_metrics(leads)
    om = order_metrics(orders, ACTIVE_ORDER_STATUSES)
    low = low_stock_products(products)
    cm = conversion_metrics(leads, orders)

    st.markdown(
        '<div class="max-header">'
        '<div class="max-header-title">⚡ AI Agent Content Manager <span>MAX</span></div>'
        '<div class="max-header-subtitle">Центр управления магазином</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.caption("MAX не просто показывает цифры — он превращает данные магазина в конкретные следующие действия.")

    try:
        max_recs = growth_recommendations(products, leads, orders, plan)
    except Exception:
        max_recs = []
    if max_recs:
        st.markdown('<div class="max-ai-plan"><div class="max-ai-plan-title">✦ План действий MAX</div>' +
                    ''.join(f'<div class="max-ai-plan-item">• {x}</div>' for x in max_recs[:4]) +
                    '</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">MAX готов. Добавьте первый товар, чтобы получить персональные рекомендации.</div>', unsafe_allow_html=True)

    a, b, c, d, e = st.columns(5)
    a.metric("Товары", len(products))
    b.metric("Заявки", crm.get("total", 0))
    c.metric("Активные заказы", om["active"])
    d.metric("Контент сегодня", len(due))
    e.metric("Конверсия", f"{cm['conversion']:.1f}%")

    st.markdown("---")
    section = st.radio(
        "Раздел MAX",
        ["Обзор", "Аккаунт", "Настройки", "AI-продавец", "CRM", "Заказы", "Склад", "Контент", "Автоматизация", "Аналитика"],
        horizontal=True,
        key="max_section",
    )

    if section == "Обзор":
        st.subheader("Состояние магазина")
        x, y, z = st.columns(3)
        x.metric("Всего заказов", om["total"])
        y.metric("Завершено", om["completed"])
        z.metric("Сумма заказов", f"{om['amount']:,.0f}".replace(",", " ") + " ₽")
        st.write(
            f"Товаров: {len(products)} · карточек: "
            f"{sum(bool(p.get('card_image')) for p in products)} · "
            f"публикаций в плане: {len(plan)}"
        )
        if overdue:
            st.error(f"Просрочено публикаций: {len(overdue)}")
        elif due:
            st.info(f"На сегодня: {len(due)} публикаций")
        else:
            st.success("На сегодня срочных публикаций нет.")
        if low:
            st.warning(f"Низкий остаток: {len(low)} товаров (порог ≤ 2).")

        st.markdown("### ⚡ MAX может сделать это сейчас")
        if not products:
            st.info("Добавьте первый товар — после этого MAX сможет создать контент и план действий.")
        else:
            qa, qb = st.columns(2)
            with qa:
                if can("write_data") and st.button("✨ Создать 7 идей контента", use_container_width=True, key="max_quick_plan"):
                    with st.spinner("MAX готовит план…"):
                        suggestions = seven_day_plan(products)
                        existing = load_plan()
                        existing_keys = {(x.get("date"), x.get("product")) for x in existing}
                        added = 0
                        for item in suggestions:
                            key = (item.get("date"), item.get("product"))
                            if key not in existing_keys:
                                existing.append(item); added += 1
                        save_plan(existing)
                    st.session_state.pop("max_data_snapshot", None)
                    st.success(f"Готово: добавлено {added} идей.")
                    st.rerun()
            with qb:
                if st.button("↻ Обновить анализ MAX", use_container_width=True, key="max_refresh"):
                    st.session_state.pop("max_data_snapshot", None)
                    st.rerun()

    elif section == "Аккаунт":
        st.subheader("🏪 Аккаунт магазина")
        st.write(f"**Магазин:** {st.session_state.get('saas_tenant_name','—')}")
        st.write(f"**Тариф:** {str(tenant_plan()).upper()}")
        st.write(f"**Статус:** {st.session_state.get('saas_tenant_status','active')}")
        try:
            from saas_core import usage_snapshot, PLAN_LIMITS
            usage=usage_snapshot()
            limits=PLAN_LIMITS.get(tenant_plan(), PLAN_LIMITS["trial"])
            st.markdown("### Использование")
            u1,u2,u3,u4=st.columns(4)
            u1.metric("Товары", f"{usage['products']} / {limits['products']}")
            u2.metric("Заявки", usage["leads"])
            u3.metric("Заказы", usage["orders"])
            u4.metric("Контент", usage["content"])
            st.progress(min(1.0, usage["products"]/max(1,limits["products"])), text=f"Лимит каталога: {limits['products']} товаров")
        except Exception as e:
            st.info(f"Статистика аккаунта пока недоступна: {e}")
        st.markdown("---")
        st.markdown("### Тарифы")
        st.caption("Тарифы уже заложены в архитектуру. Реальную оплату подключим следующим этапом.")
        p1,p2,p3=st.columns(3)
        p1.info("STARTER\n\nдо 10 000 товаров\nдо 3 пользователей")
        p2.info("PRO\n\nдо 10 000 товаров\nдо 10 пользователей")
        p3.info("BUSINESS\n\nдо 100 000 товаров\nдо 50 пользователей")
        st.caption("Смена тарифа вручную отключена: тариф должен меняться через серверную оплату или админку.\n")
        from saas_core import plan_catalog, subscription_snapshot, request_plan_change, set_auto_renew, current_role
        sub=subscription_snapshot()
        st.markdown("### 🔄 Автопродление")
        try:
            sub_auto=subscription_snapshot()
            auto_default=bool(sub_auto.get("auto_renew", st.session_state.get("auto_renew", False)))
            if current_role() in ("owner","admin"):
                auto=st.toggle("Автоматически продлевать подписку", value=auto_default, key="billing_auto_renew")
                if auto != auto_default:
                    set_auto_renew(auto)
                    st.success("Настройка автопродления сохранена.")
            else:
                st.caption("Изменять автопродление может только владелец или администратор.")
        except Exception as e:
            st.info(f"Автопродление пока не настроено: {e}")
        st.markdown("---")
        st.markdown("### 💳 Подписка")
        status_names={"trialing":"Пробный период","active":"Активна","past_due":"Требует оплаты","canceled":"Отменена","unknown":"Статус уточняется"}
        st.write(f"**Статус:** {status_names.get(sub.get('status'), sub.get('status','—'))}")
        st.write(f"**Провайдер оплаты:** {sub.get('provider') or 'не подключён'}")
        if sub.get("current_period_end"):
            st.write(f"**Период до:** {sub['current_period_end']}")
        st.caption("Оплата проводится через подключённый платёжный провайдер. Тариф активируется только после подтверждения платежа.")
        plans=plan_catalog()
        b1,b2,b3=st.columns(3)
        for col,(key,info) in zip((b1,b2,b3),plans.items()):
            with col:
                col.markdown(f"### {info['name']}")
                col.write(info["description"])
                col.caption(f"Каталог: до {info['products']:,} · команда: до {info['users']}".replace(",", " "))
                if key == tenant_plan():
                    col.success("Текущий тариф")
                elif can("billing") and st.button(f"Выбрать {info['name']}", key=f"choose_plan_{key}", use_container_width=True):
                    request_plan_change(key)
                    st.info(f"Выбран {info['name']}. Подключение оплаты будет выполнено через серверный checkout.")
        requested=st.session_state.get("requested_plan")
        if requested:
            st.info(f"Подготовлен переход на тариф **{plans[requested]['name']}**. Реальная активация произойдёт после подтверждения оплаты.")
        st.markdown("---")
        st.markdown("### 🔐 Безопасность оплаты")
        st.caption("Платёжные данные и секретные ключи не хранятся в интерфейсе или каталоге магазина.")

        st.markdown("---")
        st.markdown("### 👥 Команда магазина")
        from saas_core import current_role, team_members, team_invitations, create_team_invitation, set_member_role, limit
        role=current_role()
        role_names={"owner":"Владелец","admin":"Администратор","manager":"Менеджер","editor":"Редактор","viewer":"Наблюдатель"}
        st.write(f"**Ваша роль:** {role_names.get(role, role)}")
        members=team_members()
        st.caption(f"Участников: {len(members)} / {limit('users')}")
        for member in members:
            member_id=str(member.get("user_id",""))
            member_role=member.get("role","viewer")
            if role in ("owner","admin") and member_id != str(st.session_state.get("saas_user_id","")) and member_role != "owner":
                rc1,rc2,rc3=st.columns([2,1,1])
                with rc1:
                    st.write(f"• {member_id}")
                with rc2:
                    new_role=st.selectbox("Роль",["admin","manager","editor","viewer"],
                                          index=["admin","manager","editor","viewer"].index(member_role) if member_role in ["admin","manager","editor","viewer"] else 3,
                                          format_func=lambda x: role_names[x],key=f"member_role_{member_id}")
                with rc3:
                    if st.button("Сохранить",key=f"member_role_save_{member_id}"):
                        try:
                            set_member_role(member_id,new_role)
                            st.success("Роль обновлена.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Не удалось изменить роль: {e}")
            else:
                st.write(f"• {member_id} · **{role_names.get(member_role, member_role)}**")
        if role in ("owner","admin"):
            with st.expander("➕ Пригласить сотрудника"):
                invite_email=st.text_input("Email сотрудника", key="team_invite_email")
                invite_role=st.selectbox("Роль", ["manager","admin","editor","viewer"], format_func=lambda x: role_names[x], key="team_invite_role")
                if st.button("Создать приглашение", type="primary", key="team_invite_create"):
                    if len(members) >= limit("users"):
                        st.error("Лимит пользователей текущего тарифа достигнут.")
                    elif not invite_email.strip() or "@" not in invite_email:
                        st.warning("Укажите корректный email.")
                    else:
                        try:
                            create_team_invitation(invite_email, invite_role)
                            st.success("Приглашение создано. Отправка письма подключается через серверный invite-механизм.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Не удалось создать приглашение: {e}")
                invites=team_invitations()
                if invites:
                    st.markdown("**Ожидают приглашения:**")
                    for inv in invites:
                        st.write(f"• {inv.get('email')} · {role_names.get(inv.get('role'), inv.get('role'))}")
                else:
                    st.caption("Ожидающих приглашений нет.")

        st.markdown("### 💳 Реальная оплата")
        try:
            from billing import create_checkout, plan_price
            from saas_core import plan_catalog
            st.caption("Доступны ЮKassa и Т‑Банк. Секретные ключи хранятся только в Secrets.")
            try:
                from tbank_billing import create_checkout as tbank_create_checkout, plan_price as tbank_plan_price
                if can("billing") and st.button("Оплатить через Т‑Банк", key="pay_tbank", use_container_width=True):
                    plan_for_payment=st.session_state.get("requested_plan") or tenant_plan()
                    if plan_for_payment not in ("starter","pro","business"):
                        plan_for_payment="starter"
                    try:
                        payment=tbank_create_checkout(plan_for_payment, st.session_state.get("saas_tenant_id"))
                        st.session_state["tbank_payment_id"]=payment.get("PaymentId")
                        st.session_state["tbank_order_id"]=payment.get("OrderId")
                        st.session_state["tbank_url"]=payment.get("PaymentURL")
                        st.session_state["tbank_plan"]=plan_for_payment
                    except Exception as e:
                        st.error(str(e))
                if st.session_state.get("tbank_url"):
                    st.link_button("Перейти к оплате в Т‑Банке", st.session_state["tbank_url"], use_container_width=True)
            except Exception as e:
                st.info(f"Т‑Банк пока не подключён: {e}")
            st.caption("Оплата через ЮKassa. Секретный ключ хранится только в Secrets.")
            bplans=plan_catalog()
            cols=st.columns(3)
            for col,(pkey,pinfo) in zip(cols,bplans.items()):
                with col:
                    price=plan_price(pkey)
                    st.markdown(f"**{pinfo['name']}**")
                    st.caption(f"{price:.2f} ₽ / месяц" if price else "Цена не настроена")
                    if can("billing") and st.button(f"Оплатить {pinfo['name']}", key=f"pay_{pkey}", use_container_width=True):
                        try:
                            payment=create_checkout(pkey, st.session_state.get("saas_tenant_id"))
                            st.session_state["billing_payment_id"]=payment.get("id")
                            st.session_state["billing_plan"]=pkey
                            st.session_state["billing_url"]=payment["confirmation"]["confirmation_url"]
                            st.success("Платёж создан.")
                        except Exception as e:
                            st.error(str(e))
            if st.session_state.get("billing_url"):
                st.link_button("Перейти к оплате", st.session_state["billing_url"], use_container_width=True)
        except Exception as e:
            st.info(f"ЮKassa пока не подключена: {e}")
        st.markdown("---")
    elif section == "Настройки":
        st.subheader("⚙️ Настройки магазина")
        can_settings = can("settings")
        from saas_core import data_load, data_save
        settings=data_load("settings", [])
        current=settings[0] if settings and isinstance(settings[0], dict) else {}
        with st.form("max_store_settings"):
            business=st.selectbox("Тип бизнеса", ["Спортивный магазин","Одежда и обувь","Интернет-магазин","Другое"], disabled=not can_settings,
                                  index=["Спортивный магазин","Одежда и обувь","Интернет-магазин","Другое"].index(current.get("business_type","Спортивный магазин"))
                                  if current.get("business_type","Спортивный магазин") in ["Спортивный магазин","Одежда и обувь","Интернет-магазин","Другое"] else 0)
            city=st.text_input("Город / регион", value=current.get("city",""), disabled=not can_settings)
            telegram=st.text_input("Telegram магазина", value=current.get("telegram",""), disabled=not can_settings)
            instagram=st.text_input("Instagram", value=current.get("instagram",""), disabled=not can_settings)
            shipping=st.selectbox("Доставка", ["По России","По региону","Самовывоз","Другое"], disabled=not can_settings,
                                  index=["По России","По региону","Самовывоз","Другое"].index(current.get("shipping","По России"))
                                  if current.get("shipping","По России") in ["По России","По региону","Самовывоз","Другое"] else 0)
            if st.form_submit_button("💾 Сохранить настройки", type="primary", disabled=not can_settings):
                payload = {"business_type":business,"city":city.strip(),"telegram":telegram.strip(),
                           "instagram":instagram.strip(),"shipping":shipping,"onboarding_complete":True}
                if current.get("_saas_record_id"):
                    payload["_saas_record_id"] = current["_saas_record_id"]
                data_save("settings",[payload])
                st.success("Настройки сохранены.")
                st.rerun()

        if not can_settings:
            st.info("Изменять настройки магазина может только владелец или администратор.")
        st.markdown("---")
        st.markdown("### Подключения")
        st.info("Telegram, Instagram и VK подключаются отдельными интеграциями. Секретные токены хранятся в Secrets, а не в коде.")

    elif section == "AI-продавец":
        st.subheader("AI-продавец по каталогу")
        q = st.text_input(
            "Что ищет клиент?",
            placeholder="Например: бутсы 42 размера",
            key="max_sales_query",
        )
        if st.button("🔎 Найти товар", type="primary", key="max_sales_search"):
            ans, found = ai_sales_reply(products, q)
            st.session_state["max_sales_answer"] = ans
            st.session_state["max_sales_found"] = found
        if st.session_state.get("max_sales_answer"):
            st.text_area("Готовый ответ", st.session_state["max_sales_answer"], height=180, key="max_sales_answer_box")
        found = st.session_state.get("max_sales_found", [])
        if found:
            follow = st.text_input("Уточнение клиента", placeholder="Например: покажи второй вариант", key="max_followup")
            if st.button("↩️ Ответить клиенту", key="max_followup_btn"):
                ans, new_found = sales_followup(products, follow, found)
                st.session_state["max_sales_answer"] = ans
                st.session_state["max_sales_found"] = new_found
                st.rerun()

    elif section == "CRM":
        st.subheader("CRM — заявки и история")
        if not leads:
            st.info("Заявок пока нет.")
        for lead in reversed(leads[-20:]):
            title = lead.get("name") or lead.get("contact") or f"Заявка #{lead.get('id', '')}"
            with st.expander(f"{title} · {lead.get('status', 'Новый')}"):
                st.write(f"Контакт: {lead.get('contact', '—')}")
                st.write(f"Товар: {lead.get('product', '—')}")
                st.write(f"Источник: {lead.get('source', '—')}")
                st.write(f"Сообщение: {lead.get('message', '—')}")
                if lead.get("history"):
                    st.caption("История контакта")
                    for h in lead.get("history", [])[-10:]:
                        st.write(f"{h.get('time','')} · {h.get('direction','')} · {h.get('message','')}")
                current_status = lead.get("status", "Новый")
                ns = st.selectbox(
                    "Статус", CRM_STATUSES,
                    index=CRM_STATUSES.index(current_status) if current_status in CRM_STATUSES else 0,
                    key=f"max_lead_status_{lead.get('id')}",
                    disabled=not can("write_data"),
                )
                if ns != current_status:
                    update_lead(lead.get("id"), status=ns)
                    st.rerun()
                note = st.text_input("Добавить заметку/контакт", key=f"max_note_{lead.get('id')}", disabled=not can("write_data"))
                if can("write_data") and st.button("💾 Сохранить заметку", key=f"max_note_btn_{lead.get('id')}"):
                    if note.strip():
                        add_lead_interaction(lead.get("id"), note.strip(), "manager")
                        st.rerun()
        st.markdown("---")
        history_q = st.text_input("Найти историю клиента по имени/контакту", key="max_history_q")
        if history_q:
            history = customer_history(leads, orders, history_q)
            if not history:
                st.info("Совпадений не найдено.")
            for kind, item in history:
                st.write(f"**{'Заявка' if kind == 'lead' else 'Заказ'} #{item.get('id','')}** · {item.get('created_at','')} · {item.get('product','')}")

    elif section == "Заказы":
        st.subheader("Заказы — бесплатно, локально")
        with st.form("max_new_order", clear_on_submit=True):
            o1, o2 = st.columns(2)
            with o1:
                customer = st.text_input("Клиент")
                contact = st.text_input("Контакт")
                product_name = st.text_input("Товар")
            with o2:
                amount = st.text_input("Сумма, ₽")
                status = st.selectbox("Статус", ORDER_STATUSES)
                source = st.selectbox("Источник", ["Manual", "Telegram", "Instagram", "VK", "Другое"])
                attribution_items = [x for x in load_plan() if x.get("status") == "Опубликовано" and x.get("content_id")]
                content_choices = ["Не привязывать"] + [f'{x.get("date","")} · {x.get("platform","")} · {x.get("type","")} · {x.get("product","")}' for x in attribution_items[-50:]]
                selected_content = st.selectbox("Контент / публикация", content_choices, key="max_order_content")
            if st.form_submit_button("➕ Создать заказ", disabled=not can("write_data")):
                content_id = ""
                if selected_content != "Не привязывать":
                    idx = content_choices.index(selected_content) - 1
                    content_id = attribution_items[idx].get("content_id", "")
                create_order(customer, contact, product_name, amount=amount, status=status, source=source, content_id=content_id)
                st.success("Заказ создан.")
                st.rerun()

        if not orders:
            st.info("Заказов пока нет.")
        for order in reversed(orders[-30:]):
            title = f"#{order.get('id','')} · {order.get('customer') or order.get('contact') or 'Клиент'} · {order.get('product') or 'Товар'}"
            with st.expander(title):
                st.write(f"Контакт: {order.get('contact','—')} · Сумма: {order.get('amount','—')} ₽")
                current = order.get("status", "Новая")
                ns = st.selectbox("Статус заказа", ORDER_STATUSES,
                                  index=ORDER_STATUSES.index(current) if current in ORDER_STATUSES else 0,
                                  key=f"max_order_status_{order.get('id')}",
                                  disabled=not can("write_data"))
                if ns != current:
                    update_order(order.get("id"), status=ns)
                    st.rerun()

    elif section == "Автоматизация":
        st.subheader("🚀 Центр автоматизации AI Agent Content Manager")
        st.caption("Бесплатный локальный контур: массовые фото, контент, склад, план на 30 дней, массовое редактирование и аналитика.")

        auto_tab1, auto_tab2, auto_tab3, auto_tab4 = st.tabs(["📸 Фото", "✍️ Контент", "📦 Каталог", "📊 Аналитика"])

        with auto_tab1:
            st.markdown("### Массовая обработка фотографий")
            st.caption("Названия файлов лучше делать по артикулу: например ART-001.jpg. Фото сохраняются отдельно и не заменяют исходный каталог.")
            photo_files = st.file_uploader(
                "Загрузите несколько фото",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
                key="auto_photos",
            )
            if photo_files:
                names = [f.name for f in photo_files]
                st.write("Файлов загружено:", len(names))
                if can("write_data") and st.button("📸 Привязать фото к товарам", type="primary", key="auto_match_photos"):
                    products_now = load_products()
                    matched = 0
                    skipped = []
                    articles = {str(p.get("article","")).strip().lower(): i for i,p in enumerate(products_now) if str(p.get("article","")).strip()}
                    for f in photo_files:
                        stem = Path(f.name).stem.strip().lower()
                        idx = articles.get(stem)
                        if idx is None:
                            for i,p in enumerate(products_now):
                                key = product_key(p, i).lower()
                                if stem == key or stem in key:
                                    idx = i
                                    break
                        if idx is None:
                            skipped.append(f.name)
                            continue
                        try:
                            source_img = Image.open(io.BytesIO(f.getvalue())).convert("RGB")
                            path = save_uploaded_photo(f, products_now[idx], idx)
                            products_now[idx]["original_image"] = path
                            p_now = products_now[idx]
                            card_img = generate_card(
                                source_img,
                                p_now.get("name",""),
                                p_now.get("brand",""),
                                p_now.get("article",""),
                                p_now.get("sizes",""),
                                p_now.get("color",""),
                                p_now.get("description",""),
                                p_now.get("specs",""),
                                p_now.get("category","Другое"),
                                "Dark Premium",
                                get_logo(),
                            )
                            card_buf = io.BytesIO()
                            card_img.save(card_buf, format="PNG")
                            products_now[idx]["card_image"] = base64.b64encode(card_buf.getvalue()).decode("ascii")
                        except Exception as e:
                            skipped.append(f"{f.name}: ошибка обработки ({e})")
                            continue
                        matched += 1
                    save_products(products_now)
                    st.success(f"Готово: привязано {matched}, не найдено {len(skipped)}.")
                    if skipped:
                        st.caption("Не сопоставлены: " + ", ".join(skipped[:20]))
                    st.rerun()

        with auto_tab2:
            st.markdown("### Массовый контент")
            st.caption("Генерация выполняется без платного API. Тексты можно сразу использовать для Instagram, Telegram, VK, Stories и Reels.")
            if not products:
                st.info("Сначала добавьте товары в каталог.")
            else:
                content_limit = st.slider("Сколько товаров обработать", 1, min(100, len(products)), min(30, len(products)), key="auto_content_limit")
                if can("write_data") and st.button("✍️ Создать контент для выбранного количества", type="primary", key="auto_content_btn"):
                    bundles = {}
                    for i,p in enumerate(products[:content_limit]):
                        bundles[str(i)] = {"product": max_product_title(p), "content": content_for_product(p)}
                    st.session_state["auto_content_bundles"] = bundles
                    st.success(f"Контент подготовлен для {content_limit} товаров.")
                bundles = st.session_state.get("auto_content_bundles", {})
                if bundles:
                    for item in list(bundles.values())[:10]:
                        with st.expander(item["product"]):
                            for channel, value in item["content"].items():
                                st.markdown(f"**{channel}**")
                                st.text_area(channel, value, height=120, key=f"auto_text_{item['product']}_{channel}")

            st.markdown("---")
            st.markdown("### 📅 План на 30 дней")
            if can("write_data") and st.button("📅 Создать 30-дневный контент-план", type="primary", key="auto_30_plan"):
                generated = make_30_day_plan(products)
                existing = load_plan()
                existing_keys = {(x.get("date"), x.get("product"), x.get("platform"), x.get("type")) for x in existing}
                added = 0
                for item in generated:
                    key = (item["date"], item["product"], item["platform"], item["type"])
                    if key not in existing_keys:
                        existing.append(item)
                        added += 1
                save_plan(existing)
                st.success(f"Добавлено {added} публикаций без дублей.")
                st.rerun()

        with auto_tab3:
            st.markdown("### Массовое редактирование")
            if products:
                labels = [f"{i+1}. {max_product_title(p)}" for i,p in enumerate(products)]
                selected = st.multiselect("Товары", labels, key="auto_bulk_products")
                selected_indexes = [labels.index(x) for x in selected]
                selected_ids = [_record_id(products[i]) for i in selected_indexes]
                field = st.selectbox("Что изменить", ["category", "brand", "sizes", "color"], key="auto_bulk_field")
                if field == "category":
                    value = st.selectbox("Новое значение", CATEGORIES, key="auto_bulk_value_cat")
                else:
                    value = st.text_input("Новое значение", key="auto_bulk_value_text")
                if can("write_data") and st.button("✏️ Применить к выбранным", type="primary", key="auto_bulk_apply"):
                    if not selected_indexes:
                        st.warning("Выберите хотя бы один товар.")
                    elif not str(value).strip():
                        st.warning("Значение не должно быть пустым.")
                    else:
                        products_now = load_products()
                        indexes = []
                        for fallback_index, record_id in zip(selected_indexes, selected_ids):
                            current_index = _current_product_index(products_now, record_id, fallback_index)
                            if current_index is not None:
                                indexes.append(current_index)
                        changed = bulk_update(products_now, indexes, field, value.strip())
                        save_products(products_now)
                        st.success(f"Изменено товаров: {changed}.")
                        st.rerun()
            else:
                st.info("Каталог пуст.")

            st.markdown("---")
            st.markdown("### 📦 Остатки по размерам")
            if products:
                stock_labels = [f"{i+1}. {max_product_title(p)}" for i,p in enumerate(products)]
                stock_choice = st.selectbox("Товар", stock_labels, key="auto_stock_product")
                stock_idx = stock_labels.index(stock_choice)
                stock_record_id = _record_id(products[stock_idx])
                sp = products[stock_idx]
                current_stock = sp.get("stock_by_size") if isinstance(sp.get("stock_by_size"), dict) else {}
                raw_sizes = str(sp.get("sizes","")).replace(";", ",")
                sizes_list = [x.strip() for x in raw_sizes.split(",") if x.strip()]
                if not sizes_list and current_stock:
                    sizes_list = list(current_stock.keys())
                if not sizes_list:
                    sizes_list = ["S", "M", "L", "XL"]
                st.caption("Введите количество через запятую в порядке размеров: " + ", ".join(sizes_list))
                qty_text = st.text_input("Количество", value=", ".join(str(current_stock.get(s, 0)) for s in sizes_list), key="auto_stock_qty")
                if can("write_data") and st.button("💾 Сохранить остатки", key="auto_stock_save"):
                    parts = [x.strip() for x in qty_text.split(",")]
                    stock = {}
                    for i,size in enumerate(sizes_list):
                        try: stock[size] = max(0, int(parts[i])) if i < len(parts) else 0
                        except Exception: stock[size] = 0
                    products_now = load_products()
                    current_stock_idx = _current_product_index(products_now, stock_record_id, stock_idx)
                    if current_stock_idx is None:
                        st.error("Товар изменился или был удалён. Обновите MAX и повторите.")
                        st.stop()
                    products_now[current_stock_idx]["stock_by_size"] = stock
                    products_now[current_stock_idx]["total_stock"] = sum(stock.values())
                    save_products(products_now)
                    st.success("Остатки сохранены.")
                    st.rerun()

        with auto_tab4:
            st.markdown("### Аналитика магазина")
            a = automation_analytics(products, leads, orders, plan)
            q1,q2,q3,q4 = st.columns(4)
            q1.metric("Товары", a["products"])
            q2.metric("Заявки", a["leads"])
            q3.metric("Заказы", a["orders"])
            q4.metric("В плане", a["plan"])
            st.markdown("**Категории**")
            for name,count in a["categories"]:
                st.write(f"• {name}: {count}")
            st.markdown("**Источники заявок**")
            if a["sources"]:
                for name,count in a["sources"]:
                    st.write(f"• {name}: {count}")
            else:
                st.caption("Пока нет заявок.")
            st.markdown("**Товары в заказах**")
            if a["top_products"]:
                for name,count in a["top_products"]:
                    st.write(f"• {name}: {count}")
            else:
                st.caption("Пока нет заказов.")

            st.markdown("---")
            st.markdown("### 🔎 Умный поиск")
            query = st.text_input("Например: бутсы 42 искусственное поле", key="auto_smart_search")
            if query:
                found = product_search(products, query)
                if found:
                    for p in found[:20]:
                        qty, _, known = stock_info(p)
                        stock_text = str(qty) if known and qty is not None else "не указан"
                        st.write(f"**{max_product_title(p)}** · {p.get('sizes','—')} · остаток: {stock_text}")
                else:
                    st.info("Подходящих товаров не найдено.")

            st.markdown("---")
            st.markdown("### ⚡ Быстрый запуск")
            st.caption("Запускает бесплатный контент-конвейер для всего каталога: тексты + план на 30 дней. Фото остаются отдельным шагом, чтобы не перезаписывать исходные файлы.")
            if can("write_data") and st.button("⚡ Запустить автоматизацию", type="primary", key="auto_run_all"):
                generated = make_30_day_plan(products)
                existing = load_plan()
                existing_keys = {(x.get("date"), x.get("product"), x.get("platform"), x.get("type")) for x in existing}
                for item in generated:
                    key = (item["date"], item["product"], item["platform"], item["type"])
                    if key not in existing_keys:
                        existing.append(item)
                save_plan(existing)
                st.session_state["auto_content_bundles"] = {
                    str(i): {"product": max_product_title(p), "content": content_for_product(p)}
                    for i,p in enumerate(products)
                }
                st.success("Готово: контент подготовлен, план на 30 дней сформирован.")

    elif section == "Склад":
        st.subheader("Склад и контроль остатков")
        if not products:
            st.info("Каталог пуст.")
        else:
            if low:
                st.warning("Товары с остатком 0–2:")
                for p, qty in low:
                    st.write(f"• {max_product_title(p)} — **{qty} шт.**")
            else:
                st.success("Товаров с остатком ≤ 2 нет.")
            st.caption("Товары без заданного остатка не считаются дефицитными.")
            for p in products[:30]:
                qty, _, known = stock_info(p)
                st.write(f"{max_product_title(p)} — {qty if known and qty is not None else '—'} шт.")

    elif section == "Контент":
        st.subheader("Контент без платного AI")
        if not products:
            st.info("Сначала добавьте товар в каталог.")
        else:
            names = [max_product_title(p) for p in products]
            sel = st.selectbox("Товар", names, key="max_content_product")
            p = products[names.index(sel)]
            bundle = content_bundle(p)
            for channel, text_value in bundle.items():
                st.markdown(f"**{channel}**")
                st.text_area(channel, text_value, height=110, key=f"max_bundle_{channel}")
            if can("write_data") and st.button("✨ Создать 7 идей и добавить в план", type="primary", key="max_plan_suggest"):
                suggestions = seven_day_plan(products)
                existing = load_plan()
                for item in suggestions:
                    if not any(x.get("date") == item["date"] and x.get("product") == item["product"] for x in existing):
                        existing.append(item)
                save_plan(existing)
                st.success("План на 7 дней добавлен без дублей.")
                st.rerun()

    else:
        st.subheader("Контент → продажи → AI")
        growth = ai_summary(products, leads, orders, plan)
        f = growth["funnel"]

        x, y, z, q = st.columns(4)
        x.metric("Заявки", f["leads"])
        y.metric("Заказы", f["orders"])
        z.metric("Конверсия", f"{f['lead_to_order']:.1f}%")
        q.metric("Выручка", f"{f['revenue']:,.0f} ₽".replace(",", " "))

        st.caption("Рекомендации строятся локально по данным магазина. При малом объёме данных система показывает направление для теста, а не выдаёт его за доказанный результат.")

        st.markdown("---")
        st.subheader("🤖 Что делать дальше")
        for rec in growth["recommendations"]:
            st.write(f"• {rec}")

        st.markdown("---")
        st.subheader("🎯 AI-фокус: товар · канал · формат")
        a1, a2, a3 = st.columns(3)
        with a1:
            st.markdown("**Товары**")
            for row in growth["products"][:5]:
                st.write(f"**{row['product']}**")
                st.caption(f"Сигнал {row['score']:.2f} · лиды {row['leads']} · заказы {row['orders']} · остаток {row['stock']}")
        with a2:
            st.markdown("**Каналы**")
            for row in growth["channels"]:
                st.write(f"**{row['channel']}**")
                st.caption(f"Сигнал {row['score']:.2f} · публикации {row['published']} · лиды {row['leads']} · заказы {row['orders']}")
        with a3:
            st.markdown("**Форматы**")
            for row in growth["formats"]:
                st.write(f"**{row['format']}**")
                st.caption(f"Сигнал {row['score']:.2f} · публикации {row['published']}")

        st.markdown("---")
        st.subheader("📝 Следующий контент")
        for item in growth["next_content"]:
            st.write(f"• **{item['type']} · {item['channel']}** · {item['product']}")
            st.caption(f"{item['idea']} {item['reason']}")

        st.caption(f"Режим AI: {growth.get('ai_mode', 'local_explainable')} · без платного API.")



    if st.button("Закрыть MAX", key="close_max"):
        st.session_state["open_max"] = False
        st.rerun()


