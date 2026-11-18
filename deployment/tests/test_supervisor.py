import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock
import pytest

spec = importlib.util.spec_from_file_location('studio_supervisor',
    Path(__file__).parents[1] / 'public/supervisor.py')
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def child():
    process = Mock()
    process.poll.return_value = None
    return process


def test_single_process_per_role_reap_and_backoff(tmp_path):
    now = [0]
    first, second = child(), child()
    popen = Mock(side_effect=[first, second])
    manager = supervisor.Supervisor({'worker': ['python', 'consumer.py']}, tmp_path / 'state',
                                    popen=popen, clock=lambda: now[0])
    manager.tick()
    manager.tick()
    assert popen.call_count == 1
    first.poll.return_value = 1
    manager.tick()
    first.wait.assert_called_once()
    assert not json.loads((tmp_path / 'state').read_text())['running']['worker']
    now[0] = 2
    manager.tick()
    assert popen.call_count == 2
    assert json.loads((tmp_path / 'state').read_text())['running']['worker']


def test_shutdown_signals_and_reaps_all_roles(tmp_path, monkeypatch):
    children = [child(), child(), child()]
    manager = supervisor.Supervisor({name: [name] for name in ('api', 'publisher', 'worker')},
                                    tmp_path / 'state', popen=Mock(side_effect=children))
    manager.tick()
    def terminate(process, _sig):
        process.poll.return_value = 0
    monkeypatch.setattr(manager, 'signal_child', terminate)
    manager.shutdown()
    assert manager.stopping
    assert not (tmp_path / 'state').exists()
    for process in children:
        process.wait.assert_called_once()


def test_repeated_short_failures_stop_container(tmp_path):
    now = [0]
    processes = []
    def spawn(*_args, **_kwargs):
        process = child()
        processes.append(process)
        return process
    manager = supervisor.Supervisor({'api': ['api']}, tmp_path / 'state',
                                    popen=spawn, clock=lambda: now[0])
    manager.tick()
    for _ in range(5):
        processes[-1].poll.return_value = 1
        manager.tick()
        now[0] += 31
        manager.tick()
    processes[-1].poll.return_value = 1
    with pytest.raises(RuntimeError, match='repeatedly failed'):
        manager.tick()


def test_gunicorn_never_spawns_processing_roles():
    commands = supervisor.commands(Path('/studio'))
    assert set(commands) == {'api', 'publisher', 'worker'}
    assert commands['api'][commands['api'].index('--workers') + 1] == '1'
    assert commands['publisher'][-1].endswith('publisher.py')
    assert commands['worker'][-1].endswith('consumer.py')
