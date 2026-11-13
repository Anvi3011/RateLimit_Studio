import json
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, DateTime, Text, Index
)
from sqlalchemy.sql import func
from .connection import Base

def _utcnow():
    return datetime.now(timezone.utc)

class DemoSession(Base):
    __tablename__ = 'demo_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_active_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), index=True, nullable=False)

    def to_dict(self):
        return {
            'session_id': self.session_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_active_at': self.last_active_at.isoformat() if self.last_active_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None
        }


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
    request_timestamp = Column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    response_timestamp = Column(DateTime(timezone=True), nullable=True)
    response_status = Column(Integer, index=True, nullable=False)
    rate_limit_decision = Column(String(32), index=True, nullable=False)  # 'ALLOWED', 'REJECTED', 'ERROR'
    rejection_reason = Column(Text, nullable=True)
    request_processing_time_ms = Column(Float, nullable=False)
    configured_rate_limit = Column(Integer, nullable=True)
    burst_capacity = Column(Integer, nullable=True)
    window_duration = Column(Integer, nullable=True)
    edge_node_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)

    __table_args__ = (
        Index('idx_req_session_ts', 'demo_session_id', 'request_timestamp'),
        Index('idx_req_session_decision', 'demo_session_id', 'rate_limit_decision'),
        Index('idx_req_session_alg', 'demo_session_id', 'algorithm'),
        Index('idx_req_session_client', 'demo_session_id', 'client_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'request_id': self.request_id,
            'correlation_id': self.correlation_id,
            'demo_session_id': self.demo_session_id,
            'client_id': self.client_id,
            'algorithm': self.algorithm,
            'endpoint': self.endpoint,
            'http_method': self.http_method,
            'request_timestamp': self.request_timestamp.isoformat() if self.request_timestamp else None,
            'response_timestamp': self.response_timestamp.isoformat() if self.response_timestamp else None,
            'response_status': self.response_status,
            'rate_limit_decision': self.rate_limit_decision,
            'rejection_reason': self.rejection_reason,
            'request_processing_time_ms': self.request_processing_time_ms,
            'configured_rate_limit': self.configured_rate_limit,
            'burst_capacity': self.burst_capacity,
            'window_duration': self.window_duration,
            'edge_node_id': self.edge_node_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class ConfigurationLog(Base):
    __tablename__ = 'configuration_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    demo_session_id = Column(String(64), index=True, nullable=False)
    previous_algorithm = Column(String(32), nullable=True)
    new_algorithm = Column(String(32), nullable=False)
    previous_configuration = Column(Text, nullable=True)
    new_configuration = Column(Text, nullable=False)
    changed_at = Column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    change_status = Column(String(32), default='APPLIED', nullable=False)

    __table_args__ = (
        Index('idx_cfg_session_ts', 'demo_session_id', 'changed_at'),
    )

    def to_dict(self):
        def _parse(val):
            if not val:
                return None
            try:
                return json.loads(val)
            except Exception:
                return val

        return {
            'id': self.id,
            'demo_session_id': self.demo_session_id,
            'previous_algorithm': self.previous_algorithm,
            'new_algorithm': self.new_algorithm,
            'previous_configuration': _parse(self.previous_configuration),
            'new_configuration': _parse(self.new_configuration),
            'changed_at': self.changed_at.isoformat() if self.changed_at else None,
            'change_status': self.change_status
        }


class FaultInjectionLog(Base):
    __tablename__ = 'fault_injection_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    demo_session_id = Column(String(64), index=True, nullable=False)
    fault_type = Column(String(64), index=True, nullable=False)
    component_affected = Column(String(64), nullable=False)
    simulation_start_time = Column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    simulation_end_time = Column(DateTime(timezone=True), nullable=True)
    fault_status = Column(String(32), default='ACTIVE', nullable=False)  # 'ACTIVE', 'RECOVERED', 'CLEARED'
    recovery_status = Column(String(32), default='PENDING', nullable=False)  # 'PENDING', 'AUTO_RECOVERED', 'MANUAL_RECOVERED'
    recovery_timestamp = Column(DateTime(timezone=True), nullable=True)
    description = Column(Text, nullable=True)

    __table_args__ = (
        Index('idx_fault_session_ts', 'demo_session_id', 'simulation_start_time'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'demo_session_id': self.demo_session_id,
            'fault_type': self.fault_type,
            'component_affected': self.component_affected,
            'simulation_start_time': self.simulation_start_time.isoformat() if self.simulation_start_time else None,
            'simulation_end_time': self.simulation_end_time.isoformat() if self.simulation_end_time else None,
            'fault_status': self.fault_status,
            'recovery_status': self.recovery_status,
            'recovery_timestamp': self.recovery_timestamp.isoformat() if self.recovery_timestamp else None,
            'description': self.description
        }


class SystemEvent(Base):
    __tablename__ = 'system_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    demo_session_id = Column(String(64), index=True, nullable=True)
    event_type = Column(String(64), index=True, nullable=False)
    component = Column(String(64), index=True, nullable=False)
    severity = Column(String(16), index=True, nullable=False)  # 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    message = Column(Text, nullable=False)
    event_timestamp = Column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    correlation_id = Column(String(64), index=True, nullable=True)
    metadata_json = Column(Text, nullable=True)

    __table_args__ = (
        Index('idx_sys_session_ts', 'demo_session_id', 'event_timestamp'),
        Index('idx_sys_severity_ts', 'severity', 'event_timestamp'),
    )

    def to_dict(self):
        def _parse(val):
            if not val:
                return None
            try:
                return json.loads(val)
            except Exception:
                return val

        return {
            'id': self.id,
            'demo_session_id': self.demo_session_id,
            'event_type': self.event_type,
            'component': self.component,
            'severity': self.severity,
            'message': self.message,
            'event_timestamp': self.event_timestamp.isoformat() if self.event_timestamp else None,
            'correlation_id': self.correlation_id,
            'metadata': _parse(self.metadata_json)
        }
