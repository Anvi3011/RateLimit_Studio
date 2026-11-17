"""Periodic durable fault audit repair, running inside each backend worker process."""
import atexit
import logging
import threading

from ..db import reconcile_expired_faults
from ..redis import client

logger = logging.getLogger(__name__)


def start_fault_reconciler(app, interval=2):
    stop = threading.Event()

    def run():
        with app.app_context():
            while not stop.is_set():
                try:
                    reconcile_expired_faults(client())
                except Exception as exc:
                    # A failed Redis/DB read is not evidence that a fault recovered.
                    logger.warning('Fault audit reconciliation deferred (%s)', type(exc).__name__)
                stop.wait(interval)

    thread = threading.Thread(target=run, name='fault-audit-reconciler', daemon=True)
    app.extensions['fault_reconciler'] = {'stop': stop, 'thread': thread}
    atexit.register(stop.set)
    thread.start()
    return stop
