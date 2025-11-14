import os
import uuid
import zlib
import pymysql

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
