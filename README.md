# Docker Hub Rate Limit Monitor

A lightweight, self-contained monitoring stack that tracks your Docker Hub pull
rate-limit quota and visualises it in Grafana.

```
┌───────────────────────────────────────────────────────────┐
│  exporter (Python)  →  Prometheus  →  Grafana : 3000      │
└───────────────────────────────────────────────────────────┘
```

## How it works

The exporter calls Docker Hub every 60 seconds using the technique recommended
by Docker's own documentation:

1. Obtain a short-lived Bearer token (anonymous or authenticated).
2. Send a `HEAD` request to
   `https://registry-1.docker.io/v2/ratelimitpreview/test/manifests/latest`.
3. Read the response headers:
   - `RateLimit-Limit: 100;w=21600` → 100 pulls per 6-hour window
   - `RateLimit-Remaining: 76;w=21600` → 76 pulls left

> **HEAD requests to `ratelimitpreview/test` do not consume your rate-limit quota.**
> Docker created this repository specifically for quota introspection.

The exporter exposes these values as Prometheus gauges; Grafana displays them
on a pre-built dashboard.

## Prerequisites

- Docker Engine ≥ 24 with the Compose plugin (`docker compose`)
- Outbound internet access to `auth.docker.io` and `registry-1.docker.io`

## Quick start

```bash
# 1. Clone / enter the project
cd dockerRateLimitMonitor

# 2. (Optional) configure credentials and port
cp .env.example .env
$EDITOR .env          # set DOCKERHUB_USERNAME / DOCKERHUB_PASSWORD if desired

# 3. Build and start the stack
docker compose up -d --build

# 4. Open Grafana
open http://localhost:3000
# Login: admin / admin  (or whatever you set in .env)
```

The **"Docker Hub Rate Limit Monitor"** dashboard is pre-provisioned and will
appear under **Dashboards** immediately.

## Configuration

All knobs are environment variables. Copy `.env.example` to `.env` and edit:

| Variable | Default | Description |
|---|---|---|
| `DOCKERHUB_USERNAME` | _(empty)_ | Docker Hub username — leave blank for anonymous monitoring |
| `DOCKERHUB_PASSWORD` | _(empty)_ | Docker Hub password or access token |
| `POLL_INTERVAL` | `60` | Seconds between polls |
| `GRAFANA_USER` | `admin` | Grafana admin username |
| `GRAFANA_PASSWORD` | `admin` | Grafana admin password |
| `GRAFANA_PORT` | `3000` | Host port to expose Grafana on |

### Anonymous vs authenticated

| Mode | Pulls / 6 h | How |
|---|---|---|
| Anonymous (default) | 100 | Rate is shared per **public IP** |
| Authenticated — free | 200 | Per Docker Hub account |
| Authenticated — Pro/Team | Unlimited | Per Docker Hub account |

## Exposed metrics

| Metric | Description |
|---|---|
| `docker_ratelimit_limit_total` | Total pulls allowed in the current window |
| `docker_ratelimit_remaining_total` | Pulls still available |
| `docker_ratelimit_used_total` | Pulls consumed so far |
| `docker_ratelimit_window_seconds` | Window duration (seconds) |
| `docker_ratelimit_scrape_success` | `1` if last poll succeeded, `0` otherwise |
| `docker_ratelimit_exporter_info` | Labels: `public_ip`, `mode`, `dockerhub_user` |

Metrics are served on `http://exporter:8000/metrics` (internal).
Prometheus scrapes this endpoint; it is not exposed to the host.

## Dashboard panels

- **Remaining / Limit / Used** — current snapshot stat boxes with colour thresholds
- **Window (hours)** — rolling window duration
- **Usage %** — gauge: green → yellow → red as quota is consumed
- **Exporter Status** — UP / DOWN indicator for the last poll
- **Public IP** — which IP Docker Hub is rate-limiting
- **Rate Limit History** — time-series: Limit (dashed), Remaining, Used
- **Scrape Health** — bar chart showing any polling failures

## Stopping and cleaning up

```bash
# Stop containers (data is preserved in Docker volumes)
docker compose down

# Stop and remove all persistent data
docker compose down -v
```

## Troubleshooting

**No data in Grafana?**
```bash
docker compose logs exporter   # check for auth or network errors
docker compose logs prometheus # check scrape status
```

**Rate-limit headers missing (`--`)** — Docker Hub only returns these headers
for requests that go through the standard registry endpoint. Ensure the
exporter container has outbound internet access.

**Custom poll interval** — if you change `POLL_INTERVAL` in `.env`, also update
`scrape_interval` in `prometheus/prometheus.yml` to match; then run
`docker compose restart prometheus`.
