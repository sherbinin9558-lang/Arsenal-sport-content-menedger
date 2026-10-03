"""Catalog UI: paginated, search-first product management for large catalogs."""

import math


def render_catalog(
    *,
    categories,
    category_emoji,
    data_load_page,
    update_product,
    delete_product,
    bulk_import_products,
    data_conflict_error,
    can_write,
    page_size=50,
):
    st = __import__("streamlit")
    st.markdown(
        '<div class="section-kicker">PRODUCT LIBRARY</div>'
        '<div class="section-title">Каталог</div>'
        '<div class="section-subtitle">Поиск и редактирование работают постранично — приложение не рисует 10 000 товаров одновременно.</div>',
        unsafe_allow_html=True,
    )
    st.info("➕ Для нового товара откройте раздел «＋ Товар».")

    st.markdown("### 📥 Массовая загрузка товаров")
    st.caption("CSV/XLSX импортируется пакетно. Существующие товары можно обновлять по артикулу.")
    bulk_file = st.file_uploader(
        "Файл с товарами",
        type=["csv", "xlsx"],
        key="bulk_products_file",
    )
    bulk_update = st.checkbox(
        "Обновлять существующие товары по артикулу",
        value=False,
        key="bulk_update_existing",
    )
    if can_write and bulk_file and st.button("📦 Импортировать товары", type="primary", key="bulk_import_btn"):
        try:
            added, updated, skipped, errors = bulk_import_products(bulk_file, bulk_update)
            st.success(f"Готово: добавлено {added}, обновлено {updated}, пропущено {skipped}.")
            if errors:
                with st.expander("⚠️ Строки с ошибками"):
                    for err in errors[:50]:
                        st.write(err)
            st.rerun()
        except Exception as e:
            st.error(f"Не удалось импортировать файл: {e}")

    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    with c1:
        search = st.text_input("🔍 Поиск", key="catalog_search", placeholder="Название, бренд или артикул")
    with c2:
        cat_filter = st.selectbox("Категория", ["Все"] + categories, key="catalog_category")
    with c3:
        current_size = st.selectbox("На странице", [25, 50, 100], index=[25, 50, 100].index(page_size) if page_size in (25, 50, 100) else 1, key="catalog_page_size")

    page_key = "catalog_page"
    if st.session_state.get("_catalog_query_signature") != (search, cat_filter, current_size):
        st.session_state[page_key] = 1
        st.session_state["_catalog_query_signature"] = (search, cat_filter, current_size)

    page = max(1, int(st.session_state.get(page_key, 1)))
    try:
        result = data_load_page("products", page=page, page_size=current_size, search=search, category=cat_filter)
    except Exception as e:
        st.error(f"Не удалось загрузить каталог: {e}")
        return

    products = result["rows"]
    total = result["total"]
    pages = max(1, math.ceil(total / current_size))

    if page > pages:
        page = pages
        st.session_state[page_key] = page
        result = data_load_page("products", page=page, page_size=current_size, search=search, category=cat_filter)
        products = result["rows"]
        total = result["total"]

    st.caption(f"Показано {len(products)} из {total} товаров · страница {page} из {pages}")
    if pages > 1:
        nav1, nav2, nav3 = st.columns([1, 2, 1])
        with nav1:
            if st.button("← Назад", disabled=page <= 1, key="catalog_prev"):
                st.session_state[page_key] = page - 1
                st.rerun()
        with nav2:
            chosen_page = st.selectbox("Страница", range(1, pages + 1), index=page - 1, key="catalog_page_number")
            if int(chosen_page) != page:
                st.session_state[page_key] = int(chosen_page)
                st.rerun()
        with nav3:
            if st.button("Вперёд →", disabled=page >= pages, key="catalog_next"):
                st.session_state[page_key] = page + 1
                st.rerun()

    if not products:
        st.info("По заданным условиям товары не найдены.")
        return

    for local_i, p in enumerate(products):
        record_id = p.get("_saas_record_id") or local_i
        key_suffix = str(record_id)
        with st.expander(
            f"{category_emoji.get(p.get('category',''),'📦')} "
            f"{p.get('brand','')} {p.get('name','')} — {p.get('article','')}"
        ):
            edit = st.toggle("✏️ Редактировать", key=f"edit_{key_suffix}") if can_write else False
            if edit:
                ec1, ec2 = st.columns(2)
                with ec1:
                    nn = st.text_input("Название", p.get("name",""), key=f"n_{key_suffix}")
                    nb = st.text_input("Бренд", p.get("brand",""), key=f"b_{key_suffix}")
                    na = st.text_input("Артикул", p.get("article",""), key=f"a_{key_suffix}")
                with ec2:
                    ns = st.text_input("Размеры", p.get("sizes",""), key=f"s_{key_suffix}")
                    nc = st.text_input("Цвет", p.get("color",""), key=f"c_{key_suffix}")
                    ncat = st.selectbox(
                        "Категория",
                        categories,
                        index=categories.index(p.get("category","Другое")) if p.get("category","Другое") in categories else len(categories)-1,
                        key=f"ct_{key_suffix}",
                    )
                nd = st.text_area("Описание", p.get("description",""), key=f"d_{key_suffix}")
                nsp = st.text_input("Характеристики", p.get("specs",""), key=f"sp_{key_suffix}")
                new_original = st.file_uploader(
                    "📷 Исходное фото товара",
                    type=["jpg", "jpeg", "png", "webp"],
                    key=f"orig_{key_suffix}",
                )
                if st.button("💾 Сохранить", key=f"save_{key_suffix}"):
                    updated = {
                        "name": nn, "brand": nb, "article": na, "sizes": ns,
                        "color": nc, "description": nd, "specs": nsp,
                        "category": ncat, "date_added": p.get("date_added", ""),
                        "price": p.get("price", ""),
                        "stock": p.get("stock", 0),
                        "total_stock": p.get("total_stock", p.get("stock", 0)),
                        "stock_by_size": p.get("stock_by_size", {}),
                    }
                    if p.get("card_image"):
                        updated["card_image"] = p["card_image"]
                    if p.get("original_image"):
                        updated["original_image"] = p["original_image"]
                    if new_original:
                        import base64, io
                        from PIL import Image
                        original_buf = io.BytesIO()
                        Image.open(new_original).convert("RGB").save(original_buf, format="JPEG", quality=95)
                        updated["original_image"] = base64.b64encode(original_buf.getvalue()).decode("ascii")
                    try:
                        update_product(record_id, updated, existing=p)
                        st.success("Обновлено.")
                        st.rerun()
                    except data_conflict_error as e:
                        st.warning(str(e))
                    except Exception as e:
                        st.error(f"Не удалось сохранить товар: {e}")
            else:
                st.write(f"**Размеры:** {p.get('sizes','—')}")
                st.write(f"**Цвет:** {p.get('color','—')}")
                st.write(f"**Описание:** {p.get('description','—')}")
                st.write(f"**Характеристики:** {p.get('specs','—')}")
                st.caption(f"Добавлено: {p.get('date_added','—')}")

            if can_write and st.button("🗑️ Удалить", key=f"del_{key_suffix}"):
                try:
                    delete_product(record_id, existing=p)
                    st.rerun()
                except data_conflict_error as e:
                    st.warning(str(e))
                except Exception as e:
                    st.error(f"Не удалось удалить товар: {e}")
