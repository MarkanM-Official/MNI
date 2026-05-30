import requests


TOKEN_URL = 'https://oauth2.googleapis.com/token'
MEET_CREATE_URL = 'https://meet.googleapis.com/v2/spaces'


def _refresh_access_token(client_id, client_secret, refresh_token):
    response = requests.post(
        TOKEN_URL,
        data={
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()['access_token']


def create_google_meet_space(client_id, client_secret, refresh_token):
    access_token = _refresh_access_token(client_id, client_secret, refresh_token)
    response = requests.post(
        MEET_CREATE_URL,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        },
        json={},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return {
        'name': data.get('name', ''),
        'meeting_code': data.get('meetingCode', ''),
        'meeting_uri': data.get('meetingUri', ''),
    }
