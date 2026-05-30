"""
MNI Automation Manager - Data Agent Web Crawler
Crawls target URLs and saves the snapshot for MNI's RAG context.
"""
import requests
import logging
import re
import json
from datetime import datetime
import threading
import time
from urllib.parse import quote_plus
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None
from backend.database import db
from backend.models.data_agent import DataAgent


def _flatten_json(value, chunks):
    if isinstance(value, dict):
        for key, item in value.items():
            chunks.append(str(key))
            _flatten_json(item, chunks)
    elif isinstance(value, list):
        for item in value:
            _flatten_json(item, chunks)
    elif value is not None:
        chunks.append(str(value))


def _extract_json_payload(text):
    try:
        payload = json.loads(text)
    except Exception:
        lines = []
        for line in (text or '').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except Exception:
                continue
        if not lines:
            return None
        payload = lines

    chunks = []
    _flatten_json(payload, chunks)
    cleaned = re.sub(r'\s+', ' ', ' '.join(chunks)).strip()
    return cleaned[:15000] if cleaned else None


def _fetch_readxhub_deep_snapshot(query=''):
    variants = [query] if query else []
    if query and 'grd' in query.lower() and 'gdr' not in query.lower():
        variants.append(query.lower().replace('grd', 'gdr'))
    variants.append('')

    for variant in variants:
        params = f"q={quote_plus(variant)}&" if variant else ''
        try:
            response = requests.get(
                f'https://blogs.snapcourse.in/fetch_new_blogs.php?{params}limit=10&offset=0',
                headers={'User-Agent': 'Mozilla/5.0 (compatible; MNI-Automation-Manager/1.0)'},
                timeout=20,
            )
            response.raise_for_status()
            rows = response.json() if response.text.strip() else []
        except Exception:
            continue
        if not isinstance(rows, list) or not rows:
            continue
        chunks = []
        for row in rows[:8]:
            title = (row.get('title') or '').strip()
            description = (row.get('description') or '').strip()
            author = (row.get('author') or '').strip()
            slug = (row.get('slug') or '').strip()
            if title:
                chunks.append(f"{title} by {author or 'unknown'} | {description} | slug: {slug}")
        if chunks:
            return "ReadxHub deep snapshot: " + " || ".join(chunks)
    return None

def crawl_url(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; MNI-Automation-Manager/1.0)'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        content_type = (response.headers.get('content-type') or '').lower()

        if 'json' in content_type or url.lower().endswith(('.json', '.jsonl')):
            parsed = _extract_json_payload(response.text)
            return parsed or response.text[:15000]

        if BeautifulSoup is not None:
            soup = BeautifulSoup(response.content, 'html.parser')
            script_blobs = []
            for script in soup.find_all('script'):
                script_type = (script.get('type') or '').lower()
                if script_type in {'application/ld+json', 'application/json'} and script.string:
                    script_blobs.append(script.string)
            for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                element.extract()
            text = soup.get_text(separator=' ', strip=True)
            if 'enable javascript to run this app' in text.lower():
                extracted = []
                for blob in script_blobs:
                    parsed = _extract_json_payload(blob)
                    if parsed:
                        extracted.append(parsed)
                if extracted:
                    text = f"{text} {' '.join(extracted)}"
        else:
            text = response.text
            text = re.sub(r'<script.*?</script>', ' ', text, flags=re.I | re.S)
            text = re.sub(r'<style.*?</style>', ' ', text, flags=re.I | re.S)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()

        if 'readxhub.in' in url.lower():
            text_lower = (text or '').lower()
            if len(text.strip()) < 120 or 'blogs | snapcourse' in text_lower or 'enable javascript to run this app' in text_lower:
                deep_snapshot = _fetch_readxhub_deep_snapshot()
                if deep_snapshot:
                    return deep_snapshot[:15000]
        return text[:15000] # Limit snapshot size to keep LLM context window healthy
    except requests.exceptions.RequestException as e:
        logging.error(f"[Crawler Error] Failed to fetch {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"[Crawler Error] Unexpected error while crawling {url}: {e}", exc_info=True)
        return None


def run_agent_crawler(agent_id):
    agent = DataAgent.query.get(agent_id)
    if not agent or not agent.source_url:
        return False
        
    content = crawl_url(agent.source_url)
    agent.last_snapshot = content or ''
    agent.last_status = 'success' if content else 'failed'
    agent.last_fetched_at = datetime.utcnow()
    db.session.commit()
    return bool(content)


def _daily_crawl_loop(app):
    """Background task that runs every 24 hours to update active agents."""
    while True:
        time.sleep(86400)  # Wait 24 hours (24 * 60 * 60 seconds)
        try:
            with app.app_context():
                # Find all active agents that need their data refreshed
                active_agents = DataAgent.query.filter_by(mode='active').all()
                logging.info(f"Daily crawler running for {len(active_agents)} active agents.")
                for agent in active_agents:
                    if agent.source_url:
                        logging.info(f"Crawling agent {agent.id} ({agent.source_url})")
                        run_agent_crawler(agent.id)
        except Exception as e:
            logging.error(f"[Daily Crawler Error] {e}", exc_info=True)


def start_daily_crawler(app):
    """Starts the background daily crawler daemon thread."""
    thread = threading.Thread(target=_daily_crawl_loop, args=(app,), daemon=True)
    thread.start()
    return thread
