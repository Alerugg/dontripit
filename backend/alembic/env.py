import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models import Base
from app import catalog_release_models  # noqa: F401 - register release tables on Base.metadata
from app import search_v2_models  # noqa: F401 - register Search V2 tables on Base.metadata
from app import multilingual_models  # noqa: F401 - register print localization tables on Base.metadata
from app import onepiece_don_models  # noqa: F401 - register One Piece DON tables on Base.metadata


config = context.config


def resolve_migration_database_url() -> tuple[str, str]:
    if os.getenv("DATABASE_URL_UNPOOLED"):
        return os.environ["DATABASE_URL_UNPOOLED"], "DATABASE_URL_UNPOOLED"

    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"], "DATABASE_URL"

    return "postgresql+psycopg2://localhost:5432/appdb", "default(localhost)"


migration_database_url, source_var = resolve_migration_database_url()
config.set_main_option("sqlalchemy.url", migration_database_url)
print(f"[alembic] Using {source_var} for database connection")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


run_migrations_online() if not context.is_offline_mode() else run_migrations_offline()
