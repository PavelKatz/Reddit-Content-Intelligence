#!/usr/bin/env python3
"""Reddit Intel proxy — proxies to Reddit's public .json API. No auth needed."""

import http.server
import socketserver
import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from urllib.parse import urlparse, parse_qs

os.chdir(os.path.dirname(os.path.abspath(__file__)))

PORT = 3456
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Rate limiter: 1.5s between Reddit requests (~40 req/min, well under anonymous limit)
_last_request = 0
RATE_LIMIT_SECONDS = 1.5
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


def reddit_get(url):
    """GET Reddit with rate-limit, browser UA, and retry on 429/5xx."""
    global _last_request

    last_err = None
    for attempt in range(MAX_RETRIES):
        now = time.time()
        wait = max(0, RATE_LIMIT_SECONDS - (now - _last_request))
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                backoff = 2 ** (attempt + 1)  # 2s, 4s, 8s
                time.sleep(backoff)
                continue
            raise
    raise last_err  # unreachable, but keeps type-checkers happy


class RedditProxyHandler(http.server.SimpleHTTPRequestHandler):
    """Handles /api/* as Reddit proxy, everything else as static files."""

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/search":
            self._handle_search(parsed)
        elif parsed.path == "/api/comments":
            self._handle_comments(parsed)
        else:
            super().do_GET()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def _json_ok(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _json_err(self, status, msg):
        body = json.dumps({"error": msg}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _handle_search(self, parsed):
        params = parse_qs(parsed.query)
        subreddit = params.get("subreddit", [""])[0]
        q = params.get("q", [""])[0]
        sort = params.get("sort", ["top"])[0]
        t = params.get("t", ["month"])[0]
        limit = params.get("limit", ["25"])[0]
        after = params.get("after", [""])[0]

        if not subreddit or not q:
            self._json_err(400, "Missing subreddit or q")
            return

        try:
            qp = {"q": q, "sort": sort, "t": t, "limit": limit, "restrict_sr": "true"}
            if after:
                qp["after"] = after
            url = f"https://www.reddit.com/r/{urllib.parse.quote(subreddit)}/search.json?{urllib.parse.urlencode(qp)}"
            data = reddit_get(url)
            self._json_ok(data)
        except urllib.error.HTTPError as e:
            self._json_err(e.code, f"Reddit: {e.read().decode()[:200]}" if e.fp else str(e))
        except Exception as e:
            self._json_err(500, str(e))

    def _handle_comments(self, parsed):
        params = parse_qs(parsed.query)
        article_id = params.get("article_id", [""])[0]
        if not article_id:
            self._json_err(400, "Missing article_id")
            return

        try:
            url = f"https://www.reddit.com/comments/{urllib.parse.quote(article_id)}.json?sort=top&limit=20"
            data = reddit_get(url)
            self._json_ok(data)
        except urllib.error.HTTPError as e:
            self._json_err(e.code, f"Reddit: {e.read().decode()[:200]}" if e.fp else str(e))
        except Exception as e:
            self._json_err(500, str(e))

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with ReusableTCPServer(("", PORT), RedditProxyHandler) as httpd:
        print(f"Reddit Intel on http://localhost:{PORT}")
        httpd.serve_forever()
