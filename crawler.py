import os
import uuid
import zlib
from datetime import datetime, timezone
from urllib.parse import urlparse

import pymysql
import crawler


ASSET_EXTENSIONS = [
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".svg",
    ".css",
    ".js",
    ".pdf",
    ".doc", ".docx",
    ".xls", ".xlsx",
    ".zip", ".rar",
    ".mp4", ".webm",
]


def is_asset_url(url: str) -> bool:
    lower = url.lower()
    return any(lower.split("?", 1)[0].endswith(ext) for ext in ASSET_EXTENSIONS)


def is_http_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


class SiteCrawlerSpider(crawler.Spider):
    """
    Simple site crawler:
    - Crawls one domain (from env)
    - Stores pages, links, and assets in MySQL via pipeline
    """

    name = "site_crawler"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "LOG_LEVEL": "INFO",
        "ITEM_PIPELINES": {
            "__main__.MySQLStorePipeline": 300,
        },
        # Be reasonably polite; tweak as needed
        "DOWNLOAD_DELAY": 0.1,
        "CONCURRENT_REQUESTS": 16,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        domain = os.getenv("CRAWL_SITE_DOMAIN")
        start_url = os.getenv("CRAWL_START_URL")

        if not domain or not start_url:
            raise ValueError(
                "CRAWL_SITE_DOMAIN and CRAWL_START_URL environment variables must be set"
            )

        self.site_domain = domain
        self.allowed_domains = [domain]
        self.start_urls = [start_url]
        self.seen_asset_urls = set()

    def parse(self, response):
        # Only treat HTML-ish responses as pages
        content_type = response.headers.get(b"Content-Type", b"").decode("utf-8").lower()
        is_html = "text/html" in content_type or content_type == ""

        page_links = []
        assets = []

        # Extract links from <a>
        for href in response.css("a::attr(href)").getall():
            href = href.strip()
            if not href or href.startswith("#"):
                continue
            if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
                continue

            abs_url = response.urljoin(href)
            page_links.append(abs_url)

            # Decide if we should follow it as a page
            if is_http_url(abs_url) and not is_asset_url(abs_url):
                parsed = urlparse(abs_url)
                if parsed.netloc.endswith(self.site_domain):
                    # Internal page – follow
                    yield response.follow(abs_url, callback=self.parse)

        # Extract assets: images, scripts, stylesheets, etc.
        assets.extend(self._extract_assets(response))

        # Schedule HEAD requests for assets (only once per run)
        for asset_url in assets:
            if asset_url not in self.seen_asset_urls:
                self.seen_asset_urls.add(asset_url)
                yield crawler.Request(
                    asset_url,
                    method="HEAD",
                    callback=self.parse_asset,
                    errback=self.asset_error,
                    dont_filter=True,
                )

        if is_html:
            # Extract basic metadata
            title = response.css("title::text").get()
            if title:
                title = title.strip()

            meta_desc = response.css("meta[name='description']::attr(content)").get()
            if meta_desc:
                meta_desc = meta_desc.strip()

            canonical = response.css("link[rel='canonical']::attr(href)").get()
            if canonical:
                canonical = response.urljoin(canonical.strip())

            yield {
                "item_type": "page",
                "url": response.url,
                "status": response.status,
                "title": title,
                "meta_description": meta_desc,
                "canonical_url": canonical,
                "html": response.text,
                "page_links": page_links,
                "assets": assets,
            }

    def _extract_assets(self, response):
        assets = set()

        # Images
        for src in response.css("img::attr(src)").getall():
            src = src.strip()
            if not src:
                continue
            url = response.urljoin(src)
            if is_http_url(url):
                assets.add(url)

        # Scripts
        for src in response.css("script::attr(src)").getall():
            src = src.strip()
            if not src:
                continue
            url = response.urljoin(src)
            if is_http_url(url):
                assets.add(url)

        # Stylesheets and other <link> resources
        for href in response.css("link::attr(href)").getall():
            href = href.strip()
            if not href:
                continue
            url = response.urljoin(href)
            if is_http_url(url) and is_asset_url(url):
                assets.add(url)

        return sorted(assets)

    def parse_asset(self, response):
        content_type = response.headers.get(b"Content-Type", b"").decode("utf-8") or None
        content_length_raw = response.headers.get(b"Content-Length")
        content_length = None
        if content_length_raw:
            try:
                content_length = int(content_length_raw.decode("utf-8"))
            except ValueError:
                content_length = None

        yield {
            "item_type": "asset",
            "url": response.url,
            "status": response.status,
            "content_type": content_type,
            "content_length": content_length,
        }

    def asset_error(self, failure):
        request = failure.request
        self.logger.warning(f"Asset HEAD failed: {request.url} ({failure.value})")
        # Still yield an item so we record broken assets
        yield {
            "item_type": "asset",
            "url": request.url,
            "status": None,
            "content_type": None,
            "content_length": None,
        }


class MySQLStorePipeline:
    """
    Pipeline:
    - sets up site + crawl_run
    - stores pages / content / links / assets / asset_refs
    """

    def __init__(self):
        self.conn = None
        self.cursor = None
        self.run_id = None
        self.site_id = None
        self.asset_cache = {}  # (run_id, url) -> asset_id
        self.pages_count = 0
        self.assets_count = 0

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def open_spider(self, spider):
        db_host = os.getenv("DB_HOST", "db")
        db_port = int(os.getenv("DB_PORT", "3306"))
        db_name = os.getenv("DB_NAME", "crawlerdb")
        db_user = os.getenv("DB_USER", "crawler")
        db_password = os.getenv("DB_PASSWORD", "crawlerpass")

        self.conn = pymysql.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=db_name,
            charset="utf8mb4",
            autocommit=True,
        )
        self.cursor = self.conn.cursor()

        # Ensure site exists
        domain = spider.site_domain
        self.cursor.execute("SELECT id FROM sites WHERE domain = %s", (domain,))
        row = self.cursor.fetchone()
        if row:
            self.site_id = row[0]
        else:
            self.cursor.execute(
                "INSERT INTO sites (domain, created_at) VALUES (%s, UTC_TIMESTAMP())",
                (domain,),
            )
            self.site_id = self.cursor.lastrowid

        # Create crawl_run
        self.run_id = str(uuid.uuid4())
        spider.run_id = self.run_id

        self.cursor.execute(
            """
            INSERT INTO crawl_runs (run_id, site_id, started_at)
            VALUES (%s, %s, UTC_TIMESTAMP())
            """,
            (self.run_id, self.site_id),
        )

        self.pages_count = 0
        self.assets_count = 0

    def close_spider(self, spider):
        # Update crawl_run summary
        if self.run_id:
            self.cursor.execute(
                """
                UPDATE crawl_runs
                SET finished_at = UTC_TIMESTAMP(),
                    total_pages = %s,
                    total_assets = %s
                WHERE run_id = %s
                """,
                (self.pages_count, self.assets_count, self.run_id),
            )

        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()

    def process_item(self, item, spider):
        if item.get("item_type") == "page":
            self._store_page(item)
        elif item.get("item_type") == "asset":
            self._store_asset_head(item)
        return item

    def _store_page(self, item):
        # Insert page metadata
        self.cursor.execute(
            """
            INSERT INTO pages
                (run_id, url, status, fetched_at, title, meta_description, canonical_url, processed)
            VALUES
                (%s, %s, %s, UTC_TIMESTAMP(), %s, %s, %s, FALSE)
            """,
            (
                self.run_id,
                item["url"],
                item.get("status"),
                item.get("title"),
                item.get("meta_description"),
                item.get("canonical_url"),
            ),
        )
        page_id = self.cursor.lastrowid
        self.pages_count += 1

        # Insert compressed HTML into page_contents
        raw_html_bytes = item["html"].encode("utf-8", errors="ignore")
        compressed = zlib.compress(raw_html_bytes, level=9)
        uncompressed_length = len(raw_html_bytes)

        self.cursor.execute(
            """
            INSERT INTO page_contents (page_id, run_id, content, uncompressed_length)
            VALUES (%s, %s, %s, %s)
            """,
            (page_id, self.run_id, compressed, uncompressed_length),
        )

        # Insert page links
        for link_url in item.get("page_links", []):
            self.cursor.execute(
                """
                INSERT INTO page_links (run_id, from_page_id, to_url)
                VALUES (%s, %s, %s)
                """,
                (self.run_id, page_id, link_url),
            )

        # Insert asset references
        for asset_url in item.get("assets", []):
            asset_id = self._get_or_create_asset(asset_url)
            self.cursor.execute(
                """
                INSERT IGNORE INTO asset_references (run_id, page_id, asset_id)
                VALUES (%s, %s, %s)
                """,
                (self.run_id, page_id, asset_id),
            )

    def _get_or_create_asset(self, url: str) -> int:
        key = (self.run_id, url)
        if key in self.asset_cache:
            return self.asset_cache[key]

        # See if it already exists for this run
        self.cursor.execute(
            "SELECT id FROM assets WHERE run_id = %s AND url = %s",
            (self.run_id, url),
        )
        row = self.cursor.fetchone()
        if row:
            asset_id = row[0]
        else:
            self.cursor.execute(
                "INSERT INTO assets (run_id, url) VALUES (%s, %s)",
                (self.run_id, url),
            )
            asset_id = self.cursor.lastrowid
            self.assets_count += 1

        self.asset_cache[key] = asset_id
        return asset_id

    def _store_asset_head(self, item):
        url = item["url"]
        status = item.get("status")
        content_type = item.get("content_type")
        content_length = item.get("content_length")

        # Try to update existing; if not, insert new
        self.cursor.execute(
            "SELECT id FROM assets WHERE run_id = %s AND url = %s",
            (self.run_id, url),
        )
        row = self.cursor.fetchone()
        if row:
            asset_id = row[0]
            self.cursor.execute(
                """
                UPDATE assets
                SET status = %s,
                    content_type = %s,
                    content_length = %s,
                    fetched_at = UTC_TIMESTAMP()
                WHERE id = %s
                """,
                (status, content_type, content_length, asset_id),
            )
        else:
            self.cursor.execute(
                """
                INSERT INTO assets
                    (run_id, url, status, content_type, content_length, fetched_at)
                VALUES
                    (%s, %s, %s, %s, %s, UTC_TIMESTAMP())
                """,
                (self.run_id, url, status, content_type, content_length),
            )
            asset_id = self.cursor.lastrowid
            self.assets_count += 1

        # Update cache too
        self.asset_cache[(self.run_id, url)] = asset_id
