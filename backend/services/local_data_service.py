import re
import requests
from urllib.parse import quote_plus
from backend.database import db
from backend.models.local_data import LocalDataSource
from backend.models.blog_post import BlogPost
from backend.models.data_agent import DataAgent


DEFAULT_SOURCES = [
    {
        'key': 'wikipedia',
        'name': 'Wikipedia',
        'source_type': 'external',
        'description': 'Fetches short factual summaries from Wikipedia.',
        'endpoint': 'https://en.wikipedia.org/api/rest_v1/page/summary/{query}',
    },
    {
        'key': 'blog_posts',
        'name': 'Local Blog Posts',
        'source_type': 'local',
        'description': 'Uses admin-written blog posts as local knowledge.',
        'endpoint': '',
    },
    {
        'key': 'reddit',
        'name': 'Reddit',
        'source_type': 'external',
        'description': 'Reserved for future Reddit retrieval integration.',
        'endpoint': 'https://www.reddit.com/search.json?q={query}',
    },
    {
        'key': 'quora',
        'name': 'Quora',
        'source_type': 'external',
        'description': 'Reserved for future Quora integration.',
        'endpoint': '',
    },
    {
        'key': 'snapcourse_blog',
        'name': 'Snapcourse Blog',
        'source_type': 'external',
        'description': 'Reserved for future snapcourse.in blog crawling.',
        'endpoint': 'https://snapcourse.in',
    },
]

DEFAULT_DATA_AGENTS = [
    ('Nadia', 'Telegram signal scout'),
    ('TrendDish', 'Reddit trend watcher'),
    ('Drishti', 'ReadxHub live scanner'),
    ('Shristi', 'Snapcourse source crawler'),
    ('Aarika', 'Wikipedia fact retriever'),
    ('Rivka', 'Blog sync monitor'),
    ('Vani', 'Public announcement listener'),
    ('Tara', 'Forum insight collector'),
    ('Astra', 'Knowledge handoff agent'),
    ('Meera', 'Docs fetch operator'),
    ('Nyra', 'Feed pulse agent'),
    ('Ira', 'URL snapshot worker'),
    ('Sia', 'Realtime source validator'),
    ('Naira', 'Community pulse fetcher'),
    ('Vedika', 'Long-form source summarizer'),
]


FACTUAL_PATTERNS = [
    r'^\s*who\s+is\s+',
    r'^\s*what\s+is\s+',
    r'^\s*when\s+is\s+',
    r'^\s*where\s+is\s+',
    r'^\s*prime minister\b',
    r'^\s*president\b',
    r'^\s*capital of\b',
]

TRIVIAL_CHAT_PATTERNS = [
    r'^\s*hi\s*$',
    r'^\s*hello\s*$',
    r'^\s*hey\s*$',
    r'^\s*hey\s+mni',
    r'^\s*hello\s+mni',
    r'^\s*kya\s+haal',
    r'^\s*kaise\s+ho',
]


def ensure_local_data_sources():
    changed = False
    for item in DEFAULT_SOURCES:
        row = LocalDataSource.query.filter_by(key=item['key']).first()
        if row:
            continue
        db.session.add(LocalDataSource(**item))
        changed = True
    if changed:
        db.session.commit()

    enabled_count = LocalDataSource.query.filter_by(is_enabled=True).count()
    if enabled_count == 0:
        defaults_to_enable = {'wikipedia', 'blog_posts', 'reddit', 'snapcourse_blog'}
        touched = False
        for row in LocalDataSource.query.all():
            if row.key in defaults_to_enable and not row.is_enabled:
                row.is_enabled = True
                touched = True
        if touched:
            db.session.commit()


def ensure_default_data_agents():
    if DataAgent.query.count() > 0:
        return
    for name, role in DEFAULT_DATA_AGENTS:
        db.session.add(DataAgent(name=name, role=role, source_url='', mode='wait'))
    db.session.commit()


def get_sources():
    ensure_local_data_sources()
    return LocalDataSource.query.order_by(LocalDataSource.name.asc()).all()


def _extract_topic(message):
    cleaned = re.sub(r'\b(generate|create|make|draw|image|voice|video|tell me about|who is|what is)\b', ' ', message or '', flags=re.I)
    cleaned = re.sub(r'https?://\S+', ' ', cleaned, flags=re.I)
    cleaned = re.sub(r'\bwww\.\S+', ' ', cleaned, flags=re.I)
    cleaned = re.sub(r'\b(a|an)\s+(india|indian)\b', r'\2', cleaned, flags=re.I)
    cleaned = re.sub(r'\bof an\b', 'of', cleaned, flags=re.I)
    cleaned = re.sub(r'\bof a\b', 'of', cleaned, flags=re.I)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' ?.!')
    return cleaned[:120]


def _is_factual_query(message):
    text = str(message or '').strip().lower()
    return any(re.search(pattern, text) for pattern in FACTUAL_PATTERNS)


def _should_use_external_sources(message):
    text = str(message or '').strip().lower()
    if len(text) < 8:
        return False
    if any(re.search(pattern, text) for pattern in TRIVIAL_CHAT_PATTERNS):
        return False
    return True


def _fetch_wikipedia_search_title(query):
    response = requests.get(
        'https://en.wikipedia.org/w/rest.php/v1/search/title',
        params={'q': query, 'limit': 1},
        headers={'User-Agent': 'MNI/1.0'},
        timeout=10,
    )
    response.raise_for_status()
    pages = response.json().get('pages') or []
    if not pages:
        return None
    title = str(pages[0].get('title') or '').strip()
    return title or None


def _fetch_wikipedia_summary(query):
    if not query:
        return None
    query = re.sub(r'\b(in|on|from)\s+snapcourse\b', ' ', query, flags=re.I)
    query = re.sub(r'\breadxhub\b', ' ', query, flags=re.I)
    query = re.sub(r'\s+', ' ', query).strip()
    if not query:
        return None
    candidates = [query]
    title = _fetch_wikipedia_search_title(query)
    if title and title not in candidates:
        candidates.insert(0, title)

    for candidate in candidates:
        url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(candidate)}'
        response = requests.get(url, headers={'User-Agent': 'MNI/1.0'}, timeout=10)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        data = response.json()
        extract = (data.get('extract') or '').strip()
        if extract:
            final_title = data.get('title') or candidate
            return f"Wikipedia summary for {final_title}: {extract}"
    return None


def _fetch_blog_context(message):
    query = (message or '').strip().lower()
    if not query:
        return None
    posts = BlogPost.query.filter_by(is_enabled=True).order_by(BlogPost.updated_at.desc()).all()
    ranked = []
    for post in posts:
        haystack = ' '.join([post.title, post.summary, post.tags, post.content]).lower()
        score = sum(1 for token in query.split() if token and token in haystack)
        if score:
            ranked.append((score, post))
    ranked.sort(key=lambda item: (-item[0], item[1].updated_at or item[1].created_at), reverse=False)
    if not ranked:
        return None
    best = ranked[0][1]
    return f"Local blog context from '{best.title}' (slug: {best.slug}): {best.summary or best.content[:600]}"


def _fetch_reddit_context(message):
    if _is_factual_query(message):
        return None
    query = _extract_topic(message)
    if not query:
        return None
    response = requests.get(
        f'https://www.reddit.com/search.json?q={requests.utils.quote(query)}&limit=5&sort=relevance',
        headers={'User-Agent': 'MNI/1.0'},
        timeout=10,
    )
    response.raise_for_status()
    posts = response.json().get('data', {}).get('children', [])
    cleaned = []
    for item in posts[:5]:
        post = item.get('data', {})
        title = (post.get('title') or '').strip()
        subreddit = (post.get('subreddit') or '').strip()
        score = post.get('score', 0)
        if title:
            cleaned.append(f"r/{subreddit}: {title} (score {score})")
    if not cleaned:
        return None
    return f"Reddit summary for {query}: " + ' | '.join(cleaned)


def _fetch_quora_context(message):
    query = _extract_topic(message)
    if not query:
        return None
    try:
        response = requests.get(
            'https://html.duckduckgo.com/html/',
            params={'q': f'site:quora.com {query}'},
            headers={'User-Agent': 'MNI/1.0'},
            timeout=15,
        )
        response.raise_for_status()
    except Exception:
        return None

    html = response.text
    matches = re.findall(
        r'result__a[^>]*>(.*?)</a>.*?result__snippet[^>]*>(.*?)</a>|result__snippet[^>]*>(.*?)</div>',
        html,
        flags=re.I | re.S,
    )
    cleaned = []
    for match in matches[:5]:
        parts = [re.sub(r'<[^>]+>', ' ', item or '') for item in match if item]
        text = ' | '.join(re.sub(r'\s+', ' ', part).strip() for part in parts if part.strip())
        if text and 'quora' in text.lower():
            cleaned.append(text)
    if not cleaned:
        return None
    return f"Quora search summary for {query}: " + ' || '.join(cleaned)


def _fetch_snapcourse_blog_context(message):
    query = _extract_topic(message)
    normalized = re.sub(r'\b(in|on|from)\s+snapcourse\b', ' ', query or '', flags=re.I)
    normalized = re.sub(r'\breadxhub\b', ' ', normalized, flags=re.I)
    normalized = re.sub(r'https?://\S+', ' ', normalized, flags=re.I)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    candidates = [candidate for candidate in [query, normalized] if candidate]
    if query and 'grd' in query.lower() and 'gdr' not in query.lower():
        candidates.append(query.lower().replace('grd', 'gdr'))
    if query and 'gdr' in query.lower() and 'grd' not in query.lower():
        candidates.append(query.lower().replace('gdr', 'grd'))
    if not candidates:
        candidates = ['']

    for candidate in candidates:
        params = f"q={quote_plus(candidate)}&" if candidate else ''
        response = requests.get(
            f'https://blogs.snapcourse.in/fetch_new_blogs.php?{params}limit=8&offset=0',
            headers={'User-Agent': 'MNI/1.0'},
            timeout=15,
        )
        response.raise_for_status()
        rows = response.json() if response.text.strip() else []
        if not isinstance(rows, list) or not rows:
            continue
        chunks = []
        gdr_hit = None
        for row in rows[:6]:
            title = (row.get('title') or '').strip()
            description = (row.get('description') or '').strip()
            author = (row.get('author') or '').strip()
            slug = (row.get('slug') or '').strip()
            if title:
                if 'gdr' in title.lower():
                    gdr_hit = {
                        'title': title,
                        'description': description,
                        'slug': slug,
                    }
                chunks.append(f"{title} by {author or 'unknown'} | {description} | slug: {slug}")
        if gdr_hit and any(token in (message or '').lower() for token in ['gdr', 'readxhub', 'snapcourse']):
            return (
                f"ReadxHub/SnapCourse GDR direct match: {gdr_hit['title']} | "
                f"{gdr_hit['description']} | slug: {gdr_hit['slug']}"
            )
        if chunks:
            label = candidate or 'latest blogs'
            return f"Snapcourse/ReadxHub blog data for {label}: " + ' || '.join(chunks)
    return None


def collect_local_data_debug(message):
    ensure_local_data_sources()
    debug_rows = []
    for source in get_sources():
        row = {
            'key': source.key,
            'name': source.name,
            'source_type': source.source_type,
            'description': source.description,
            'endpoint': source.endpoint,
            'enabled': bool(source.is_enabled),
            'status': 'disabled',
            'preview': '',
            'fetched_from': source.endpoint or source.name,
        }
        if not source.is_enabled:
            debug_rows.append(row)
            continue
        try:
            preview = ''
            if source.key == 'wikipedia':
                preview = _fetch_wikipedia_summary(_extract_topic(message)) or ''
            elif source.key == 'blog_posts':
                preview = _fetch_blog_context(message) or ''
            elif source.key == 'reddit':
                preview = _fetch_reddit_context(message) or ''
            elif source.key == 'quora':
                preview = _fetch_quora_context(message) or ''
            elif source.key == 'snapcourse_blog':
                preview = _fetch_snapcourse_blog_context(message) or ''
            row['status'] = 'ok' if preview else 'empty'
            row['preview'] = preview[:2000]
        except Exception as error:
            row['status'] = 'error'
            row['preview'] = str(error)
        debug_rows.append(row)

    for agent in DataAgent.query.order_by(DataAgent.name.asc()).all():
        debug_rows.append({
            'key': f"agent:{agent.name}",
            'name': f"Data Agent: {agent.name}",
            'source_type': 'agent',
            'description': agent.role,
            'endpoint': agent.source_url,
            'enabled': agent.mode == 'active',
            'status': agent.last_status or ('active' if agent.mode == 'active' else 'idle'),
            'preview': (agent.last_snapshot or '')[:2000],
            'fetched_from': agent.source_url or agent.name,
        })
    return debug_rows


def build_local_context(message):
    ensure_local_data_sources()
    if not _should_use_external_sources(message):
        return ''
    contexts = []
    for source in get_sources():
        if not source.is_enabled:
            continue
        try:
            if source.key == 'wikipedia':
                summary = _fetch_wikipedia_summary(_extract_topic(message))
                if summary:
                    contexts.append(summary)
            elif source.key == 'blog_posts':
                blog_context = _fetch_blog_context(message)
                if blog_context:
                    contexts.append(blog_context)
            elif source.key == 'reddit':
                reddit_context = _fetch_reddit_context(message)
                if reddit_context:
                    contexts.append(reddit_context)
            elif source.key == 'quora':
                quora_context = _fetch_quora_context(message)
                if quora_context:
                    contexts.append(quora_context)
            elif source.key == 'snapcourse_blog':
                snapcourse_context = _fetch_snapcourse_blog_context(message)
                if snapcourse_context:
                    contexts.append(snapcourse_context)
        except Exception as error:
            print(f"[Local Data Error][{source.key}] {error}")
            
    # ── Inject Active Data Agents Knowledge ───────────────────────────
    try:
        active_agents = DataAgent.query.filter_by(mode='active').all()
        for agent in active_agents:
            if agent.last_snapshot:
                contexts.append(f"Knowledge from Data Agent '{agent.name}' (Source: {agent.source_url}):\n{agent.last_snapshot[:1500]}")
    except Exception as e:
        print(f"[Data Agent Context Error] {e}")
        
    return '\n\n'.join(contexts).strip()
