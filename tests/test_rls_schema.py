import unittest
from pathlib import Path


SCHEMA = Path(__file__).resolve().parents[1] / "supabase_schema.sql"


class RlsSchemaRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = SCHEMA.read_text(encoding="utf-8")

    def test_rls_enabled_on_core_tenant_tables(self):
        for table in ("tenants", "memberships", "subscriptions", "app_data"):
            self.assertRegex(
                self.sql,
                rf"alter table public\.{table} enable row level security;",
                table,
            )

    def _function_block(self, name):
        start = self.sql.index(f"create or replace function public.{name}")
        end = self.sql.find("create or replace function public.", start + 1)
        return self.sql[start:] if end == -1 else self.sql[start:end]

    def test_tenant_helpers_are_security_definer_and_locked_down(self):
        for name in ("is_tenant_member", "is_tenant_writer", "is_tenant_admin"):
            block = self._function_block(name).lower()
            self.assertIn("security definer", block, name)
            self.assertIn("set search_path = ''", block, name)
            self.assertIn(
                f"revoke all on function public.{name}(uuid) from public;",
                self.sql,
            )
            self.assertIn(
                f"grant execute on function public.{name}(uuid) to authenticated;",
                self.sql,
            )

    def test_helper_grants_follow_function_definitions(self):
        for name in ("is_tenant_member", "is_tenant_writer", "is_tenant_admin"):
            fn_pos = self.sql.index(f"create or replace function public.{name}")
            grant_pos = self.sql.index(
                f"grant execute on function public.{name}(uuid) to authenticated;"
            )
            self.assertLess(fn_pos, grant_pos, name)

    def test_tenant_helpers_use_fixed_search_path(self):
        for name in ("is_tenant_member", "is_tenant_writer", "is_tenant_admin"):
            block = self._function_block(name).lower()
            self.assertIn("security definer", block, name)
            self.assertIn("set search_path = ''", block, name)

    def test_app_data_policy_matrix_is_present(self):
        required = (
            'for select to authenticated using ((select public.is_tenant_member(tenant_id)));',
            'for insert to authenticated with check ((select public.is_tenant_writer(tenant_id)));',
            'for update to authenticated using ((select public.is_tenant_writer(tenant_id)))',
            'with check ((select public.is_tenant_writer(tenant_id)));',
            'for delete to authenticated using ((select public.is_tenant_writer(tenant_id)));',
        )
        for fragment in required:
            self.assertIn(fragment, self.sql)

    def test_tenant_update_requires_admin(self):
        self.assertIn(
            'for update to authenticated',
            self.sql,
        )
        self.assertIn(
            'using ((select public.is_tenant_admin(id)))',
            self.sql,
        )
        self.assertIn(
            'with check ((select public.is_tenant_admin(id)));',
            self.sql,
        )

    def test_invitation_indexes_are_created_after_table(self):
        table_pos = self.sql.index("create table if not exists public.invitations")
        index_pos = self.sql.index("create index if not exists idx_invitations_invited_by")
        self.assertGreater(index_pos, table_pos)

    def test_atomic_save_migration_has_no_invalid_signature_revoke(self):
        migration = (Path(__file__).resolve().parents[1] / "supabase/migrations/20261004_atomic_app_data_batch_save.sql").read_text(encoding="utf-8")
        self.assertNotIn("save_app_data_batch(uuid,text,jsonb,text[])", migration)
        self.assertIn("grant execute on function public.save_app_data_batch(uuid,text,jsonb) to authenticated;", migration)

    def test_security_definer_functions_pin_empty_search_path(self):
        files = [
            "supabase_schema.sql",
            "supabase/migrations/20261004_atomic_app_data_batch_save.sql",
            "supabase/migrations/20261004_atomic_app_data_conflict_hardening.sql",
            "supabase/migrations/20261003222323_owner_role_protection.sql",
        ]
        for rel in files:
            sql = (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")
            lower = sql.lower()
            pos = 0
            while True:
                pos = lower.find("security definer", pos)
                if pos == -1:
                    break
                line_start = lower.rfind("\n", 0, pos) + 1
                if lower[line_start:pos].lstrip().startswith("--"):
                    pos += len("security definer")
                    continue
                fn_start = lower.rfind("create or replace function public.", 0, pos)
                self.assertGreaterEqual(fn_start, 0, rel)
                fn_end = lower.find("create or replace function public.", pos + len("security definer"))
                block = lower[fn_start:] if fn_end == -1 else lower[fn_start:fn_end]
                self.assertIn("set search_path = ''", block, rel)
                pos += len("security definer")

    def test_fresh_schema_has_valid_dollar_quoting(self):
        self.assertNotRegex(self.sql, r"(?m)^\s*as \$\s*$")
        self.assertIn("as $", self.sql)
        self.assertNotIn("\nas $\n", self.sql)

    def test_fresh_schema_contains_atomic_save_rpc(self):
        self.assertIn("create or replace function public.save_app_data_batch", self.sql)
        self.assertIn("security invoker", self.sql.lower())
        self.assertIn("grant execute on function public.save_app_data_batch(uuid,text,jsonb) to authenticated;", self.sql)

    def test_invited_signup_does_not_create_orphan_tenant(self):
        block = self._function_block("handle_new_user").lower()
        self.assertIn("from public.invitations", block)
        self.assertIn("if invite_id is not null then", block)
        self.assertIn("insert into public.memberships(user_id, tenant_id, role)", block)
        self.assertIn("return new;", block)
        self.assertIn("create or replace function public.handle_new_user", self.sql.lower())

    def test_invitation_acceptance_does_not_overwrite_existing_membership(self):
        block = self._function_block("accept_invitation").lower()
        self.assertIn("update public.invitations set status='accepted' where id=invite_id;", block)
        self.assertNotIn("raise exception 'already_a_member';", block)
        self.assertNotIn("on conflict (user_id, tenant_id) do update set role=excluded.role", self.sql.lower())

    def test_sensitive_invitation_and_checkout_policies_are_authenticated_only(self):
        required = (
            'create policy "admins can cancel invitations" on public.invitations\nfor update to authenticated',
            'create policy "admins can delete invitations" on public.invitations\nfor delete to authenticated',
            'on public.billing_checkout_sessions for select to authenticated',
        )
        for fragment in required:
            self.assertIn(fragment, self.sql)

    def test_member_role_rpc_cannot_demote_owner(self):
        self.assertIn("CANNOT_CHANGE_OWNER_ROLE", self.sql)
        self.assertIn("if target_current_role = 'owner' then", self.sql)
        self.assertIn("grant execute on function public.set_member_role(uuid,uuid,text) to authenticated;", self.sql)


if __name__ == "__main__":
    unittest.main()
