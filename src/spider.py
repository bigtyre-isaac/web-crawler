import os
import scrapy
from urllib.parse import urlparse, urlunparse
from pipelines import MySQLStorePipeline

from scrapy.utils.request import fingerprint as scrapy_fingerprint

def normalized_fingerprint(request):
    norm_url = normalize_url(request.url)
    return scrapy_fingerprint(
        request.replace(url=norm_url)
    )

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


def normalize_url(url: str) -> str:
    """
    Normalises URLs so that:
    - /index.html → /
    - removes fragments (#section)
    - removes default ports
    - ensures trailing slash for directories
    """
    parsed = urlparse(url)
    path = parsed.path

    # Remove default filenames
    if path.endswith("/index.html") or path == "index.html":
        path = "/"

    # Ensure root '/'
    if path == "":
        path = "/"

    # Remove fragment
    new = parsed._replace(path=path, fragment="")

    return urlunparse(new)

def is_asset_url(url):
    lower = url.lower()
    return any(lower.split("?", 1)[0].endswith(ext) for ext in ASSET_EXTENSIONS)


def is_http_url(url):
    return url.startswith("http://") or url.startswith("https://")


class SiteCrawlerSpider(scrapy.Spider):
    name = "site_crawler"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "LOG_LEVEL": "INFO",
        "ITEM_PIPELINES": {
            "pipelines.MySQLStorePipeline": 300,
        },
        "DOWNLOAD_DELAY": 0.1,
        "CONCURRENT_REQUESTS": 16,
        "REQUEST_FINGERPRINTER": "crawler.normalized_fingerprint",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        domain = os.getenv("CRAWL_SITE_DOMAIN")
        start_url = os.getenv("CRAWL_START_URL")
        if not domain or not start_url:
            raise ValueError("Must set CRAWL_SITE_DOMAIN and CRAWL_START_URL")

        self.site_domain = domain
        self.allowed_domains = [domain]
        self.start_urls = [start_url]
        self.seen_assets = set()

    def parse(self, response):
        # Normalised URL for this page
        current_url = normalize_url(response.url)

        content_type = response.headers.get(b"Content-Type", b"").decode("utf-8")
        is_html = "text/html" in content_type or content_type == ""

        # ---- Extract links ----
        page_links = []
        for href in response.css("a::attr(href)").getall():
            href = href.strip()
            if not href or href.startswith("#"):
                continue

            abs_url = normalize_url(response.urljoin(href))
            page_links.append(abs_url)

            # Follow only internal HTML pages
            if is_http_url(abs_url) and not is_asset_url(abs_url):
                parsed = urlparse(abs_url)
                if parsed.netloc.endswith(self.site_domain):
                    norm = normalize_url(abs_url)
                    yield scrapy.Request(norm, callback=self.parse)
        
        # ---- Extract assets ----
        assets = set()
        for sel in ["img::attr(src)", "script::attr(src)", "link::attr(href)"]:
            for src in response.css(sel).getall():
                src = src.strip()
                if not src:
                    continue

                abs_url = normalize_url(response.urljoin(src))
                if is_http_url(abs_url) and is_asset_url(abs_url):
                    assets.add(abs_url)

        # Asset HEAD requests
        for asset_url in assets:
            if asset_url not in self.seen_assets:
                self.seen_assets.add(asset_url)
                yield scrapy.Request(
                    asset_url,
                    method="HEAD",
                    callback=self.parse_asset,
                    errback=self.asset_error,
                    dont_filter=True,
                )

        # ---- Yield page record ----
        if is_html:
            title = (response.css("title::text").get() or "").strip()
            meta_desc = response.css("meta[name='description']::attr(content)").get()
            canonical = response.css("link[rel='canonical']::attr(href)").get()

            yield {
                "item_type": "page",
                "url": current_url,  # <---- NORMALISED
                "status": response.status,
                "title": title or None,
                "meta_description": meta_desc.strip() if meta_desc else None,
                "canonical_url": normalize_url(response.urljoin(canonical)) if canonical else None,
                "html": response.text,
                "page_links": page_links,            # already normalised
                "assets": list(assets),              # already normalised
            }

    def parse_asset(self, response):
        content_type = response.headers.get(b"Content-Type", b"").decode("utf-8") or None
        length = response.headers.get(b"Content-Length")
        size = int(length.decode("utf-8")) if length else None

        yield {
            "item_type": "asset",
            "url": normalize_url(response.url),  # <--- NORMALISED
            "status": response.status,
            "content_type": content_type,
            "content_length": size,
        }


    def asset_error(self, failure):
        request = failure.request
        yield {
            "item_type": "asset",
            "url": request.url,
            "status": None,
            "content_type": None,
            "content_length": None,
        }
