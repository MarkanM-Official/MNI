"""
MNI Automation Manager - Video Generation Service (via Replicate)
"""
import os
import time
import requests
from backend.services.router import pick_api, mark_failed, get_env_api_key
from backend.services.secret_store import decrypt_secret_text

# A popular open-source Text-to-Video model on Replicate
REPLICATE_VIDEO_MODEL = "anotherjesse/zeroscope-v2-xl:9f747673945c62801b13b84701c783929c0ee784e4748ec062204894dda1a351"

def generate_video(prompt):
    """
    Generates a video using an open-source model hosted on Replicate.
    This is an asynchronous process that involves starting and then polling for the result.
    """
    api_entry = pick_api('video')
    api_key = decrypt_secret_text(api_entry.api_key) if api_entry else get_env_api_key('video')

    if not api_key:
        return None, "Video generation API (Replicate) not configured."

    try:
        # Step 1: Start the prediction job on Replicate
        start_response = requests.post(
            "https://api.replicate.com/v1/predictions",
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
            json={"version": REPLICATE_VIDEO_MODEL, "input": {"prompt": prompt}},
            timeout=15
        )
        start_response.raise_for_status()
        prediction_id = start_response.json().get("id")
        
        if not prediction_id:
            return None, "Failed to start video generation job on Replicate."

        # Step 2: Poll for the result
        poll_url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
        for _ in range(60): # Poll for up to 2 minutes
            poll_response = requests.get(poll_url, headers={"Authorization": f"Token {api_key}"}, timeout=10)
            poll_response.raise_for_status()
            result = poll_response.json()
            if result['status'] == 'succeeded':
                return result['output'][0] if result.get('output') else None, None
            if result['status'] in ['failed', 'canceled']:
                return None, f"Video generation {result['status']}: {result.get('error', 'Unknown error')}"
            time.sleep(2)

        return None, "Video generation timed out."
    except Exception as e:
        if api_entry: mark_failed(api_entry.id)
        return None, f"Video generation failed: {e}"
