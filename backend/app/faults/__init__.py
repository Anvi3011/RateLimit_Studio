"""Model failure outcomes on a probe that does not enter the live pipeline."""

PUBLIC_DURATION_LIMIT = 60

PROBES = {
    'redis_unavailable': ('Redis', 'decision', 'unavailable', 503,
                          'The policy check cannot complete when the rate-limit store is unavailable.'),
    'rabbitmq_unavailable': ('RabbitMQ', 'publish', 'blocked', None,
                             'The event stays in the outbox awaiting a publisher retry.'),
    'worker_unavailable': ('Worker', 'persist', 'pending', None,
                           'The event remains queued until a consumer can persist it.'),
    'api_degraded': ('Flask API', 'response', 'degraded', 503,
                     'The API returns a service-unavailable response in this scenario.'),
    'network_timeout': ('Reverse proxy', 'response', 'timeout', 504,
                        'The proxy returns a gateway timeout in this scenario.'),
}


def probe_result(name, active):
    component, stage, outcome, http_status, description = PROBES[name]
    return {
        'name': name, 'mode': 'simulation', 'component': component, 'stage': stage,
        'active': active, 'outcome': outcome if active else 'recovered',
        'simulated_http_status': http_status if active else None,
        'description': description if active else 'This fault is inactive. The probe can proceed.',
    }
