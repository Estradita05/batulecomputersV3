from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context
from config import DB_URL

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Soportar driver psycopg (v3) con SQLAlchemy
sqlalchemy_url = DB_URL
if sqlalchemy_url.startswith("postgresql://"):
    sqlalchemy_url = sqlalchemy_url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    context.configure(
        url=sqlalchemy_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
