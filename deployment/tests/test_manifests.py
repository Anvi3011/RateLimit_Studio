import json
from pathlib import Path
import yaml

ROOT = Path(__file__).parents[2]


def test_render_creates_only_one_free_web_service():
    config = yaml.safe_load((ROOT / 'render.yaml').read_text())
    assert set(config) == {'services'}
    assert len(config['services']) == 1
    service = config['services'][0]
    assert service['type'] == 'web'
    assert service['plan'] == 'free'
    assert service['autoDeployTrigger'] == 'off'
    assert service['healthCheckPath'] == '/api/live'
    env = {entry['key']: entry for entry in service['envVars']}
    for name in ('DATABASE_URL', 'REDIS_URL', 'RABBITMQ_URL', 'SESSION_SECRET'):
        assert env[name] == {'key': name, 'sync': False}


def test_compose_preserves_eight_defaults_and_volume_names():
    config = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
    defaults = {name for name, service in config['services'].items() if not service.get('profiles')}
    assert defaults == {'postgres', 'rabbitmq', 'redis', 'worker', 'publisher', 'backend', 'frontend', 'nginx'}
    assert set(config['volumes']) == {'postgres_data', 'redis_data', 'rabbitmq_data'}
    tls = yaml.safe_load((ROOT / 'deployment/aws/compose.tls.yml').read_text())
    assert set(tls['services']) == {'nginx'}


def test_vercel_proxies_api_before_spa_without_secrets():
    config = json.loads((ROOT / 'frontend/vercel.json').read_text())
    assert config['rewrites'][0]['source'] == '/api/:path*'
    assert config['rewrites'][0]['destination'].endswith('/api/:path*')
    assert 'env' not in config
