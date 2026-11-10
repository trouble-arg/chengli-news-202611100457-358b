import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import email.utils
import hashlib
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_FILE = DATA_DIR / "news_cache.json"
CONFIG_FILE = BASE_DIR / "config.json"
HOST = "127.0.0.1"
PORT = 8765
USER_AGENT = "Mozilla/5.0 ChengLiDianXun/1.0 Personal News Aggregator"


def load_config():
    with CONFIG_FILE.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def clean_text(value):
    if not value:
        return ""
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", str(value))
    if match:
        year, month, day = map(int, match.groups())
        return datetime(year, month, day, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def request_text(url, timeout=6):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9"
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset()
        if not charset:
            header = raw[:3000].decode("ascii", errors="ignore")
            meta = re.search(r'charset=["\']?([\w-]+)', header, flags=re.I)
            charset = meta.group(1) if meta else "utf-8"
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            return raw.decode("gb18030", errors="replace")


def request_json(url, timeout=12):
    return json.loads(request_text(url, timeout))


def source_from_link(link):
    host = urllib.parse.urlparse(link).netloc.lower().replace("www.", "")
    return host or "网络来源"


def fetch_rss(url, topic, source_override=None):
    root = ET.fromstring(request_text(url))
    records = []
    for node in root.findall(".//item"):
        title = clean_text(node.findtext("title"))
        link = clean_text(node.findtext("link"))
        if not title or not link:
            continue
        source_node = node.find("source")
        source = source_override or clean_text(source_node.text if source_node is not None else "") or source_from_link(link)
        records.append({
            "title": title,
            "link": link,
            "summary": clean_text(node.findtext("description")),
            "source": source,
            "published_at": parse_date(node.findtext("pubDate")).isoformat(),
            "category": topic["category"],
            "provider": "rss"
        })
    return records


def fetch_page(url, topic, source_override=None, kind=None):
    page = request_text(url, timeout=10)
    source = source_override or source_from_link(url)
    now = datetime.now(timezone.utc)
    records = []

    def add(title, link, summary="", published=None, time_label=None, image=""):
        title = clean_text(title)
        link = urllib.parse.urljoin(url, html.unescape(link))
        if not title or not link:
            return
        records.append({
            "title": title,
            "link": link,
            "summary": clean_text(summary) or f"{source}发布的信息：{title}。点击原文查看完整内容。",
            "source": source,
            "published_at": (published or now).isoformat(),
            "category": topic["category"],
            "provider": "page",
            "time_label": time_label,
            "relevance_text": title,
            "image": image
        })

    if kind == "energy":
        pattern = re.compile(r'<a\s+href="([^"]+)"\s+title="([^"]+)"[^>]*>(.*?)</a>', re.S)
        for match in pattern.finditer(page):
            link, title, block = match.groups()
            tail = page[match.end():match.end() + 700]
            date_match = re.search(r'class="c_tmt">\s*(20\d{2}-\d{1,2}-\d{1,2})\s*<', tail)
            published = parse_date(date_match.group(1)) if date_match else now
            heading = re.search(r'<div class="c_h24">(.*?)</div>', tail, re.S)
            add(title, link, heading.group(1) if heading else title, published)

    elif kind == "nuedc":
        pattern = re.compile(r"href='(show\.aspx\?[^']+)'[^>]*>\s*(.*?)\s*</a>(.*?)(?=href='show\.aspx|</table>)", re.S)
        for link, title, tail in pattern.findall(page)[:8]:
            date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", tail)
            if not date_match:
                continue
            add(title, link, title, parse_date(date_match.group(1)))

    elif kind == "mcm":
        pattern = re.compile(r'<div class="item"[^>]*>.*?<a href="([^"]+)">(.*?)</a>', re.S)
        for match in pattern.finditer(page):
            link, title = match.groups()
            if not re.search(r"20\d{2}", clean_text(title)):
                continue
            add(title, link, title, now, "官网更新")
            if len(records) >= 8:
                break

    elif kind == "jobs":
        pattern = re.compile(r'<div class="enterprise-list-title[^"]*"[^>]*>(.*?)</div>', re.S)
        for title_match in pattern.finditer(page):
            anchor_start = page.rfind("<a ", 0, title_match.start())
            if anchor_start < 0:
                continue
            href = re.search(r'href="([^"]+)"', page[anchor_start:title_match.start()])
            if not href:
                continue
            title = clean_text(title_match.group(1))
            image_match = re.search(r'<img src="([^"]+)"', page[anchor_start:title_match.start()])
            add(title, href.group(1), f"{title}。点击查看岗位详情、招聘对象与报名要求。", now, "实时更新", image_match.group(1) if image_match else "")

    elif kind == "competition_home":
        pattern = re.compile(r'<a\b(?P<attrs>[^>]*)>(?P<body>.*?)(?:</a>)(?P<tail>.*?)(?=<a\b|</li|</ul)', re.S | re.I)
        for match in pattern.finditer(page):
            href_match = re.search(r'href=["\']([^"\']+)', match.group("attrs"))
            if not href_match:
                continue
            href = href_match.group(1)
            if not any(token in href for token in ("info/", "article/", "information/")):
                continue
            combined = match.group("body") + match.group("tail")[:900]
            date_text = clean_text(combined)
            full_date = re.search(r"(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})", date_text)
            month_day_year = re.search(r"(\d{1,2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(20\d{2})", date_text)
            day_year_month = re.search(r"(\d{1,2})\s+(20\d{2})\s*[-./]\s*(\d{1,2})", date_text)
            if full_date:
                year, month, day = map(int, full_date.groups())
            elif month_day_year:
                month, day, year = map(int, month_day_year.groups())
            elif day_year_month:
                day, year, month = map(int, day_year_month.groups())
            else:
                continue
            try:
                datetime(year, month, day)
            except ValueError:
                continue
            title_node = re.search(r"<h[23][^>]*>(.*?)</h[23]>", combined, re.S)
            title = clean_text(title_node.group(1) if title_node else match.group("body"))
            if len(title) < 8 or len(title) > 180:
                continue
            if len(records) < 12:
                add(title, href, title, datetime(year, month, day, tzinfo=timezone.utc))

    elif kind == "sichuan":
        pattern = re.compile(r'<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>', re.S | re.I)
        for match in pattern.finditer(page):
            href_match = re.search(r'href=["\']([^"\']+)', match.group("attrs"))
            if not href_match:
                continue
            href = href_match.group(1)
            title_match = re.search(r'title=["\']([^"\']+)', match.group("attrs"), re.I)
            title = clean_text(title_match.group(1) if title_match else match.group("body"))
            if len(title) < 10 or len(title) > 180:
                continue
            local_pattern = r"四川|成都|蓉城|川渝|成都理工|东部新区|天府|锦江|武侯|青羊|金牛|成华|高新|绵阳|德阳|宜宾|南充|泸州|乐山|眉山|遂宁|内江|自贡|广安|达州|巴中|广元|资阳|阿坝|甘孜|凉山"
            if not re.search(local_pattern, title):
                continue
            date_match = re.search(r"(20\d{2})(\d{2})(\d{2})", href) or re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", href)
            month_match = re.search(r"(20\d{2})(\d{2})(?!\d)", href) or re.search(r"(20\d{2})[-/](\d{1,2})(?![-/])", href)
            if date_match:
                try:
                    year, month, day = map(int, date_match.groups())
                    datetime(year, month, day)
                except ValueError:
                    continue
                published = datetime(year, month, day, tzinfo=timezone.utc)
                label = "今日热点" if published.date() == now.date() else None
            elif month_match:
                try:
                    year, month = map(int, month_match.groups())
                    datetime(year, month, 1)
                except ValueError:
                    continue
                if year == now.year and month == now.month:
                    published = now
                    label = "本月热点"
                else:
                    continue
            else:
                continue
            if (now - published).days > 60:
                continue
            if len(records) < 20:
                add(title, href, title, published, label)

    return records


def fetch_360_search(query, topic):
    encoded = urllib.parse.quote(query)
    page = ""
    positions = []
    for host in ("m.so.com", "www.so.com"):
        try:
            candidate = request_text(f"https://{host}/s?q={encoded}&pn=1", timeout=6)
            candidate_positions = [match.start() for match in re.finditer(r'<li[^>]+class="res-list', candidate)]
            if candidate_positions:
                page = candidate
                positions = candidate_positions
                break
        except Exception:
            continue
    if not positions:
        raise RuntimeError("360搜索暂时被限流")
    records = []
    for index, start in enumerate(positions):
        end = positions[index + 1] if index + 1 < len(positions) else min(len(page), start + 12000)
        block = page[start:end]
        anchor = re.search(r'<h3[^>]*class="res-title[^"]*"[^>]*>\s*<a([^>]*)>(.*?)</a>', block, re.S)
        if not anchor:
            continue
        attributes, raw_title = anchor.groups()
        direct_match = re.search(r'data-mdurl="([^"]+)"', attributes)
        href_match = re.search(r'href="([^"]+)"', attributes)
        link = html.unescape(direct_match.group(1) if direct_match else href_match.group(1))
        description = re.search(r'<p class="res-desc">(.*?)</p>', block, re.S)
        summary = clean_text(description.group(1)) if description else ""
        if not summary:
            summary = clean_text(re.sub(r"<[^>]+>", " ", block[:3000]))[:180]
        date_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", summary)
        if not date_match:
            continue
        published = datetime(*map(int, date_match.groups()), tzinfo=timezone.utc)
        records.append({
            "title": clean_text(raw_title),
            "link": link,
            "summary": summary,
            "source": source_from_link(link),
            "published_at": published.isoformat(),
            "category": topic["category"],
            "provider": "search"
        })
    return records


def enrich_summary(record):
    if len(record.get("summary", "")) >= 45:
        return record
    try:
        page = request_text(record["link"], timeout=6)
        patterns = [
            r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:description|og:description)["\']'
        ]
        for pattern in patterns:
            match = re.search(pattern, page, flags=re.I)
            if match:
                record["summary"] = clean_text(match.group(1))[:180]
                break
    except Exception:
        pass
    if not record.get("summary"):
        record["summary"] = f"这条信息与「{record['category']}」相关。点击原文查看完整发布内容和背景。"
    return record


def score_record(record, topic, age_hours):
    text = f"{record['title']} {record.get('summary', '')}".lower()
    keyword_score = sum(weight for keyword, weight in topic["keywords"].items() if keyword.lower() in text)
    freshness = max(0, 18 - min(age_hours, 72) / 4)
    official_score = 5 if any(token in record["source"].lower() for token in ["教育", "大学", "gov", "官方", "大学网"]) else 2
    search_boost = 18 if topic["category"] == "升学就业" and record.get("provider") == "search" else 0
    competition_boost = 8 if topic["category"] == "竞赛科研" and record.get("provider") == "page" else 0
    return round(keyword_score + freshness + official_score + search_boost + competition_boost + (3 if record.get("provider") == "search" else 2), 1)


def fetch_all():
    config = load_config()
    records = []
    errors = []
    jobs = []
    for topic in config["topics"]:
        for feed in topic.get("feeds", []):
            jobs.append((topic, feed.get("source") or topic["category"], fetch_rss, feed["url"], topic, feed.get("source")))
        for page in topic.get("pages", []):
            jobs.append((topic, page.get("source") or topic["category"], fetch_page, page["url"], topic, page.get("source"), page.get("kind")))
    with ThreadPoolExecutor(max_workers=9) as executor:
        futures = {executor.submit(job[2], *job[3:]): job for job in jobs}
        for future in as_completed(futures):
            topic, provider = futures[future][0], futures[future][1]
            try:
                records.extend(future.result())
            except Exception as error:
                errors.append(f"{topic['category']} / {provider}: {error}")

    search_jobs = [(topic, query) for topic in config["topics"] for query in topic.get("searches", [])]
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_360_search, query, topic): topic for topic, query in search_jobs}
        for future in as_completed(futures):
            topic = futures[future]
            try:
                records.extend(future.result())
            except Exception as error:
                errors.append(f"{topic['category']} / 搜索: {error}")

    deduplicated = {}
    now = datetime.now(timezone.utc)
    for record in records:
        normalized = re.sub(r"\W+", "", record["title"].lower())
        record_id = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
        if record_id in deduplicated:
            continue
        published = datetime.fromisoformat(record["published_at"])
        age_hours = max(0, (now - published).total_seconds() / 3600)
        topic = next(item for item in config["topics"] if item["category"] == record["category"])
        if record.get("provider") == "search" and age_hours / 24 > topic.get("search_max_age_days", 365):
            continue
        searchable = f"{record.get('relevance_text', record['title'] + ' ' + record.get('summary', ''))}".lower()
        if any(keyword.lower() in searchable for keyword in topic.get("exclude_keywords", [])):
            continue
        if topic["category"] not in ("科技前沿", "四川热点") and not any(keyword.lower() in searchable for keyword in topic["keywords"]):
            continue
        record["id"] = record_id
        record["score"] = score_record(record, topic, age_hours)
        record["important"] = record["score"] >= 17
        official = any(token in f"{record['source']} {record['link']}".lower() for token in ["edu.cn", "gov.cn", "大学", "教育", "官方"])
        record["source_type"] = "官方" if official else "媒体"
        deduplicated[record_id] = record

    ranked = sorted(deduplicated.values(), key=lambda item: item["score"], reverse=True)
    limits = {"科技前沿": 10, "升学就业": 8, "专业学习": 5, "校园机会": 4, "竞赛科研": 12, "政策行业": 4, "四川热点": 12}
    selected = []
    for category, limit in limits.items():
        pool = [record for record in ranked if record["category"] == category]
        grouped = {}
        for record in pool:
            grouped.setdefault(record["source"], []).append(record)
        groups = sorted(grouped.values(), key=lambda items: items[0]["score"], reverse=True)
        pointers = [0] * len(groups)
        category_selected = []
        while len(category_selected) < limit and any(pointers[index] < len(group) for index, group in enumerate(groups)):
            for index, group in enumerate(groups):
                if len(category_selected) >= limit:
                    break
                if pointers[index] < len(group):
                    category_selected.append(group[pointers[index]])
                    pointers[index] += 1
        selected.extend(category_selected)
    selected = sorted(selected, key=lambda item: item["score"], reverse=True)[: config["max_items"]]
    for record in selected[:22]:
        enrich_summary(record)
    for record in selected:
        summary = record.get("summary", "")
        record["summary"] = summary[:220].rstrip() + ("…" if len(summary) > 220 else "")
    return {
        "site_name": config["site_name"],
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "items": selected,
        "errors": errors[-8:],
        "status": "ok" if selected else "empty"
    }


def refresh_cache():
    DATA_DIR.mkdir(exist_ok=True)
    payload = fetch_all()
    with CACHE_FILE.open("w", encoding="utf-8") as cache_file:
        json.dump(payload, cache_file, ensure_ascii=False, indent=2)
    return payload


def load_cache():
    if CACHE_FILE.exists():
        with CACHE_FILE.open("r", encoding="utf-8") as cache_file:
            return json.load(cache_file)
    return {"site_name": "成理电讯", "last_updated": None, "items": [], "errors": [], "status": "empty"}


class NewsHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/news":
            encoded = json.dumps(load_cache(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        if path == "/api/refresh":
            try:
                payload = refresh_cache()
                status, message = 200, f"已更新 {len(payload['items'])} 条"
            except Exception as error:
                status, message = 502, str(error)
            encoded = json.dumps({"message": message}, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        super().do_GET()

    def log_message(self, message_format, *args):
        print(f"[{datetime.now():%H:%M:%S}] {message_format % args}")


def background_scheduler():
    config = load_config()
    last_daily = ""
    while True:
        try:
            now = datetime.now()
            cache = load_cache()
            updated = datetime.fromisoformat(cache["last_updated"]) if cache.get("last_updated") else None
            stale = not updated or datetime.now(timezone.utc) - updated > timedelta(minutes=config["refresh_minutes"])
            daily_key = now.strftime("%Y-%m-%d")
            if stale or (now.hour == config["daily_refresh_hour"] and last_daily != daily_key):
                refresh_cache()
                last_daily = daily_key
                print(f"[{now:%H:%M:%S}] 资讯缓存已更新")
        except Exception as error:
            print(f"[scheduler] {error}")
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-only", action="store_true")
    args = parser.parse_args()
    if args.refresh_only:
        payload = refresh_cache()
        print(f"更新完成：{len(payload['items'])} 条")
        return
    threading.Thread(target=background_scheduler, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), NewsHandler)
    print(f"成理电讯运行于 http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
