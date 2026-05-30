import requests


def _base_url(url):
    return str(url or '').strip().rstrip('/')


def n8n_headers(api_key=''):
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    key = str(api_key or '').strip()
    if key:
        headers['X-N8N-API-KEY'] = key
    return headers


def list_workflows(url, api_key):
    base = _base_url(url)
    if not base:
        return {'success': False, 'error': 'n8n URL is required'}
    response = requests.get(f'{base}/api/v1/workflows', headers=n8n_headers(api_key), timeout=15)
    response.raise_for_status()
    data = response.json()
    workflows = data.get('data', data if isinstance(data, list) else [])
    return {'success': True, 'workflows': workflows}


def trigger_webhook(webhook_url, payload=None):
    url = str(webhook_url or '').strip()
    if not url:
        return {'success': False, 'error': 'Webhook URL is required'}
    response = requests.post(url, json=payload or {}, timeout=30)
    content_type = response.headers.get('content-type', '')
    body = response.json() if 'application/json' in content_type else {'text': response.text}
    return {'success': response.ok, 'status_code': response.status_code, 'response': body}
