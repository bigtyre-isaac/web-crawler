# Simple web crawler

This project is a small internal web crawler built with Scrapy. It scans a target site and stores the results in MySQL, cataloguing pages, assets, and internal links. Its primary purpose is to gather the data needed for SEO analysis and to help monitor overall site health.

It doesn’t yet perform any SEO checks (like duplicate meta descriptions or titles that are too short or too long) but the collected data makes those easy to identify with a few queries. I may add automated checks later. The database schema already includes tables for rules and page-rule results (pass/fail, etc.); the only part missing is the analyzer that actually runs those checks.