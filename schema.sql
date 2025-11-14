-- ============================================================
--  SEO / Site Crawler Database Schema
-- ============================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ============================================================
-- 0. Sites
-- ============================================================
DROP TABLE IF EXISTS sites;
CREATE TABLE sites (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    domain        VARCHAR(255) NOT NULL UNIQUE,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;


-- ============================================================
-- 1. Crawl Runs
-- ============================================================
DROP TABLE IF EXISTS crawl_runs;
CREATE TABLE crawl_runs (
    run_id        CHAR(36) NOT NULL PRIMARY KEY,
    site_id       INT NOT NULL,
    started_at    DATETIME NOT NULL,
    finished_at   DATETIME NULL,
    total_pages   INT NULL,
    total_assets  INT NULL,

    CONSTRAINT fk_crawlruns_site FOREIGN KEY (site_id)
        REFERENCES sites(id) ON DELETE CASCADE
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_crawl_runs_siteid ON crawl_runs(site_id);


-- ============================================================
-- 2. Pages
-- ============================================================
DROP TABLE IF EXISTS pages;
CREATE TABLE pages (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id           CHAR(36) NOT NULL,
    url              TEXT NOT NULL,
    STATUS           INT NULL,
    fetched_at       DATETIME NULL,

    title            VARCHAR(1024) NULL,
    meta_description TEXT NULL,
    canonical_url    TEXT NULL,

    processed        BOOLEAN NOT NULL DEFAULT FALSE,

    CONSTRAINT fk_pages_run FOREIGN KEY (run_id)
        REFERENCES crawl_runs(run_id) ON DELETE CASCADE
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_pages_runid ON pages(run_id);
CREATE INDEX idx_pages_url_100 ON pages(url(100));
CREATE INDEX idx_pages_title_100 ON pages(title(100));


-- ============================================================
-- 3. Page Contents (compressed HTML)
-- ============================================================
DROP TABLE IF EXISTS page_contents;
CREATE TABLE page_contents (
    page_id              BIGINT PRIMARY KEY,
    run_id               CHAR(36) NOT NULL,
    content              LONGBLOB NOT NULL,  -- compressed (gzip/zstd)
    UNCOMPRESSED_LENGTH  INT NOT NULL,

    CONSTRAINT fk_pagecontents_page FOREIGN KEY (page_id)
        REFERENCES pages(id) ON DELETE CASCADE,

    CONSTRAINT fk_pagecontents_run FOREIGN KEY (run_id)
        REFERENCES crawl_runs(run_id) ON DELETE CASCADE
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;


-- ============================================================
-- 4. Page Links (internal graph)
-- ============================================================
DROP TABLE IF EXISTS page_links;
CREATE TABLE page_links (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id        CHAR(36) NOT NULL,
    from_page_id  BIGINT NOT NULL,
    to_url        TEXT NOT NULL,

    CONSTRAINT fk_pagelinks_run FOREIGN KEY (run_id)
        REFERENCES crawl_runs(run_id) ON DELETE CASCADE,

    CONSTRAINT fk_pagelinks_frompage FOREIGN KEY (from_page_id)
        REFERENCES pages(id) ON DELETE CASCADE
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_pagelinks_runid      ON page_links(run_id);
CREATE INDEX idx_pagelinks_frompage   ON page_links(from_page_id);
CREATE INDEX idx_pagelinks_to_url_100 ON page_links(to_url(100));


-- ============================================================
-- 5. Assets
-- ============================================================
DROP TABLE IF EXISTS assets;
CREATE TABLE assets (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id          CHAR(36) NOT NULL,
    url             TEXT NOT NULL,

    STATUS          INT NULL,
    content_type    VARCHAR(255) NULL,
    content_length  BIGINT NULL,
    fetched_at      DATETIME NULL,

    CONSTRAINT fk_assets_run FOREIGN KEY (run_id)
        REFERENCES crawl_runs(run_id) ON DELETE CASCADE
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_assets_runid   ON assets(run_id);
CREATE INDEX idx_assets_url_100 ON assets(url(100));


-- ============================================================
-- 6. Asset References (many-to-many: page → asset)
-- ============================================================
DROP TABLE IF EXISTS asset_references;
CREATE TABLE asset_references (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id        CHAR(36) NOT NULL,
    page_id       BIGINT NOT NULL,
    asset_id      BIGINT NOT NULL,

    CONSTRAINT fk_assetrefs_run FOREIGN KEY (run_id)
        REFERENCES crawl_runs(run_id) ON DELETE CASCADE,

    CONSTRAINT fk_assetrefs_page FOREIGN KEY (page_id)
        REFERENCES pages(id) ON DELETE CASCADE,

    CONSTRAINT fk_assetrefs_asset FOREIGN KEY (asset_id)
        REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_assetrefs_runid ON asset_references(run_id);
CREATE INDEX idx_assetrefs_page  ON asset_references(page_id);
CREATE INDEX idx_assetrefs_asset ON asset_references(asset_id);

CREATE UNIQUE INDEX uq_assetrefs_page_asset_run
    ON asset_references(run_id, page_id, asset_id);


-- ============================================================
-- 7. Analysis Rules (rule definitions)
-- ============================================================
DROP TABLE IF EXISTS analysis_rules;
CREATE TABLE analysis_rules (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    CODE          VARCHAR(100) NOT NULL UNIQUE,
    NAME          VARCHAR(255) NOT NULL,
    DESCRIPTION   TEXT NULL,
    category      VARCHAR(100) NULL,
    severity      ENUM('info','warning','error') NOT NULL DEFAULT 'warning'
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;


-- ============================================================
-- 8. Analysis Results (per page, per rule)
-- ============================================================
DROP TABLE IF EXISTS analysis_results;
CREATE TABLE analysis_results (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id     CHAR(36) NOT NULL,
    page_id    BIGINT NOT NULL,
    rule_id    INT NOT NULL,
    passed     BOOLEAN NOT NULL,
    message    TEXT NULL,
    details    JSON NULL,

    CONSTRAINT fk_results_run FOREIGN KEY (run_id)
        REFERENCES crawl_runs(run_id) ON DELETE CASCADE,

    CONSTRAINT fk_results_page FOREIGN KEY (page_id)
        REFERENCES pages(id) ON DELETE CASCADE,

    CONSTRAINT fk_results_rule FOREIGN KEY (rule_id)
        REFERENCES analysis_rules(id) ON DELETE CASCADE
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_results_run_page ON analysis_results(run_id, page_id);
CREATE INDEX idx_results_rule     ON analysis_results(rule_id);

SET FOREIGN_KEY_CHECKS = 1;
