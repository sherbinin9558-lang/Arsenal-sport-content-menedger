import ast
import unittest
from pathlib import Path


SAAS_CORE = Path(__file__).resolve().parents[1] / "saas_core.py"


class DataSaveSafetyRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SAAS_CORE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_data_save_uses_atomic_batch_rpc(self):
        fn = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "data_save"
        )
        rpc_calls = []
        direct_mutations = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "_rest_post":
                    rpc_calls.append(node)
                if node.func.id in {"_rest_patch", "_rest_delete"}:
                    direct_mutations.append(node)
        self.assertTrue(rpc_calls, "data_save must persist SaaS changes through the atomic RPC")
        self.assertFalse(direct_mutations, "data_save must not perform partial direct mutations")
        self.assertIn("save_app_data_batch", self.source)

    def test_data_save_builds_new_update_and_delete_items(self):
        self.assertIn('"is_new":True', self.source)
        self.assertIn('"is_deleted":True', self.source)
        self.assertIn('"expected_updated_at":baseline[record_id].get("updated_at")', self.source)
        self.assertIn('data_load(entity,[])', self.source)

    def test_atomic_rpc_conflicts_are_mapped_to_data_conflict(self):
        self.assertIn('"DATA_CONFLICT"', self.source)
        self.assertIn('"RECORD_NOT_FOUND"', self.source)
        self.assertIn('"RECORD_ALREADY_EXISTS"', self.source)
        self.assertIn("raise DataConflictError", self.source)


class OnboardingSettingsSafetyTests(unittest.TestCase):
    def test_onboarding_preserves_existing_settings_record_identity(self):
        source = (Path(__file__).resolve().parents[1] / "saas_core.py").read_text(encoding="utf-8")
        self.assertIn('existing=data_load("settings",[])', source)
        self.assertIn('onboarding_payload["_saas_record_id"]=existing[0]["_saas_record_id"]', source)


class SettingsRecordSafetyTests(unittest.TestCase):
    def test_settings_preserve_saas_record_identity(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn('existing = data_load("settings", [])', source)
        self.assertIn('existing[0].get("_saas_record_id")', source)
        self.assertIn('payload["_saas_record_id"] = existing[0]["_saas_record_id"]', source)


class ProductStockRegressionTests(unittest.TestCase):
    def test_product_edit_preserves_stock_and_price_fields(self):
        source = (Path(__file__).resolve().parents[1] / "ui" / "catalog.py").read_text(encoding="utf-8")
        self.assertIn('"price": p.get("price", "")', source)
        self.assertIn('"stock": p.get("stock", 0)', source)
        self.assertIn('"total_stock": p.get("total_stock", p.get("stock", 0))', source)

    def test_product_and_content_updates_merge_existing_fields(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("existing = dict(p[idx] or {})", source)
        self.assertIn("existing.update(updated)", source)
        self.assertIn('existing.pop("_saas_record_id", None)', source)

    def test_stock_helpers_accept_legacy_stock_field(self):
        source = (Path(__file__).resolve().parents[1] / "inventory_core.py").read_text(encoding="utf-8")
        self.assertIn('product.get("total_stock", product.get("stock"))', source)
        automation = (Path(__file__).resolve().parents[1] / "automation_suite.py").read_text(encoding="utf-8")
        self.assertIn("from inventory_core import stock_info as _stock_info", automation)
        free = (Path(__file__).resolve().parents[1] / "free_automation.py").read_text(encoding="utf-8")
        self.assertIn("from inventory_core import stock_info", free)


class OrderSafetyRegressionTests(unittest.TestCase):
    def test_order_identity_and_status_are_validated(self):
        source = (Path(__file__).resolve().parents[1] / "free_automation.py").read_text(encoding="utf-8")
        self.assertIn("ORDER_STATUSES =", source)
        self.assertIn("ORDER_IMMUTABLE_FIELDS =", source)
        self.assertIn("created_at", source)
        self.assertIn('if "status" in changes and changes["status"] not in ORDER_STATUSES:', source)

    def test_order_immutable_fields_are_rejected(self):
        from free_automation import ORDER_IMMUTABLE_FIELDS
        self.assertEqual(ORDER_IMMUTABLE_FIELDS, frozenset({"id", "created_at"}))


class CrmSafetyRegressionTests(unittest.TestCase):
    def test_lead_immutable_fields_are_rejected(self):
        from crm_core import LEAD_IMMUTABLE_FIELDS
        self.assertEqual(LEAD_IMMUTABLE_FIELDS, frozenset({"id", "created_at"}))

    def test_lead_identity_and_status_are_validated(self):
        source = (Path(__file__).resolve().parents[1] / "crm_core.py").read_text(encoding="utf-8")
        self.assertIn("LEAD_IMMUTABLE_FIELDS =", source)
        self.assertIn("created_at", source)
        self.assertIn('if "status" in changes and changes["status"] not in STATUSES:', source)


class RlsSchemaConsistencyRegressionTests(unittest.TestCase):
    def test_invitation_select_policy_is_not_duplicated_in_schema(self):
        source = (Path(__file__).resolve().parents[1] / "supabase_schema.sql").read_text(encoding="utf-8")
        self.assertEqual(source.count('create policy "admins or invitees can read invitations"'), 1)
        self.assertNotIn('create policy "invitees can read own invitations"', source)


class RlsRoleSafetyRegressionTests(unittest.TestCase):
    def test_owner_role_cannot_be_changed_by_member_role_rpc(self):
        source = (Path(__file__).resolve().parents[1] / "supabase_schema.sql").read_text(encoding="utf-8")
        self.assertIn("CANNOT_CHANGE_OWNER_ROLE", source)
        self.assertIn("if target_current_role = 'owner' then", source)
        self.assertIn("for update;", source)


class UploadSafetyRegressionTests(unittest.TestCase):
    def test_product_image_extensions_are_whitelisted(self):
        source = (Path(__file__).resolve().parents[1] / "automation_suite.py").read_text(encoding="utf-8")
        self.assertIn('if ext not in {".jpg", ".jpeg", ".png", ".webp"}:', source)
        self.assertIn("Поддерживаются только JPG, JPEG, PNG и WEBP.", source)


class InventoryRegressionTests(unittest.TestCase):
    def test_stock_source_prefers_size_breakdown_then_total_stock(self):
        from inventory_core import stock_info
        self.assertEqual(stock_info({"stock_by_size": {"42": 2, "43": 3}, "total_stock": 99})[0], 5)
        self.assertEqual(stock_info({"total_stock": 7, "stock": 99})[0], 7)
        self.assertEqual(stock_info({"stock": 4})[0], 4)


class ContentIdentityRegressionTests(unittest.TestCase):
    def test_saas_record_id_is_used_for_stable_content_identity(self):
        source = (Path(__file__).resolve().parents[1] / "content_manager.py").read_text(encoding="utf-8")
        self.assertIn('item.get("_saas_record_id")', source)
        self.assertIn('item.get("content_id")', source)

    def test_existing_content_id_is_preserved(self):
        from content_manager import content_identity
        item = {"content_id": "cnt-existing", "date": "2026-10-01", "idea": "old"}
        self.assertEqual(content_identity(item), "cnt-existing")


class DemoModeRecordRegressionTests(unittest.TestCase):
    def test_record_index_helper_supports_local_mode_indices(self):
        root = Path(__file__).resolve().parents[1]
        catalog = (root / "ui" / "catalog.py").read_text(encoding="utf-8")
        app = (root / "app.py").read_text(encoding="utf-8")
        self.assertIn('record_id = p.get("_saas_record_id") or local_i', catalog)
        self.assertIn("render_catalog(", app)
        self.assertIn("def _find_record_index(", app)


class LargeCatalogPerformanceRegressionTests(unittest.TestCase):
    def test_paged_catalog_api_and_single_record_writes_exist(self):
        source = (Path(__file__).resolve().parents[1] / "saas_core.py").read_text(encoding="utf-8")
        self.assertIn("def data_load_page(", source)
        self.assertIn('Prefer"] = "count=exact"', source)
        self.assertIn("def data_update_record(", source)
        self.assertIn("def data_delete_record(", source)

    def test_catalog_ui_is_paged(self):
        source = (Path(__file__).resolve().parents[1] / "ui" / "catalog.py").read_text(encoding="utf-8")
        self.assertIn("data_load_page", source)
        self.assertIn("На странице", source)
        self.assertIn("catalog_next", source)
        self.assertNotIn("for real_i, p in enumerate(products):", source)


class GrowthEnginePerformanceRegressionTests(unittest.TestCase):
    def test_product_attribution_uses_match_index(self):
        source = (Path(__file__).resolve().parents[1] / "growth_engine.py").read_text(encoding="utf-8")
        self.assertIn("def _build_product_match_index", source)
        self.assertIn("_candidate_product_indexes", source)
        self.assertIn("O(products × leads/orders/content)", source)


class ModularUiRegressionTests(unittest.TestCase):
    def test_ui_modules_exist_and_app_routes_dashboard_settings(self):
        root = Path(__file__).resolve().parents[1]
        dashboard = (root / "ui" / "dashboard.py").read_text(encoding="utf-8")
        settings = (root / "ui" / "settings.py").read_text(encoding="utf-8")
        app = (root / "app.py").read_text(encoding="utf-8")
        self.assertIn("def render_dashboard(", dashboard)
        self.assertIn("def render_settings(", settings)
        self.assertIn("from ui.dashboard import render_dashboard", app)
        self.assertIn("from ui.settings import render_settings", app)
        self.assertNotIn("with tab_dashboard:\n    # Reuse one dashboard snapshot", app)
        self.assertNotIn("with tab7:\n    st.markdown", app)

    def test_auth_cookie_has_reload_recovery(self):
        source = (Path(__file__).resolve().parents[1] / "saas_core.py").read_text(encoding="utf-8")
        self.assertIn('CookieController(key="saas_auth_cookie")', source)
        self.assertIn("getAll()", source)
        self.assertIn("_AUTH_COOKIE_DAYS = 30", source)
        self.assertIn("attempts < 3", source)




class RoleBasedUiRegressionTests(unittest.TestCase):
    def test_write_controls_are_gated_in_catalog_and_content(self):
        root = Path(__file__).resolve().parents[1]
        catalog = (root / "ui" / "catalog.py").read_text(encoding="utf-8")
        app = (root / "app.py").read_text(encoding="utf-8")
        self.assertIn("can_write", catalog)
        self.assertIn('if can_write and st.button("📦 Импортировать товары"', catalog)
        self.assertIn('if can_write and st.button("🗑️ Удалить"', catalog)
        self.assertIn('if can("write_data") and st.form_submit_button("📌 Добавить в workflow")', app)
        self.assertIn('disabled=not can("write_data")', app)

    def test_settings_ui_preserves_identity_and_requires_settings_permission(self):
        root = Path(__file__).resolve().parents[1]
        settings = (root / "ui" / "settings.py").read_text(encoding="utf-8")
        max_ui = (root / "ui" / "max.py").read_text(encoding="utf-8")
        app = (root / "app.py").read_text(encoding="utf-8")
        self.assertIn('st.success("Настройки сохранены.")', settings)
        self.assertIn('if can("settings"):', app)
        self.assertIn('payload["_saas_record_id"] = current["_saas_record_id"]', max_ui)

if __name__ == "__main__":
    unittest.main()
