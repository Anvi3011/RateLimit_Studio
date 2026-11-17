import csv
import io
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import create_app
from app.db.connection import Base
from app.db.models import RequestLog
from app.db.service import get_request_logs
from app.session import generate_session_id


def test_csv_exports_more_than_one_api_page(monkeypatch):
    from app.routes import api as routes
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    sid = generate_session_id()
    with Session(engine) as db:
        db.add_all([RequestLog(request_id=uuid.uuid4().hex, demo_session_id=sid,
            client_id='csv-test', algorithm='token_bucket', endpoint='/test',
            response_status=200, rate_limit_decision='ALLOWED', request_processing_time_ms=1)
            for _ in range(123)])
        db.commit()
        def logs(session_id, *args, **kwargs):
            return get_request_logs(session_id, *args, db_session=db, **kwargs)
        monkeypatch.setattr(routes, 'get_request_logs', logs)
        client = create_app({'TESTING': True}).test_client()
        client.set_cookie('demo_session_id', sid)
        # Normal API pagination remains capped at 100.
        assert len(client.get('/api/logs/requests?page_size=1000').json['items']) == 100
        response = client.get('/api/logs/requests/export.csv')
        assert response.status_code == 200
        assert len(list(csv.DictReader(io.StringIO(response.data.decode())))) == 123
    engine.dispose()
