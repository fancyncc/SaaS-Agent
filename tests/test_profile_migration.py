import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def upgrade(connection):
    spec = importlib.util.spec_from_file_location('profile_migration', Path(__file__).parents[1] / 'alembic/versions/0015_username_profile.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()


def test_profile_migration_preserves_legacy_and_allows_optional_contacts():
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(160) NOT NULL UNIQUE)')
        connection.exec_driver_sql('CREATE TABLE email_verifications (id VARCHAR(36) PRIMARY KEY)')
        connection.exec_driver_sql("INSERT INTO users VALUES ('old', 'old@example.com')")
        upgrade(connection)
        user = connection.exec_driver_sql('SELECT username, email, phone FROM users').one()
        assert user.username.startswith('u_') and user.email == 'old@example.com' and user.phone is None
        connection.exec_driver_sql("INSERT INTO users(id,username) VALUES ('new1','first'), ('new2','second')")
        assert 'user_id' in {c['name'] for c in sa.inspect(connection).get_columns('email_verifications')}
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO users(id,username) VALUES ('duplicate','FIRST')")
    engine.dispose()
