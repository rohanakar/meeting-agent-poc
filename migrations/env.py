from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from meeting_agent.config import get_settings
from meeting_agent.models import Base

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

# Tests point Alembic at their own database through the config, so it wins over settings.
url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url

with create_engine(url).connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
