#!/usr/bin/env python3
"""
제약시장 뉴스 자동 업데이트 스크립트
매일 오전 8시(KST) GitHub Actions를 통해 실행됨

동작:
1. RSS 피드에서 전날 ~ 오늘 기사 수집
2. 키워드 필터링
3. 해외 기사 한국어 번역
4. news.html ARTICLES 배열 맨 앞에 삽입 (중복 제거)
5. 14일 이상 지난 isNew 플래그 제거
"""

import feedparser
import hashlib
import json
import re
import sys
import os
from datetime import datetime, timedelta, timezone
from deep_translator import GoogleTranslator

# ── 시간대 설정 ──────────────────────────────────────────
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
YESTERDAY = TODAY - timedelta(days=1)
NEW_THRESHOLD = TODAY - timedelta(days=14)  # 14일 이내 = isNew

# ── news.html 경로 ────────────────────────────────────────
HTML_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'news.html')

# ── RSS 피드 목록 ─────────────────────────────────────────
# lang: 'ko' → 제목 그대로 사용 / 'en' → 한국어 번역 필요
RSS_SOURCES = [
    # ── 정책 기관 (매일 우선 수집) ──
    {
        'url': 'https://www.mfds.go.kr/rss/rss.do',
        'kw': 'mfds', 'lang': 'ko',
        'source_name': '식품의약품안전처'
    },
    {
        'url': 'https://www.mohw.go.kr/react/al/sal0301vw.do?type=rss',
        'kw': 'mohw', 'lang': 'ko',
        'source_name': '보건복지부'
    },
    {
        'url': 'https://www.hira.or.kr/rss/rss.do',
        'kw': 'hira', 'lang': 'ko',
        'source_name': '건강보험심사평가원'
    },
    # 정책 전문 매체
    {
        'url': 'https://www.medipana.com/rss/allArticle.xml',
        'kw': 'auto', 'lang': 'ko',
        'source_name': '메디파나뉴스'
    },
    {
        'url': 'https://www.dailypharm.com/rss/allArticle.xml',
        'kw': 'auto', 'lang': 'ko',
        'source_name': '데일리팜'
    },
    {
        'url': 'https://medigatenews.com/rss/allArticle.xml',
        'kw': 'auto', 'lang': 'ko',
        'source_name': '메디게이트뉴스'
    },
    {
        'url': 'https://www.pharmnews.com/rss/allArticle.xml',
        'kw': 'auto', 'lang': 'ko',
        'source_name': '팜뉴스'
    },
    {
        'url': 'https://www.hitnews.co.kr/rss/allArticle.xml',
        'kw': 'auto', 'lang': 'ko',
        'source_name': '히트뉴스'
    },
    {
        'url': 'https://www.kpanews.co.kr/rss.asp',
        'kw': 'auto', 'lang': 'ko',
        'source_name': '약사공론'
    },
    {
        'url': 'https://www.biotimes.co.kr/rss/allArticle.xml',
        'kw': 'auto', 'lang': 'ko',
        'source_name': '바이오타임즈'
    },
    # ── FDA ──
    {
        'url': 'https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/new-drug-approvals/rss.xml',
        'kw': 'fda', 'lang': 'en',
        'source_name': 'FDA'
    },
    {
        'url': 'https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml',
        'kw': 'fda', 'lang': 'en',
        'source_name': 'FDA'
    },
    # ── EMA ──
    {
        'url': 'https://www.ema.europa.eu/en/news/rss',
        'kw': 'ema', 'lang': 'en',
        'source_name': 'EMA'
    },
    # ── WHO ──
    {
        'url': 'https://www.who.int/rss-feeds/news-releases.xml',
        'kw': 'who', 'lang': 'en',
        'source_name': 'WHO'
    },
]

# ── kw='auto' 일 때 제목으로 키워드 자동 분류 ─────────────
KW_DETECT = {
    'mfds':   ['식약처', '식품의약품안전처', 'MFDS', '허가심사'],
    'mohw':   ['복지부', '보건복지부', '보건부', '건강보험료', '의료급여'],
    'hira':   ['심평원', '건강보험심사평가원', 'HIRA', '급여등재', '급여기준'],
    'launch': ['신약', '출시', '허가', '승인', '신제품', '발매', '시판', '적응증'],
    'market': ['시장', '전망', '산업', '투자', '바이오', '제약', 'M&A', 'CDMO', 'ADC', '매출', '실적', '영업이익'],
}

# ── 이 키워드 중 하나라도 있어야 수집 (관련 없는 기사 걸러냄) ──
RELEVANCE_KO = ['제약', '의약품', '바이오', '신약', '식약처', '복지부', '심평원',
                '허가', '승인', '약가', '건강보험', '임상', '치료제', '의료',
                '매출', '실적', '영업이익', '적응증', '급여등재', '급여 등재']
RELEVANCE_EN = ['drug', 'medicine', 'pharma', 'biologic', 'approval', 'FDA', 'EMA',
                'WHO', 'clinical', 'therapeutic', 'health', 'treatment']

# ── 식품 관련 제외 키워드 (의약품 키워드 없이 단독으로 쓰이면 제외) ──
EXCLUDE_KO = ['식품안전', '건강기능식품', '식품첨가물', '식품위생', '식품표시',
              '식품제조', '식품업체', '식품산업', '음식', '먹거리']
# 식품 기사라도 의약품 관련 키워드가 함께 있으면 수집
PHARMA_KO   = ['의약품', '의약', '신약', '제약', '치료제', '임상', '바이오']

# ── 국제 기사 앞에 붙는 레이블 ───────────────────────────
INTL_PREFIX = {'fda': '[FDA] ', 'ema': '[EMA] ', 'who': '[WHO] '}


def is_relevant(title: str, lang: str) -> bool:
    """관련 없는 기사 필터링 (식품 단독 기사 제외)"""
    if lang == 'ko':
        # 명시적 식품 키워드 → 즉시 제외
        if any(kw in title for kw in EXCLUDE_KO):
            return False
        # '식품'이 포함됐지만 의약품 키워드가 없으면 제외
        if '식품' in title and not any(kw in title for kw in PHARMA_KO):
            return False
        return any(kw in title for kw in RELEVANCE_KO)
    else:
        return any(kw.lower() in title.lower() for kw in RELEVANCE_EN)


def detect_kw(title: str) -> str:
    """제목으로 kw 자동 판별 (우선순위 순)"""
    for kw in ['mfds', 'mohw', 'hira', 'launch', 'market']:
        for term in KW_DETECT[kw]:
            if term in title:
                return kw
    return 'market'  # 기본값


def parse_date(entry: dict) -> str:
    """RSS entry에서 날짜 추출 → yyyy-mm-dd"""
    try:
        t = entry.get('published_parsed') or entry.get('updated_parsed')
        if t:
            dt = datetime(*t[:6], tzinfo=timezone.utc).astimezone(KST)
            return dt.date().isoformat()
    except Exception:
        pass
    return TODAY.isoformat()


def translate(text: str) -> str:
    """영문 → 한국어 번역 (실패 시 원문 반환)"""
    try:
        result = GoogleTranslator(source='auto', target='ko').translate(text)
        return result or text
    except Exception:
        return text


def make_id(url: str) -> str:
    """URL 기반 고유 ID 생성 (8자 해시)"""
    return 'r' + hashlib.md5(url.encode()).hexdigest()[:7]


def fetch_articles() -> list[dict]:
    """모든 RSS 피드에서 신규 기사 수집"""
    collected = []

    for src in RSS_SOURCES:
        try:
            feed = feedparser.parse(src['url'])
            for entry in feed.entries[:30]:
                pub = parse_date(entry)
                # 전날 이전 기사 무시
                if pub < YESTERDAY.isoformat():
                    continue

                title = (entry.get('title') or '').strip()
                url = (entry.get('link') or '').strip()
                if not title or not url:
                    continue

                # 관련성 필터
                if not is_relevant(title, src['lang']):
                    continue

                kw = src['kw']
                if src['lang'] == 'en':
                    title_en = title
                    title_ko = INTL_PREFIX.get(kw, '') + translate(title)
                else:
                    title_en = None
                    title_ko = title
                    if kw == 'auto':
                        kw = detect_kw(title)

                collected.append({
                    'id':      make_id(url),
                    'date':    pub,
                    'kw':      kw,
                    'isNew':   True,
                    'titleKo': title_ko,
                    'titleEn': title_en,
                    'url':     url,
                    'source':  src['source_name'],
                })

        except Exception as e:
            print(f'[WARN] {src["url"]}: {e}', file=sys.stderr)

    return collected


def read_html() -> str:
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        return f.read()


def write_html(content: str):
    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(content)


def get_existing(content: str) -> tuple[set, set]:
    """현재 HTML에서 기존 URL과 ID 추출"""
    urls = set(re.findall(r"url:'([^']+)'", content))
    ids  = set(re.findall(r"id:'([^']+)'", content))
    return urls, ids


def article_to_js(a: dict) -> str:
    """기사 딕셔너리 → JS 객체 문자열"""
    parts = [f"  {{ id:'{a['id']}', date:'{a['date']}', kw:'{a['kw']}'"]
    if a.get('isNew'):
        parts[0] += ', isNew:true'
    title_ko = a['titleKo'].replace("'", "\\'").replace('\n', ' ')
    parts.append(f",\n    titleKo:'{title_ko}'")
    if a.get('titleEn'):
        title_en = a['titleEn'].replace("'", "\\'").replace('\n', ' ')
        parts.append(f",\n    titleEn:'{title_en}'")
    url = a['url'].replace("'", "%27")
    source = a['source'].replace("'", "\\'")
    parts.append(f",\n    url:'{url}', source:'{source}' }}")
    return ''.join(parts)


def expire_is_new(content: str) -> str:
    """14일 이상 된 기사의 isNew:true 제거"""
    threshold = NEW_THRESHOLD.isoformat()

    def replacer(m):
        # id, date, kw, isNew:true 순서로 캡처
        date_val = m.group(1)
        if date_val < threshold:
            # isNew 제거
            return m.group(0).replace(', isNew:true', '')
        return m.group(0)

    # date:'2026-xx-xx', ... isNew:true 패턴 찾아서 처리
    return re.sub(
        r"date:'(\d{4}-\d{2}-\d{2})',[^\n]*?, isNew:true",
        replacer,
        content
    )


def main():
    print(f'[INFO] 실행 날짜: {TODAY} (KST)')
    content = read_html()

    # 1. 기존 isNew 만료 처리
    content_new = expire_is_new(content)
    if content_new != content:
        print('[INFO] 만료된 isNew 플래그 제거 완료')
        content = content_new

    # 2. 새 기사 수집
    articles = fetch_articles()
    print(f'[INFO] 수집된 후보: {len(articles)}건')

    # 3. 중복 제거
    existing_urls, existing_ids = get_existing(content)
    unique = [
        a for a in articles
        if a['url'] not in existing_urls and a['id'] not in existing_ids
    ]
    print(f'[INFO] 중복 제거 후: {len(unique)}건')

    if not unique:
        print('[INFO] 추가할 새 기사 없음.')
        # isNew 만료만 있어도 저장
        if content_new != read_html():
            write_html(content)
        sys.exit(0)

    # 4. ARTICLES 배열 맨 앞에 삽입
    comment = f'  /* ── 자동 추가 {TODAY.isoformat()} ({len(unique)}건) ── */\n'
    block = comment + ',\n'.join(article_to_js(a) for a in unique) + ',\n'
    content = content.replace('const ARTICLES = [\n', f'const ARTICLES = [\n{block}', 1)

    write_html(content)
    print(f'[OK] {len(unique)}건 추가 완료:')
    for a in unique:
        print(f"     [{a['kw']}] {a['titleKo'][:50]}")


if __name__ == '__main__':
    main()
