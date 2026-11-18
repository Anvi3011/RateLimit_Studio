"""One container, three real processes. No workers are spawned by Flask/Gunicorn."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def commands(root):
    return {
        'api': [sys.executable, '-m', 'gunicorn', '--chdir', str(root / 'backend'),
                '--bind', '0.0.0.0:' + os.getenv('PORT', '10000'),
                '--workers', '1', '--worker-class', 'gthread', '--threads', '4',
                '--timeout', '120', '--graceful-timeout', '20', 'wsgi:app'],
        'publisher': [sys.executable, str(root / 'worker/publisher.py')],
        'worker': [sys.executable, str(root / 'worker/consumer.py')],
    }


class Supervisor:
    def __init__(self, commands, status_file, popen=subprocess.Popen, clock=time.monotonic):
        self.commands = commands
        self.status_file = Path(status_file)
        self.popen = popen
        self.clock = clock
        self.children = {}
        self.started = {}
        self.failures = {name: 0 for name in commands}
        self.retry_at = {name: 0 for name in commands}
        self.stopping = False

    def stop(self, *_):
        self.stopping = True

    def tick(self):
        for name, command in self.commands.items():
            child = self.children.get(name)
            if child is not None and child.poll() is not None:
                child.wait()  # Reap before replacing.
                short_lived = self.clock() - self.started[name] < 60
                self.failures[name] = self.failures[name] + 1 if short_lived else 1
                self.retry_at[name] = self.clock() + min(30, 2 ** self.failures[name])
                self.children.pop(name)
                print(f'{name} exited; restart attempt {self.failures[name]}', flush=True)
                if self.failures[name] >= 6:
                    raise RuntimeError(f'{name} repeatedly failed')
            if not self.stopping and name not in self.children and self.clock() >= self.retry_at[name]:
                self.children[name] = self.popen(command, start_new_session=True)
                self.started[name] = self.clock()
        state = {'updated_at': time.time(), 'running': {
            name: name in self.children and self.children[name].poll() is None
            for name in self.commands}}
        temporary = self.status_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(state), encoding='utf-8')
        temporary.replace(self.status_file)

    def shutdown(self, grace=25):
        self.stopping = True
        for child in self.children.values():
            if child.poll() is None:
                self.signal_child(child, signal.SIGTERM)
        deadline = self.clock() + grace
        while any(c.poll() is None for c in self.children.values()) and self.clock() < deadline:
            time.sleep(.1)
        for child in self.children.values():
            if child.poll() is None:
                self.signal_child(child, signal.SIGKILL if os.name != 'nt' else signal.SIGTERM)
            child.wait()
        self.status_file.unlink(missing_ok=True)

    @staticmethod
    def signal_child(child, sig):
        try:
            if os.name == 'nt':
                child.terminate()
            else:
                os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass


def initialize_schema():
    # Backend metadata includes all tables/indexes; no drop, truncate or reset.
    from app.config import validate_public_config
    from app.db import init_db
    validate_public_config()
    deadline = time.monotonic() + 60
    while True:
        try:
            init_db()
            return
        except Exception as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError('PostgreSQL schema initialization unavailable') from None
            print(f'Waiting for PostgreSQL ({type(exc).__name__})', flush=True)
            time.sleep(3)


def main():
    root = Path(__file__).resolve().parents[2]
    manager = Supervisor(commands(root), os.environ['SUPERVISOR_STATUS_FILE'])
    signal.signal(signal.SIGTERM, manager.stop)
    signal.signal(signal.SIGINT, manager.stop)
    try:
        initialize_schema()
        while not manager.stopping:
            manager.tick()
            time.sleep(1)
    finally:
        manager.shutdown()


if __name__ == '__main__':
    main()
