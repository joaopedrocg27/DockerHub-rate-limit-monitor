#!/usr/bin/env python3
"""
Docker Hub Rate Limit Exporter
Polls Docker Hub manifest HEAD endpoint (ratelimitpreview/test) to read the
RateLimit-Limit and RateLimit-Remaining response headers and exposes them as
Prometheus metrics.

Note: HEAD requests to ratelimitpreview/test do NOT count against the rate
limit — they are provided by Docker specifically for this monitoring use-case.
"""

import logging
import os
import time

import requests
from prometheus_client import Gauge, Info, start_http_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Configuration — all values are overridable via environment variables
# --------------------------------------------------------------------------- #
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
EXPORTER_PORT = int(os.getenv("EXPORTER_PORT", "8000"))
DOCKERHUB_USERNAME = os.getenv("DOCKERHUB_USERNAME", "")
DOCKERHUB_PASSWORD = os.getenv("DOCKERHUB_PASSWORD", "")
# Comma-separated list of public IPs to monitor. When set, polls are skipped
# for any IP not in the list. Leave blank to allow all IPs.
_IP_ALLOWLIST_RAW = os.getenv("IP_ALLOWLIST", "")
IP_ALLOWLIST: set = {
    ip.strip() for ip in _IP_ALLOWLIST_RAW.split(",") if ip.strip()
}

AUTH_URL = "https://auth.docker.io/token"
MANIFEST_URL = (
    "https://registry-1.docker.io/v2/ratelimitpreview/test/manifests/latest"
)
TOKEN_SCOPE = "repository:ratelimitpreview/test:pull"
REQUEST_TIMEOUT = 15  # seconds

# --------------------------------------------------------------------------- #
# Prometheus metrics
# --------------------------------------------------------------------------- #
RATE_LIMIT_TOTAL = Gauge(
    "docker_ratelimit_limit_total",
    "Docker Hub pull rate limit — total pulls allowed per window",
    ["public_ip"],
)
RATE_LIMIT_REMAINING = Gauge(
    "docker_ratelimit_remaining_total",
    "Docker Hub pull rate limit — remaining pulls in current window",
    ["public_ip"],
)
RATE_LIMIT_USED = Gauge(
    "docker_ratelimit_used_total",
    "Docker Hub pull rate limit — pulls consumed in current window",
    ["public_ip"],
)
RATE_LIMIT_WINDOW = Gauge(
    "docker_ratelimit_window_seconds",
    "Docker Hub pull rate limit — window duration in seconds",
    ["public_ip"],
)
SCRAPE_SUCCESS = Gauge(
    "docker_ratelimit_scrape_success",
    "1 if the last scrape succeeded, 0 otherwise",
    ["public_ip"],
)
IP_ALLOWED = Gauge(
    "docker_ratelimit_ip_allowed",
    "1 if the current public IP is in the configured allowlist (or no allowlist set), 0 otherwise",
    ["public_ip"],
)
EXPORTER_INFO = Info(
    "docker_ratelimit_exporter",
    "Metadata about the Docker rate-limit exporter",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _public_ip() -> str:
    """Return the public IP of this machine (best-effort)."""
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            return r.text.strip()
        except Exception:
            pass
    return "unknown"


def _get_token() -> str:
    """Obtain a short-lived Bearer token from Docker Hub auth service."""
    params = {"service": "registry.docker.io", "scope": TOKEN_SCOPE}
    auth = (DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD) if DOCKERHUB_USERNAME else None
    resp = requests.get(AUTH_URL, params=params, auth=auth, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["token"]


def _parse_header(value: str):
    """
    Parse a Docker Hub rate-limit header such as ``100;w=21600``.
    Returns ``(limit_int, window_seconds_int)``; either may be ``None``.
    """
    if not value:
        return None, None
    parts = value.split(";")
    try:
        limit = int(parts[0])
    except (ValueError, IndexError):
        return None, None
    window = None
    for part in parts[1:]:
        if part.startswith("w="):
            try:
                window = int(part[2:])
            except ValueError:
                pass
    return limit, window


# --------------------------------------------------------------------------- #
# Core collection loop
# --------------------------------------------------------------------------- #
def collect() -> None:
    """Fetch rate-limit headers from Docker Hub and update gauge values."""
    current_ip = _public_ip()

    # IP allowlist check — re-detect public IP on every poll so dynamic-IP
    # environments are handled correctly.
    if IP_ALLOWLIST:
        if current_ip not in IP_ALLOWLIST:
            log.info(
                "Current IP %s is not in IP_ALLOWLIST — skipping poll",
                current_ip,
            )
            IP_ALLOWED.labels(public_ip=current_ip).set(0)
            SCRAPE_SUCCESS.labels(public_ip=current_ip).set(0)
            return
        IP_ALLOWED.labels(public_ip=current_ip).set(1)
    else:
        IP_ALLOWED.labels(public_ip=current_ip).set(1)  # no allowlist → always allowed

    try:
        token = _get_token()
        resp = requests.head(
            MANIFEST_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()

        limit_header = resp.headers.get("RateLimit-Limit", "")
        remaining_header = resp.headers.get("RateLimit-Remaining", "")

        log.info("RateLimit-Limit:     %s", limit_header or "(not present)")
        log.info("RateLimit-Remaining: %s", remaining_header or "(not present)")

        limit, window = _parse_header(limit_header)
        remaining, _ = _parse_header(remaining_header)

        if limit is not None:
            RATE_LIMIT_TOTAL.labels(public_ip=current_ip).set(limit)
        if remaining is not None:
            RATE_LIMIT_REMAINING.labels(public_ip=current_ip).set(remaining)
            if limit is not None:
                RATE_LIMIT_USED.labels(public_ip=current_ip).set(limit - remaining)
        if window is not None:
            RATE_LIMIT_WINDOW.labels(public_ip=current_ip).set(window)

        SCRAPE_SUCCESS.labels(public_ip=current_ip).set(1)
        log.info(
            "Collected — limit=%s remaining=%s window=%ss",
            limit,
            remaining,
            window,
        )
    except Exception as exc:
        log.error("Scrape failed: %s", exc)
        SCRAPE_SUCCESS.labels(public_ip=current_ip).set(0)


def main() -> None:
    mode = "authenticated" if DOCKERHUB_USERNAME else "anonymous"
    public_ip = _public_ip()
    log.info("Docker Rate Limit Exporter starting (mode=%s, ip=%s)", mode, public_ip)

    if IP_ALLOWLIST:
        log.info(
            "IP allowlist active — monitoring only: %s",
            ", ".join(sorted(IP_ALLOWLIST)),
        )
        if public_ip not in IP_ALLOWLIST:
            log.warning(
                "Current IP %s is NOT in the allowlist — polls will be skipped until the IP changes",
                public_ip,
            )
    else:
        log.info("IP allowlist: disabled (all IPs allowed)")

    EXPORTER_INFO.info(
        {
            "mode": mode,
            "dockerhub_user": DOCKERHUB_USERNAME or "",
            "public_ip": public_ip,
            "poll_interval_seconds": str(POLL_INTERVAL),
            "ip_allowlist": ",".join(sorted(IP_ALLOWLIST)) if IP_ALLOWLIST else "",
        }
    )

    start_http_server(EXPORTER_PORT)
    log.info("Metrics available at http://0.0.0.0:%d/metrics", EXPORTER_PORT)

    while True:
        collect()
        log.info("Next poll in %ds …", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
