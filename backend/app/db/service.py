import json
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import func, or_, desc
from .connection import get_session_factory
from .models import DemoSession, RequestLog, ConfigurationLog, FaultInjectionLog, SystemEvent

logger = logging.getLogger(__name__)

def _utcnow():
    return datetime.now(timezone.utc)

def touch_session(session_id, retention_hours=24, db_session=None):
    """Ensure session exists and update activity/expiration."""
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        now = _utcnow()
        expires = now + timedelta(hours=retention_hours)
        ds = session.query(DemoSession).filter_by(session_id=session_id).first()
        if not ds:
            ds = DemoSession(
                session_id=session_id,
                created_at=now,
                last_active_at=now,
                expires_at=expires
            )
            session.add(ds)
            # Log system event for session creation
            event = SystemEvent(
                demo_session_id=session_id,
                event_type='SESSION_CREATED',
                component='SessionManager',
                severity='INFO',
                message=f'Public demo session {session_id[:16]}... created',
                event_timestamp=now
            )
            session.add(event)
        else:
            ds.last_active_at = now
            ds.expires_at = expires
        session.commit()
        return ds
    except Exception as e:
        session.rollback()
        logger.warning("touch_session failed: %s", e)
        return None
    finally:
        if close:
            session.close()

def log_configuration_change(session_id, prev_alg, new_alg, prev_cfg, new_cfg, status='APPLIED', db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        now = _utcnow()
        entry = ConfigurationLog(
            demo_session_id=session_id,
            previous_algorithm=prev_alg,
            new_algorithm=new_alg,
            previous_configuration=json.dumps(prev_cfg) if isinstance(prev_cfg, dict) else prev_cfg,
            new_configuration=json.dumps(new_cfg) if isinstance(new_cfg, dict) else new_cfg,
            changed_at=now,
            change_status=status
        )
        session.add(entry)

        # System event
        event = SystemEvent(
            demo_session_id=session_id,
            event_type='CONFIGURATION_APPLIED',
            component='ConfigManager',
            severity='INFO',
            message=f'Rate limit policy updated to {new_alg} (rate: {new_cfg.get("rate")}, burst: {new_cfg.get("burst")})',
            event_timestamp=now,
            metadata_json=json.dumps({'algorithm': new_alg, 'rate': new_cfg.get('rate'), 'burst': new_cfg.get('burst')})
        )
        session.add(event)
        session.commit()
        return entry.to_dict()
    except Exception as e:
        session.rollback()
        logger.error("log_configuration_change failed: %s", e)
        return None
    finally:
        if close:
            session.close()

def log_fault_injection(session_id, fault_type, component, duration_seconds=60, description=None, db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        now = _utcnow()
        end_time = now + timedelta(seconds=duration_seconds)
        entry = FaultInjectionLog(
            demo_session_id=session_id,
            fault_type=fault_type,
            component_affected=component,
            simulation_start_time=now,
            simulation_end_time=end_time,
            fault_status='ACTIVE',
            recovery_status='PENDING',
            description=description or f'Simulated {fault_type} failure on {component} (duration: {duration_seconds}s)'
        )
        session.add(entry)

        event = SystemEvent(
            demo_session_id=session_id,
            event_type='FAULT_INJECTION_TRIGGERED',
            component='FaultManager',
            severity='WARNING',
            message=f'Injected simulated fault: {fault_type} on {component}',
            event_timestamp=now,
            metadata_json=json.dumps({'fault_type': fault_type, 'duration_seconds': duration_seconds})
        )
        session.add(event)
        session.commit()
        return entry.to_dict()
    except Exception as e:
        session.rollback()
        logger.error("log_fault_injection failed: %s", e)
        return None
    finally:
        if close:
            session.close()

def log_fault_recovery(session_id, fault_type=None, recovery_status='MANUAL_RECOVERED', db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        now = _utcnow()
        query = session.query(FaultInjectionLog).filter(
            FaultInjectionLog.demo_session_id == session_id,
            FaultInjectionLog.fault_status == 'ACTIVE'
        )
        if fault_type:
            query = query.filter(FaultInjectionLog.fault_type == fault_type)

        active_faults = query.all()
        for f in active_faults:
            f.fault_status = 'CLEARED'
            f.recovery_status = recovery_status
            f.recovery_timestamp = now

        event = SystemEvent(
            demo_session_id=session_id,
            event_type='FAULT_INJECTION_RECOVERED',
            component='FaultManager',
            severity='INFO',
            message=f'Simulated fault(s) restored: {fault_type or "all"}',
            event_timestamp=now,
            metadata_json=json.dumps({'fault_type': fault_type, 'recovery_status': recovery_status})
        )
        session.add(event)
        session.commit()
        return len(active_faults)
    except Exception as e:
        session.rollback()
        logger.error("log_fault_recovery failed: %s", e)
        return 0
    finally:
        if close:
            session.close()

def log_system_event(event_type, component, severity, message, session_id=None, correlation_id=None, metadata=None, db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        now = _utcnow()
        event = SystemEvent(
            demo_session_id=session_id,
            event_type=event_type,
            component=component,
            severity=severity,
            message=message,
            event_timestamp=now,
            correlation_id=correlation_id,
            metadata_json=json.dumps(metadata) if isinstance(metadata, dict) else metadata
        )
        session.add(event)
        session.commit()
        return event.to_dict()
    except Exception as e:
        session.rollback()
        logger.error("log_system_event failed: %s", e)
        return None
    finally:
        if close:
            session.close()

def get_logs_summary(session_id, db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        total = session.query(func.count(RequestLog.id)).filter(RequestLog.demo_session_id == session_id).scalar() or 0
        allowed = session.query(func.count(RequestLog.id)).filter(
            RequestLog.demo_session_id == session_id,
            RequestLog.rate_limit_decision == 'ALLOWED'
        ).scalar() or 0
        rejected = session.query(func.count(RequestLog.id)).filter(
            RequestLog.demo_session_id == session_id,
            RequestLog.rate_limit_decision == 'REJECTED'
        ).scalar() or 0
        failed = session.query(func.count(RequestLog.id)).filter(
            RequestLog.demo_session_id == session_id,
            or_(RequestLog.response_status >= 500, RequestLog.rate_limit_decision == 'ERROR')
        ).scalar() or 0
        config_changes = session.query(func.count(ConfigurationLog.id)).filter(
            ConfigurationLog.demo_session_id == session_id
        ).scalar() or 0
        faults_count = session.query(func.count(FaultInjectionLog.id)).filter(
            FaultInjectionLog.demo_session_id == session_id
        ).scalar() or 0

        return {
            'total_requests': total,
            'allowed_requests': allowed,
            'rejected_requests': rejected,
            'failed_operations': failed,
            'total_config_changes': config_changes,
            'total_fault_simulations': faults_count
        }
    except Exception as e:
        logger.error("get_logs_summary failed: %s", e)
        return {
            'total_requests': 0,
            'allowed_requests': 0,
            'rejected_requests': 0,
            'failed_operations': 0,
            'total_config_changes': 0,
            'total_fault_simulations': 0
        }
    finally:
        if close:
            session.close()

def get_request_logs(session_id, filters=None, page=1, page_size=50, db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    filters = filters or {}
    try:
        query = session.query(RequestLog).filter(RequestLog.demo_session_id == session_id)

        if filters.get('algorithm'):
            query = query.filter(RequestLog.algorithm == filters['algorithm'])
        if filters.get('decision'):
            query = query.filter(RequestLog.rate_limit_decision == filters['decision'].upper())
        if filters.get('client_id'):
            query = query.filter(RequestLog.client_id.ilike(f"%{filters['client_id']}%"))
        if filters.get('endpoint'):
            query = query.filter(RequestLog.endpoint.ilike(f"%{filters['endpoint']}%"))
        if filters.get('request_id'):
            query = query.filter(RequestLog.request_id.ilike(f"%{filters['request_id']}%"))
        if filters.get('status'):
            try:
                query = query.filter(RequestLog.response_status == int(filters['status']))
            except (ValueError, TypeError):
                pass
        if filters.get('from_timestamp'):
            try:
                ts = datetime.fromisoformat(filters['from_timestamp'].replace('Z', '+00:00'))
                query = query.filter(RequestLog.request_timestamp >= ts)
            except Exception:
                pass
        if filters.get('to_timestamp'):
            try:
                ts = datetime.fromisoformat(filters['to_timestamp'].replace('Z', '+00:00'))
                query = query.filter(RequestLog.request_timestamp <= ts)
            except Exception:
                pass

        total = query.count()
        page = max(1, int(page))
        page_size = min(100, max(1, int(page_size)))
        rows = query.order_by(desc(RequestLog.request_timestamp)).offset((page - 1) * page_size).limit(page_size).all()

        return {
            'items': [r.to_dict() for r in rows],
            'total': total,
            'page': page,
            'page_size': page_size
        }
    except Exception as e:
        logger.error("get_request_logs failed: %s", e)
        return {'items': [], 'total': 0, 'page': page, 'page_size': page_size}
    finally:
        if close:
            session.close()

def get_request_log_by_id(session_id, request_id, db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        row = session.query(RequestLog).filter(
            RequestLog.demo_session_id == session_id,
            RequestLog.request_id == request_id
        ).first()
        return row.to_dict() if row else None
    except Exception as e:
        logger.error("get_request_log_by_id failed: %s", e)
        return None
    finally:
        if close:
            session.close()

def get_configuration_logs(session_id, page=1, page_size=50, db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        query = session.query(ConfigurationLog).filter(ConfigurationLog.demo_session_id == session_id)
        total = query.count()
        page = max(1, int(page))
        page_size = min(100, max(1, int(page_size)))
        rows = query.order_by(desc(ConfigurationLog.changed_at)).offset((page - 1) * page_size).limit(page_size).all()
        return {
            'items': [r.to_dict() for r in rows],
            'total': total,
            'page': page,
            'page_size': page_size
        }
    except Exception as e:
        logger.error("get_configuration_logs failed: %s", e)
        return {'items': [], 'total': 0, 'page': page, 'page_size': page_size}
    finally:
        if close:
            session.close()

def get_fault_logs(session_id, page=1, page_size=50, db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        query = session.query(FaultInjectionLog).filter(FaultInjectionLog.demo_session_id == session_id)
        total = query.count()
        page = max(1, int(page))
        page_size = min(100, max(1, int(page_size)))
        rows = query.order_by(desc(FaultInjectionLog.simulation_start_time)).offset((page - 1) * page_size).limit(page_size).all()
        return {
            'items': [r.to_dict() for r in rows],
            'total': total,
            'page': page,
            'page_size': page_size
        }
    except Exception as e:
        logger.error("get_fault_logs failed: %s", e)
        return {'items': [], 'total': 0, 'page': page, 'page_size': page_size}
    finally:
        if close:
            session.close()

def get_system_events(session_id=None, severity=None, component=None, page=1, page_size=50, db_session=None):
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        query = session.query(SystemEvent)
        if session_id:
            query = query.filter(or_(SystemEvent.demo_session_id == session_id, SystemEvent.demo_session_id.is_(None)))
        if severity and severity.upper() != 'ALL':
            query = query.filter(SystemEvent.severity == severity.upper())
        if component and component.upper() != 'ALL':
            query = query.filter(SystemEvent.component.ilike(f"%{component}%"))

        total = query.count()
        page = max(1, int(page))
        page_size = min(100, max(1, int(page_size)))
        rows = query.order_by(desc(SystemEvent.event_timestamp)).offset((page - 1) * page_size).limit(page_size).all()
        return {
            'items': [r.to_dict() for r in rows],
            'total': total,
            'page': page,
            'page_size': page_size
        }
    except Exception as e:
        logger.error("get_system_events failed: %s", e)
        return {'items': [], 'total': 0, 'page': page, 'page_size': page_size}
    finally:
        if close:
            session.close()

def cleanup_expired_sessions(retention_hours=24, db_session=None):
    """Safely purge expired demo sessions and their associated logs.
    Active sessions are NEVER deleted."""
    session = db_session or get_session_factory()()
    close = db_session is None
    try:
        now = _utcnow()
        expired_sessions = session.query(DemoSession.session_id).filter(
            DemoSession.expires_at < now
        ).all()
        expired_ids = [s[0] for s in expired_sessions]
        if not expired_ids:
            return {'expired_sessions_purged': 0, 'logs_purged': 0}

        deleted_reqs = session.query(RequestLog).filter(RequestLog.demo_session_id.in_(expired_ids)).delete(synchronize_session=False)
        deleted_cfgs = session.query(ConfigurationLog).filter(ConfigurationLog.demo_session_id.in_(expired_ids)).delete(synchronize_session=False)
        deleted_faults = session.query(FaultInjectionLog).filter(FaultInjectionLog.demo_session_id.in_(expired_ids)).delete(synchronize_session=False)
        deleted_events = session.query(SystemEvent).filter(SystemEvent.demo_session_id.in_(expired_ids)).delete(synchronize_session=False)
        deleted_sessions = session.query(DemoSession).filter(DemoSession.session_id.in_(expired_ids)).delete(synchronize_session=False)

        session.commit()
        total_logs = deleted_reqs + deleted_cfgs + deleted_faults + deleted_events
        logger.info("Purged %d expired sessions and %d logs", deleted_sessions, total_logs)
        return {
            'expired_sessions_purged': deleted_sessions,
            'logs_purged': total_logs
        }
    except Exception as e:
        session.rollback()
        logger.error("cleanup_expired_sessions failed: %s", e)
        return {'expired_sessions_purged': 0, 'logs_purged': 0, 'error': str(e)}
    finally:
        if close:
            session.close()
