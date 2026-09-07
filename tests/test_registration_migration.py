import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def legacy_schema(connection, duplicate=False):
    connection.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(160) UNIQUE, display_name VARCHAR(100), account_type VARCHAR(20))")
    connection.exec_driver_sql("CREATE TABLE customer_tenants (id VARCHAR(36) PRIMARY KEY, name VARCHAR(120), slug VARCHAR(80) UNIQUE, status VARCHAR(32), settings JSON, created_at DATETIME, updated_at DATETIME)")
    connection.exec_driver_sql("CREATE TABLE tenant_memberships (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), user_id VARCHAR(36), role VARCHAR(40), company_role_code VARCHAR(80), status VARCHAR(32), created_at DATETIME, updated_at DATETIME, UNIQUE (tenant_id,user_id))")
    connection.exec_driver_sql("CREATE UNIQUE INDEX uq_active_company_per_user ON tenant_memberships(user_id) WHERE status='active'")
    connection.exec_driver_sql("CREATE TABLE user_invitations (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), email VARCHAR(160), status VARCHAR(32))")
    connection.exec_driver_sql("CREATE TABLE platform_invitations (id VARCHAR(36) PRIMARY KEY, email VARCHAR(160))")
    connection.exec_driver_sql("INSERT INTO users VALUES ('customer', ' User@Example.com ', '历史用户', 'customer'), ('staff', 'staff@example.com', '平台人员', 'platform')")
    connection.exec_driver_sql("INSERT INTO customer_tenants VALUES ('company', '历史公司', 'legacy', 'active', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    connection.exec_driver_sql("INSERT INTO tenant_memberships VALUES ('membership', 'company', 'customer', 'tenant_admin', 'company_admin', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    if duplicate:
        connection.exec_driver_sql("INSERT INTO users VALUES ('duplicate', 'user@example.com', '重复用户', 'customer')")


def run_upgrade(connection):
    path = Path(__file__).parents[1] / "alembic/versions/0014_open_registration.py"
    spec = importlib.util.spec_from_file_location("registration_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()


def test_legacy_migration_keeps_company_and_backfills_personal():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        legacy_schema(connection)
        run_upgrade(connection)
        users = connection.execute(sa.text("SELECT email, email_verified_at FROM users WHERE id='customer'")).one()
        assert users == ("user@example.com", None)
        spaces = connection.execute(sa.text("SELECT kind, personal_owner_id FROM customer_tenants ORDER BY kind")).all()
        assert spaces == [("company", None), ("personal", "customer")]
        assert connection.execute(sa.text("SELECT count(*) FROM tenant_memberships WHERE user_id='customer' AND status='active'")).scalar_one() == 2
        assert connection.execute(sa.text("SELECT tenant_id FROM tenant_memberships WHERE id='membership'")).scalar_one() == "company"
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(sa.text("INSERT INTO users(id,email) VALUES ('another', 'USER@example.com')"))
    engine.dispose()


def test_normalization_conflicts_abort_before_schema_changes():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        legacy_schema(connection, duplicate=True)
        with pytest.raises(RuntimeError, match="customer,duplicate"):
            run_upgrade(connection)
        assert "email_verified_at" not in {c["name"] for c in sa.inspect(connection).get_columns("users")}
        assert connection.execute(sa.text("SELECT email FROM users WHERE id='customer'")).scalar_one() == " User@Example.com "
    engine.dispose()
