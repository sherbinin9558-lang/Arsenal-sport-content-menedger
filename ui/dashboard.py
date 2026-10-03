"""Dashboard UI. Keeps dashboard rendering outside the application entrypoint."""
import datetime
import streamlit as st

def render_dashboard(
    *,
    load_products, load_plan, load_leads, load_orders,
    crm_metrics, order_metrics, conversion_metrics, workflow_metrics,
    low_stock_products, growth_recommendations, max_product_title,
    attribution_loader, attribution_metrics, add_product,
    categories, can_write,
):
    # Reuse one dashboard snapshot so opening MAX does not trigger another
    # round of Supabase reads.
    if "max_data_snapshot" not in st.session_state:
        st.session_state["max_data_snapshot"] = {
            "products": load_products(),
            "plan": load_plan(),
            "leads": load_leads(),
            "orders": load_orders(),
        }
    snap = st.session_state["max_data_snapshot"]
    products = snap["products"]
    plan = snap["plan"]
    leads = snap["leads"]
    orders = snap["orders"]
    crm = crm_metrics(leads)
    om = order_metrics(orders, {"Новая", "Связались", "Ожидает оплаты", "Оплачен", "Собирается", "Отправлен"})
    cm = conversion_metrics(leads, orders)
    wm = workflow_metrics(plan)
    low = low_stock_products(products)
    due, overdue = [], []
    today = datetime.date.today()
    for item in plan:
        try:
            item_date = datetime.date.fromisoformat(str(item.get("date", "")))
            if item.get("status") != "Опубликовано":
                (overdue if item_date < today else due if item_date == today else []).append(item)
        except Exception:
            pass
    
    store_name = st.session_state.get("saas_tenant_name", "Ваш магазин")
    first_run = len(products) == 0 and len(leads) == 0 and len(orders) == 0
    try:
        ai_next = growth_recommendations(products, leads, orders, plan)
    except Exception:
        ai_next = []
    
    st.markdown(
        f'<div class="dashboard-hero">'
        f'<div class="dashboard-hero-kicker">AI BUSINESS COMMAND CENTER</div>'
        f'<div class="dashboard-hero-title">Добро пожаловать в {store_name}</div>'
        f'<div class="dashboard-hero-text">MAX смотрит на каталог, контент и продажи и помогает решить следующую задачу — без лишней рутины.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    
    if first_run:
        st.markdown(
            '<div class="first-run-card"><div class="first-run-kicker">ПЕРВЫЙ ЗАПУСК</div>'
            '<div class="first-run-title">Запустим магазин за несколько шагов</div>'
            '<div class="first-run-text">Добавьте первый товар — затем MAX поможет создать контент и подготовить следующий шаг.</div></div>',
            unsafe_allow_html=True,
        )
        fr1, fr2, fr3 = st.columns(3)
        fr1.metric("Шаг 1", "Магазин ✓")
        fr2.metric("Шаг 2", "Первый товар", "сейчас")
        fr3.metric("Шаг 3", "Первый контент", "после товара")
        if can_write:
            with st.expander("Добавить первый товар прямо сейчас", expanded=st.session_state.get("first_run_quick_add", True)):
                with st.form("first_run_product_form", clear_on_submit=True):
                    q1, q2 = st.columns(2)
                    with q1:
                        fr_name = st.text_input("Название товара", placeholder="Футбольная форма")
                        fr_brand = st.text_input("Бренд", placeholder="Nike")
                        fr_article = st.text_input("Артикул", placeholder="ART-001")
                    with q2:
                        fr_category = st.selectbox("Категория", categories)
                        fr_price = st.text_input("Цена, ₽", placeholder="4990")
                        fr_stock = st.number_input("Остаток", min_value=0, value=1, step=1)
                    fr_submit = st.form_submit_button("Создать товар и передать его MAX", type="primary", use_container_width=True)
                if fr_submit:
                    if not fr_name.strip():
                        st.error("Укажите название товара.")
                    else:
                        try:
                            add_product({
                                "name": fr_name.strip(), "brand": fr_brand.strip(), "article": fr_article.strip(),
                                "category": fr_category, "price": fr_price.strip(), "stock": int(fr_stock),
                                "sizes": "", "color": "", "description": "", "specs": "",
                                "card_image": "", "date_added": str(datetime.date.today())
                            })
                            st.success("Товар создан. MAX уже может использовать его для контента и рекомендаций.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Не удалось создать товар: {e}")
        else:
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("Продажи", f'{om["amount"]:,.0f} ₽'.replace(",", " "))
        d2.metric("Заявки", crm.get("total", 0))
        d3.metric("Заказы", om.get("total", 0))
        d4.metric("Конверсия", f'{cm["conversion"]:.1f}%')
        d5.metric("Товаров", len(products))
    
        q1, q2, q3 = st.columns(3)
        with q1:
            st.markdown("### Следующий шаг")
            if ai_next:
                st.write(ai_next[0])
            else:
                st.write("MAX пока собирает данные.")
        with q2:
            st.markdown("### Сегодня")
            st.write(f"Контент: **{len(due)}** · Просрочено: **{len(overdue)}**")
            st.write(f"Активные заказы: **{om['active']}**")
        with q3:
            st.markdown("### Состояние")
            st.write(f"Каталог: **{len(products)}** товаров")
            st.write(f"Заявки: **{crm.get('total',0)}** · Заказы: **{om.get('total',0)}**")
    
    st.markdown("---")
    left, right = st.columns(2)
    with left:
        st.markdown("### Контент и задачи")
        c1, c2, c3 = st.columns(3)
        c1.metric("В плане", wm.get("total", 0))
        c2.metric("На проверке", wm.get("counts", {}).get("На проверке", 0))
        c3.metric("Просрочено", wm.get("overdue", 0))
        if overdue:
            st.warning(f"Просроченных публикаций: {len(overdue)}")
        if due:
            st.info(f"На сегодня запланировано: {len(due)}")
        if not overdue and not due:
            st.success("Срочных контент-задач нет.")
    
    with right:
        st.markdown("### Склад")
        s1, s2 = st.columns(2)
        s1.metric("Позиций в каталоге", len(products))
        s2.metric("Низкий остаток", len(low))
        if low:
            for product, qty in low[:8]:
                st.write(f'• {max_product_title(product)} — {qty} шт.')
        else:
            st.success("Дефицитных позиций нет.")
    
    st.markdown("---")
    a, b = st.columns(2)
    with a:
        st.markdown("### Продажи")
        st.write(f'Активных заказов: **{om["active"]}**')
        st.write(f'Завершённых заказов: **{om["completed"]}**')
        st.write(f'Отменённых заказов: **{om["cancelled"]}**')
        st.write(f'Сумма неотменённых заказов: **{om["amount"]:,.0f} ₽**'.replace(",", " "))
    with st.expander("🔗 Контент → продажи", expanded=True):
        attribution = attribution_loader()
        st.caption("Новый слой атрибуции не меняет существующие заказы и CRM. Он готовит связь публикация → товар → лид → заказ.")
        if not attribution:
            st.info("Пока нет событий атрибуции. Существующий контент и продажи продолжают работать без изменений.")
        else:
            stats = attribution_metrics(attribution, leads, orders)
            if stats:
                for product_key, row in list(stats.items())[:8]:
                    st.write(f"• {product_key}: контента {row['content']}, заявок {row['leads']}, заказов {row['orders']}")
    
    with b:
        st.markdown("### Быстрые действия")
        if st.button("➕ Добавить товар", key="dash_add_product", use_container_width=True):
            st.info("Откройте раздел «Создать» — там можно сразу загрузить фото и создать карточку.")
        if st.button("📅 Открыть контент-план", key="dash_open_plan", use_container_width=True):
            st.info("Откройте раздел «План» для управления публикациями и workflow.")
    
    st.markdown("---")
    st.markdown("### Состояние системы")
    checks = [
        ("Каталог", bool(products), f'{len(products)} товаров'),
        ("CRM", True, f'{len(leads)} заявок'),
        ("Заказы", True, f'{len(orders)} заказов'),
        ("Контент workflow", True, f'{len(plan)} материалов'),
        ("Склад", True, f'{len(low)} позиций с низким остатком'),
    ]
    for name, ok, detail in checks:
        st.write(("🟢" if ok else "🟡") + f" **{name}** — {detail}")
    
