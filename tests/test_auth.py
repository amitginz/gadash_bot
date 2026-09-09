"""Integration tests for gadash/auth.py and the DB-backed gadash/workers.py —
same real-local-Postgres approach as tests/test_db.py, because tenant
isolation is exactly the kind of thing that's worth verifying against the
real thing.
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql:///gadash_test")

import app as app_module
from gadash import auth, workers as gworkers
from gadash.models_db import Manager, Tenant, Worker, db

# See test_db.py for why this reuses app.py's Flask app instead of a second one.
@pytest.fixture
def app_ctx():
    return app_module.app


@pytest.fixture
def clean_db(mock_gsheet, app_ctx):
    with app_ctx.app_context():
        for model in [Worker, Manager, Tenant]:
            model.query.delete()
        db.session.commit()
        yield


class TestCreateTenant:

    def test_creates_tenant_and_manager(self, app_ctx, clean_db):
        with app_ctx.app_context():
            tenant_id = auth.create_tenant("חוות הדר", "hadar1", "s3cret!!")
            assert tenant_id is not None
            assert auth.verify_manager("hadar1", "s3cret!!")[0] == tenant_id
            assert auth.verify_manager("hadar1", "wrong") is None

    def test_slug_defaults_from_name(self, app_ctx, clean_db):
        with app_ctx.app_context():
            tenant_id = auth.create_tenant("חוות הדר", "u1", "pw123456")
            assert auth.get_tenant_slug(tenant_id)  # non-empty, derived

    def test_explicit_slug_is_used(self, app_ctx, clean_db):
        with app_ctx.app_context():
            tenant_id = auth.create_tenant("חוות הדר", "u2", "pw123456", slug="hadar-farm")
            assert auth.get_tenant_slug(tenant_id) == "hadar-farm"

    def test_duplicate_slug_rejected(self, app_ctx, clean_db):
        with app_ctx.app_context():
            auth.create_tenant("א", "userA", "pw123456", slug="dup")
            with pytest.raises(ValueError):
                auth.create_tenant("ב", "userB", "pw123456", slug="dup")

    def test_duplicate_manager_username_rejected(self, app_ctx, clean_db):
        with app_ctx.app_context():
            auth.create_tenant("א", "sameuser", "pw123456", slug="a")
            with pytest.raises(ValueError):
                auth.create_tenant("ב", "sameuser", "pw123456", slug="b")


class TestVerifyManager:

    def test_unknown_username_fails(self, app_ctx, clean_db):
        with app_ctx.app_context():
            assert auth.verify_manager("nobody", "whatever") is None

    def test_two_tenants_resolve_to_different_ids(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("קבלן א", "mgrA", "pw123456")
            t2 = auth.create_tenant("קבלן ב", "mgrB", "pw123456")
            assert auth.verify_manager("mgrA", "pw123456")[0] == t1
            assert auth.verify_manager("mgrB", "pw123456")[0] == t2
            assert t1 != t2


class TestWorkerLoginByTenantSlug:

    def test_same_worker_name_in_two_tenants_resolves_correctly(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("חווה א", "m1", "pw123456", slug="farm-a")
            t2 = auth.create_tenant("חווה ב", "m2", "pw123456", slug="farm-b")
            gworkers._add_worker(t1, "דני", "worker-pw-1")
            gworkers._add_worker(t2, "דני", "worker-pw-2")

            result_a = auth.verify_worker("farm-a", "דני", "worker-pw-1")
            result_b = auth.verify_worker("farm-b", "דני", "worker-pw-2")
            assert result_a == (t1, "דני")
            assert result_b == (t2, "דני")

    def test_right_name_wrong_tenant_slug_fails(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("חווה א", "m1", "pw123456", slug="farm-a")
            gworkers._add_worker(t1, "דני", "worker-pw")
            assert auth.verify_worker("farm-b-does-not-exist", "דני", "worker-pw") is None

    def test_wrong_password_fails(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("חווה א", "m1", "pw123456", slug="farm-a")
            gworkers._add_worker(t1, "דני", "worker-pw")
            assert auth.verify_worker("farm-a", "דני", "wrong") is None


class TestWorkersModule:

    def test_load_workers_scoped_to_tenant(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("א", "m1", "pw123456", slug="a")
            t2 = auth.create_tenant("ב", "m2", "pw123456", slug="b")
            gworkers._add_worker(t1, "עובד1", "pw")
            gworkers._add_worker(t2, "עובד2", "pw")
            assert [w["שם"] for w in gworkers._load_workers(t1)] == ["עובד1"]
            assert [w["שם"] for w in gworkers._load_workers(t2)] == ["עובד2"]

    def test_add_duplicate_name_in_same_tenant_fails(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("א", "m1", "pw123456", slug="a")
            assert gworkers._add_worker(t1, "דני", "pw") is True
            assert gworkers._add_worker(t1, "דני", "pw2") is False

    def test_same_name_allowed_across_different_tenants(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("א", "m1", "pw123456", slug="a")
            t2 = auth.create_tenant("ב", "m2", "pw123456", slug="b")
            assert gworkers._add_worker(t1, "דני", "pw") is True
            assert gworkers._add_worker(t2, "דני", "pw") is True

    def test_delete_worker_only_affects_that_tenant(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("א", "m1", "pw123456", slug="a")
            t2 = auth.create_tenant("ב", "m2", "pw123456", slug="b")
            gworkers._add_worker(t1, "דני", "pw")
            gworkers._add_worker(t2, "דני", "pw")
            assert gworkers._delete_worker(t1, "דני") is True
            assert [w["שם"] for w in gworkers._load_workers(t1)] == []
            assert [w["שם"] for w in gworkers._load_workers(t2)] == ["דני"]

    def test_telegram_lookup_is_global_and_returns_tenant(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("א", "m1", "pw123456", slug="a")
            gworkers._add_worker(t1, "דני", "pw")
            gworkers._link_worker_telegram(t1, "דני", 555555)
            found = gworkers._get_worker_by_telegram_id(555555)
            assert found == {"שם": "דני", "tenant_id": t1}
            assert gworkers._get_worker_by_telegram_id(999999) is None

    def test_whatsapp_lookup_is_global_and_returns_tenant(self, app_ctx, clean_db):
        with app_ctx.app_context():
            t1 = auth.create_tenant("א", "m1", "pw123456", slug="a")
            gworkers._add_worker(t1, "דני", "pw")
            gworkers._link_worker_whatsapp(t1, "דני", "972501234567")
            found = gworkers._get_worker_by_whatsapp_number("972501234567")
            assert found == {"שם": "דני", "tenant_id": t1}
            assert gworkers._get_worker_by_whatsapp_number("972500000000") is None

    def test_telegram_and_whatsapp_links_are_independent(self, app_ctx, clean_db):
        # A worker can be linked on one channel, both, or neither — the two
        # columns don't interfere with each other.
        with app_ctx.app_context():
            t1 = auth.create_tenant("א", "m1", "pw123456", slug="a")
            gworkers._add_worker(t1, "דני", "pw")
            gworkers._link_worker_telegram(t1, "דני", 555555)
            assert gworkers._get_worker_by_whatsapp_number("972501234567") is None
            gworkers._link_worker_whatsapp(t1, "דני", "972501234567")
            assert gworkers._get_worker_by_telegram_id(555555) == {"שם": "דני", "tenant_id": t1}
            assert gworkers._get_worker_by_whatsapp_number("972501234567") == {"שם": "דני", "tenant_id": t1}
