import os
import logging
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, BigInteger, String, Float, DateTime, Integer, Text, Index
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

Base = declarative_base()

class RequestLog(Base):
    __tablename__ = 'request_logs'

    id = Column(Integer().with_variant(BigInteger, "postgresql"), primary_key=True, autoincrement=True)
    request_id = Column(String(64), unique=True, index=True, nullable=False)
    correlation_id = Column(String(64), index=True, nullable=True)
    demo_session_id = Column(String(64), index=True, nullable=False)
    client_id = Column(String(128), index=True, nullable=False)
    algorithm = Column(String(32), index=True, nullable=False)
    endpoint = Column(String(256), index=True, nullable=False)
    http_method = Column(String(10), default='POST', nullable=False)
    request_timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    response_timestamp = Column(DateTime(timezone=True), nullable=True)
    response_status = Column(Integer, index=True, nullable=False)
    rate_limit_decision = Column(String(32), index=True, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    request_processing_time_ms = Column(Float, nullable=False)
    configured_rate_limit = Column(Integer, nullable=True)
    burst_capacity = Column(Integer, nullable=True)
    window_duration = Column(Integer, nullable=True)
    edge_node_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)

_engine = None
_SessionFactory = None
_tables_initialized = False

def get_database_url():
    url = os.getenv('DATABASE_URL')
    if url:
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

from sqlalchemy.pool import StaticPool

def get_engine(db_url=None):
    global _engine
    if _engine is not None and db_url is None:
        return _engine
    url = db_url or get_database_url()
    if url.startswith('sqlite'):
        engine = create_engine(url, connect_args={'check_same_thread': False}, poolclass=StaticPool)
    else:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=1800
        )
    if db_url is None:
        _engine = engine
    return engine

def init_db(engine=None):
    global _tables_initialized
    eng = engine or get_engine()
    Base.metadata.create_all(bind=eng)
    _tables_initialized = True

def get_session(engine=None):
    global _SessionFactory, _tables_initialized
    eng = engine or get_engine()
    if not _tables_initialized:
        try:
            Base.metadata.create_all(bind=eng)
            _tables_initialized = True
        except Exception:
            pass
    if _SessionFactory is None or engine is not None:
        factory = sessionmaker(autocommit=False, autoflush=False, bind=eng)
        if engine is None:
            _SessionFactory = factory
        return factory()
    return _SessionFactory()

def parse_dt(val):
    if not val:
        return None
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace('Z', '+00:00'))
        except Exception:
            return None
    return val
