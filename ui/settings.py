"""Store settings UI."""
import streamlit as st

def render_settings(*, load_settings, save_settings, templates, get_logo, save_logo):
    st.markdown('<div class="section-kicker">STORE SETTINGS</div><div class="section-title">Настройки магазина</div><div class="section-subtitle">Основная информация AI Agent Content Manager для контента и работы магазина.</div>', unsafe_allow_html=True)
    settings = load_settings()
    
    st.markdown("### 🏪 Магазин")
    s1, s2 = st.columns(2)
    with s1:
        store_name = st.text_input("Название магазина", value=settings["store_name"], key="settings_store_name")
        city = st.text_input("Город", value=settings["city"], key="settings_city")
        phone = st.text_input("Телефон", value=settings["phone"], key="settings_phone")
        manager_name = st.text_input("Имя менеджера", value=settings["manager_name"], key="settings_manager_name")
        pickup_address = st.text_input("Адрес самовывоза", value=settings["pickup_address"], key="settings_pickup_address")
    with s2:
        order_contact = st.text_input("Контакт для заказа", value=settings["order_contact"], key="settings_order_contact")
        telegram = st.text_input("Telegram", value=settings["telegram"], key="settings_telegram")
        vk = st.text_input("VK", value=settings["vk"], key="settings_vk")
        instagram = st.text_input("Instagram", value=settings["instagram"], key="settings_instagram")
        st.info("Логотип хранится отдельно для каждого магазина.")
    
    st.markdown("### 📦 Заказы и доставка")
    o1, o2 = st.columns(2)
    with o1:
        payment_methods = st.text_input("Способы оплаты", value=settings["payment_methods"], key="settings_payment_methods")
        delivery_methods = st.text_input("Способы доставки", value=settings["delivery_methods"], key="settings_delivery_methods")
        delivery_terms = st.text_input("Сроки доставки", value=settings["delivery_terms"], key="settings_delivery_terms")
    with o2:
        delivery = st.text_input("Доставка", value=settings["delivery"], key="settings_delivery")
        return_policy = st.text_area("Возврат и обмен", value=settings["return_policy"], key="settings_return_policy")
    
    st.markdown("### ✍️ Контент")
    c1, c2 = st.columns(2)
    with c1:
        content_signature = st.text_input("Подпись магазина", value=settings["content_signature"], key="settings_content_signature")
        cta = st.text_input("Призыв к действию", value=settings["cta"], key="settings_cta")
        hashtags = st.text_input("Стандартные хэштеги", value=settings["hashtags"], key="settings_hashtags")
    with c2:
        main_sport = st.selectbox("Основной спорт", ["Футбол", "Баскетбол", "Все виды спорта"], index=["Футбол", "Баскетбол", "Все виды спорта"].index(settings["main_sport"]) if settings["main_sport"] in ["Футбол", "Баскетбол", "Все виды спорта"] else 0, key="settings_main_sport")
        card_template = st.selectbox("Шаблон карточки", list(templates.keys()), index=list(templates.keys()).index(settings["card_template"]) if settings["card_template"] in templates else 0, key="settings_card_template")
        show_price_on_cards = st.selectbox("Показывать цену на карточках", ["Нет", "Да"], index=0 if settings["show_price_on_cards"] == "Нет" else 1, key="settings_show_price")
    
    st.markdown("### 🤖 AI-продавец")
    a1, a2 = st.columns(2)
    with a1:
        ai_tone = st.selectbox("Стиль общения", ["Дружелюбный и профессиональный", "Коротко и по делу", "Более продающий"], index=["Дружелюбный и профессиональный", "Коротко и по делу", "Более продающий"].index(settings["ai_tone"]) if settings["ai_tone"] in ["Дружелюбный и профессиональный", "Коротко и по делу", "Более продающий"] else 0, key="settings_ai_tone")
        ai_required_questions = st.text_input("Что обязательно уточнять", value=settings["ai_required_questions"], key="settings_ai_required")
        ai_no_stock_reply = st.text_area("Если товара нет", value=settings["ai_no_stock_reply"], height=80, key="settings_ai_no_stock")
    with a2:
        ai_escalation_reply = st.text_area("Если нужен менеджер", value=settings["ai_escalation_reply"], height=80, key="settings_ai_escalation")
        notification_contact = st.text_input("Куда отправлять уведомления о заявках", value=settings["notification_contact"], key="settings_notification_contact")
    ai_seller_instructions = st.text_area("Главная инструкция для AI-продавца", value=settings["ai_seller_instructions"], height=110, key="settings_ai_seller")
    
    st.markdown("### ❓ FAQ магазина")
    faq = settings.get("faq", [])
    faq_text = "\n".join(f"{item.get('question','')} | {item.get('answer','')}" for item in faq if isinstance(item, dict))
    faq_input = st.text_area("Вопрос | Ответ — по одному на строку", value=faq_text, height=180, key="settings_faq")
    st.caption("Пример: Как заказать? | Напишите название товара, размер и город.")
    
    if st.button("💾 Сохранить все настройки", type="primary", key="save_store_settings"):
        save_settings({
            "store_name": store_name,
            "city": city,
            "delivery": delivery,
            "telegram": telegram,
            "vk": vk,
            "instagram": instagram,
            "order_contact": order_contact,
            "phone": phone,
            "manager_name": manager_name,
            "pickup_address": pickup_address,
            "payment_methods": payment_methods,
            "return_policy": return_policy,
            "delivery_methods": delivery_methods,
            "delivery_terms": delivery_terms,
            "content_signature": content_signature,
            "cta": cta,
            "hashtags": hashtags,
            "ai_seller_instructions": ai_seller_instructions,
            "notification_contact": notification_contact,
            "card_template": card_template,
            "show_price_on_cards": show_price_on_cards,
            "main_sport": main_sport,
            "ai_tone": ai_tone,
            "ai_required_questions": ai_required_questions,
            "ai_no_stock_reply": ai_no_stock_reply,
            "ai_escalation_reply": ai_escalation_reply,
            "faq": [{"question": line.split("|",1)[0].strip(), "answer": line.split("|",1)[1].strip()} for line in faq_input.splitlines() if "|" in line and line.split("|",1)[0].strip() and line.split("|",1)[1].strip()],
        })
        st.success("Настройки сохранены.")
        st.rerun()
    
