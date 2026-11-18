import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import consumer


def test_ack_only_after_process_returns(monkeypatch):
    channel = Mock()
    order = []
    monkeypatch.setattr(consumer, 'process', lambda *_: order.append('committed'))
    channel.basic_ack.side_effect = lambda *_: order.append('ack')
    consumer.handle_delivery(channel, SimpleNamespace(delivery_tag=1), None,
                             json.dumps({'event_id': 'one'}), Mock())
    assert order == ['committed', 'ack']


def test_database_failure_requeues_even_when_redis_is_down(monkeypatch):
    channel, r = Mock(), Mock()
    r.incr.side_effect = ConnectionError()
    monkeypatch.setattr(consumer, 'process', Mock(side_effect=ConnectionError()))
    with pytest.raises(ConnectionError):
        consumer.handle_delivery(channel, SimpleNamespace(delivery_tag=1), None,
                                 '{"event_id":"one"}', r)
    channel.basic_nack.assert_called_once_with(1, requeue=True)
    channel.basic_ack.assert_not_called()


@pytest.mark.parametrize('body', ['invalid', '{}', '[]', '{"event_id":"x","status":"bad"}'])
def test_poison_message_dead_letters_without_ack(monkeypatch, body):
    channel = Mock()
    monkeypatch.setattr(consumer, 'process', Mock(side_effect=ValueError()))
    consumer.handle_delivery(channel, SimpleNamespace(delivery_tag=2), None, body, Mock())
    channel.basic_nack.assert_called_once_with(2, requeue=False)
    channel.basic_ack.assert_not_called()
