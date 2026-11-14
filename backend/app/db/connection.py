import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, scoped_session
from sqlalchemy.pool import QueuePool, NullPool, StaticPool

logger = logging.getLogger(__name__)

Base = declarative_base()
_engine = None
_SessionFactory = None

def get_database_url():
    url = os.getenv('DATABASE_URL')
    if url:
        # Handle postgres:// vs postgresql:// for SQLAlchemy compatibility
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        return url

    user = os.getenv('POSTGRES_USER', 'ratelimit_user')
    password = os.getenv('POSTGRES_PASSWORD', '')
    host = os.getenv('POSTGRES_HOST', 'postgres')
    port = os.getenv('POSTGRES_PORT', '5432')
    db = os.getenv('POSTGRES_DB', 'ratelimit_studio')

    if password:
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return "sqlite:///:memory:"

def get_engine(db_url=None):
    global _engine
    if _engine is not None and db_url is None:
        return _engine

    url = db_url or get_database_url()
    try:
        if url.startswith('sqlite'):
            engine = create_engine(url, connect_args={'check_same_thread': False}, poolclass=StaticPool)
        else:
            engine = create_engine(
                url,
                poolclass=QueuePool,
                pool_size=int(os.getenv('DB_POOL_SIZE', '10')),
                max_overflow=int(os.getenv('DB_MAX_OVERFLOW', '20')),
                pool_timeout=int(os.getenv('DB_POOL_TIMEOUT', '5')),
                pool_recycle=int(os.getenv('DB_POOL_RECYCLE', '1800')),
                pool_pre_ping=True
            )
        if db_url is None:
            _engine = engine
        return engine
    except Exception as e:
        logger.error("Failed to create database engine: %s", e)
        raise

def get_session_factory(engine=None):
    global _SessionFactory
    if _SessionFactory is not None and engine is None:
        return _SessionFactory

    eng = engine or get_engine()
    factory = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=eng))
    if engine is None:
        _SessionFactory = factory
    return factory

def get_db_session():
    factory = get_session_factory()
    return factory()

def init_db(engine=None):
    """Create all tables idempotently."""
    eng = engine or get_engine()
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=eng)
    logger.info("Database initialized successfully.")
