#!/usr/bin/env python3
"""
Keyword rank tracker for hotel clients, built on the DataForSEO SERP API.

Reads keyword/location pairs from a CSV, queues one Google organic SERP task per
keyword per device, waits for the queue to actually finish, and writes the target
domain's position for each keyword to an output CSV.

Credentials come from the environment (DFS_LOGIN / DFS_PASSWORD), never from this
file. Copy .env.example to .env and fill it in, or export the two variables.

    python rank_tracker.py --target-domain peerlesshotels.com

Run `python rank_tracker.py --help` for the full set of options.
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from requests.auth import HTTPBasicAuth

BASE_URL = "https://api.dataforseo.com/v3/"
ENDPOINT_POST = "serp/google/organic/task_post"
ENDPOINT_READY = "serp/google/organic/tasks_ready"
ENDPOINT_GET = "serp/google/organic/task_get/advanced/"

# DataForSEO accepts up to 100 tasks in a single task_post call.
MAX_TASKS_PER_POST = 100

OUTPUT_FIELDS = [
    "keyword",
    "device",
    "location_code",
    "location_label",
    "status",
    "rank_group",
    "rank_absolute",
    "ranking_url",
    "task_id",
    "checked_at",
]


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

def load_dotenv(path=".env"):
    """Populate os.environ from a .env file, without overwriting real env vars."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get_credentials():
    login = os.environ.get("DFS_LOGIN")
    password = os.environ.get("DFS_PASSWORD")
    if not login or not password:
        sys.exit(
            "DFS_LOGIN and DFS_PASSWORD are not set.\n"
            "Copy .env.example to .env and fill in your DataForSEO credentials, "
            "or export them in your shell before running this script."
        )
    return HTTPBasicAuth(login, password)


def read_keywords(path):
    """Read keyword rows. Requires columns: keyword, location_code, location_label."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = {"keyword", "location_code", "location_label"} - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"{path} is missing required column(s): {', '.join(sorted(missing))}")

        rows, seen = [], set()
        for line_no, row in enumerate(reader, start=2):
            keyword = (row.get("keyword") or "").strip()
            if not keyword:
                continue
            try:
                location_code = int(str(row["location_code"]).strip())
            except (TypeError, ValueError):
                sys.exit(f"{path} line {line_no}: location_code is not an integer")

            key = (keyword.lower(), location_code)
            if key in seen:
                print(f"  skipping duplicate row {line_no}: {keyword} @ {location_code}")
                continue
            seen.add(key)

            rows.append({
                "keyword": keyword,
                "location_code": location_code,
                "location_label": (row.get("location_label") or "").strip(),
            })

    if not rows:
        sys.exit(f"No keywords found in {path}")
    return rows


# --------------------------------------------------------------------------- #
# API calls
# --------------------------------------------------------------------------- #

def api_request(method, path, auth, timeout, json_body=None, attempts=4):
    """Call the API, retrying network and 5xx failures with exponential backoff."""
    url = BASE_URL + path
    delay = 2
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method, url, auth=auth, json=json_body, timeout=timeout
            )
            if response.status_code >= 500:
                raise requests.HTTPError(f"HTTP {response.status_code}")
            if response.status_code != 200:
                return None, f"HTTP {response.status_code}: {response.text[:300]}"
            return response.json(), None
        except (requests.RequestException, ValueError) as exc:
            if attempt == attempts:
                return None, f"request failed after {attempts} attempts: {exc}"
            time.sleep(delay)
            delay *= 2
    return None, "unreachable"


def post_tasks(jobs, auth, language_code, depth, run_tag, timeout):
    """Queue SERP tasks. Returns {task_id: job} for everything accepted."""
    payload = [
        {
            "keyword": job["keyword"],
            "location_code": job["location_code"],
            "language_code": language_code,
            "device": job["device"],
            "depth": depth,
            "tag": run_tag,
        }
        for job in jobs
    ]

    body, error = api_request("POST", ENDPOINT_POST, auth, timeout, json_body=payload)
    if error:
        print(f"  task_post failed: {error}")
        return {}
    if body.get("status_code") != 20000:
        print(f"  task_post rejected: {body.get('status_code')} {body.get('status_message')}")
        return {}

    accepted = {}
    for task in body.get("tasks", []):
        # 20100 = Task Created. Anything else means this task was not queued.
        if task.get("status_code") != 20100 or not task.get("id"):
            data = task.get("data", {})
            print(
                f"  rejected: {data.get('keyword')!r} [{data.get('device')}] "
                f"-> {task.get('status_code')} {task.get('status_message')}"
            )
            continue

        data = task.get("data", {})
        accepted[task["id"]] = {
            "keyword": data.get("keyword"),
            "device": data.get("device"),
            "location_code": data.get("location_code"),
        }
    return accepted


def fetch_ready_ids(auth, timeout):
    """IDs of finished-but-uncollected tasks on this account."""
    body, error = api_request("GET", ENDPOINT_READY, auth, timeout)
    if error or body.get("status_code") != 20000:
        return set()
    ready = set()
    for task in body.get("tasks", []):
        for item in task.get("result") or []:
            if item.get("id"):
                ready.add(item["id"])
    return ready


def fetch_task(task_id, auth, timeout):
    """Returns (result_payload, error_message)."""
    body, error = api_request("GET", ENDPOINT_GET + task_id, auth, timeout)
    if error:
        return None, error
    if body.get("status_code") != 20000:
        return None, f"{body.get('status_code')} {body.get('status_message')}"

    tasks = body.get("tasks") or []
    if not tasks:
        return None, "no task in response"
    task = tasks[0]
    if task.get("status_code") != 20000:
        return None, f"{task.get('status_code')} {task.get('status_message')}"
    return task.get("result"), None


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

def collect_organic_items(node, found):
    """
    Walk the result payload and collect every organic listing.

    Deliberately recursive rather than assuming a fixed nesting depth: the
    advanced endpoint returns a mixed list (organic, hotels_pack, people_also_ask,
    paid, related_searches...) and the exact nesting has changed before. Requiring
    both type == "organic" and a url keeps SERP-feature blocks out of the results,
    which matters here because a hotels_pack element carries a google.com URL and a
    rank_group that has nothing to do with organic position.
    """
    if isinstance(node, dict):
        if node.get("type") == "organic" and node.get("url"):
            found.append(node)
        for value in node.values():
            collect_organic_items(value, found)
    elif isinstance(node, list):
        for value in node:
            collect_organic_items(value, found)


def normalise_host(value):
    host = (value or "").strip().lower()
    if "//" not in host:
        host = "//" + host
    host = urlparse(host).hostname or ""
    return host[4:] if host.startswith("www.") else host


def host_matches(url, target_host):
    """True for the domain itself and its subdomains, false for lookalikes."""
    host = normalise_host(url)
    return bool(host) and (host == target_host or host.endswith("." + target_host))


def find_ranking(result_payload, target_host):
    """Best organic placement for the target domain, or None if it is absent."""
    organic = []
    collect_organic_items(result_payload, organic)

    matches = [item for item in organic if host_matches(item.get("url"), target_host)]
    if not matches:
        return None

    best = min(
        matches,
        key=lambda item: (
            item.get("rank_absolute") if item.get("rank_absolute") is not None else 10**6
        ),
    )
    return {
        "rank_group": best.get("rank_group"),
        "rank_absolute": best.get("rank_absolute"),
        "ranking_url": best.get("url"),
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def parse_args():
    parser = argparse.ArgumentParser(
        description="Track a domain's Google organic positions via the DataForSEO SERP API."
    )
    parser.add_argument("--target-domain", default="peerlesshotels.com",
                        help="domain to look for in the SERPs (default: peerlesshotels.com)")
    parser.add_argument("--keywords", default="keywords.csv",
                        help="input CSV: keyword, location_code, location_label")
    parser.add_argument("--out", default=None,
                        help="output CSV (default: rankings_<domain>_<UTC timestamp>.csv)")
    parser.add_argument("--devices", default="desktop,mobile",
                        help="comma-separated device list (default: desktop,mobile)")
    parser.add_argument("--language-code", default="en", help="language code (default: en)")
    parser.add_argument("--depth", type=int, default=100,
                        help="how many organic results to scan per SERP (default: 100)")
    parser.add_argument("--poll-interval", type=int, default=30,
                        help="seconds between tasks_ready polls (default: 30)")
    parser.add_argument("--max-wait", type=int, default=2700,
                        help="seconds to wait for the queue to drain (default: 2700)")
    parser.add_argument("--http-timeout", type=int, default=60,
                        help="per-request timeout in seconds (default: 60)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the queries that would be sent, call nothing")
    return parser.parse_args()


def build_jobs(keyword_rows, devices):
    return [
        dict(row, device=device)
        for row in keyword_rows
        for device in devices
    ]


def write_row(writer, handle, job, status, ranking=None, task_id=""):
    writer.writerow({
        "keyword": job["keyword"],
        "device": job["device"],
        "location_code": job["location_code"],
        "location_label": job.get("location_label", ""),
        "status": status,
        "rank_group": (ranking or {}).get("rank_group", ""),
        "rank_absolute": (ranking or {}).get("rank_absolute", ""),
        "ranking_url": (ranking or {}).get("ranking_url", ""),
        "task_id": task_id,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    handle.flush()


def main():
    args = parse_args()
    load_dotenv()

    target_host = normalise_host(args.target_domain)
    if not target_host:
        sys.exit(f"Could not read a hostname from --target-domain {args.target_domain!r}")

    devices = [d.strip() for d in args.devices.split(",") if d.strip()]
    if not devices:
        sys.exit("--devices produced an empty list")

    keyword_rows = read_keywords(args.keywords)
    jobs = build_jobs(keyword_rows, devices)

    out_path = args.out or (
        f"rankings_{target_host.replace('.', '_')}_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    )

    print(f"target domain : {target_host}")
    print(f"keywords      : {len(keyword_rows)} from {args.keywords}")
    print(f"devices       : {', '.join(devices)}")
    print(f"queries       : {len(jobs)}")
    print(f"depth         : top {args.depth} organic results")
    print(f"output        : {out_path}")

    if args.dry_run:
        print("\ndry run, nothing sent:")
        for job in jobs:
            print(f"  {job['keyword']!r} [{job['device']}] @ {job['location_code']} "
                  f"({job.get('location_label')})")
        return 0

    auth = get_credentials()
    run_tag = f"rank-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    # Queue everything first, so the whole set cooks in parallel on their side.
    print("\nqueueing tasks...")
    pending, unqueued = {}, []
    for start in range(0, len(jobs), MAX_TASKS_PER_POST):
        chunk = jobs[start:start + MAX_TASKS_PER_POST]
        accepted = post_tasks(chunk, auth, args.language_code, args.depth,
                              run_tag, args.http_timeout)
        print(f"  batch {start // MAX_TASKS_PER_POST + 1}: "
              f"{len(accepted)}/{len(chunk)} accepted")

        # Match accepted tasks back to their job rows so location_label survives.
        by_key = {}
        for job in chunk:
            by_key.setdefault(
                (job["keyword"].lower(), job["device"], job["location_code"]), []
            ).append(job)

        claimed = set()
        for task_id, echo in accepted.items():
            key = (
                (echo.get("keyword") or "").lower(),
                echo.get("device"),
                echo.get("location_code"),
            )
            candidates = by_key.get(key) or []
            job = candidates.pop(0) if candidates else None
            if job is None:
                # Fall back to the echoed values rather than dropping the task.
                job = {
                    "keyword": echo.get("keyword"),
                    "device": echo.get("device"),
                    "location_code": echo.get("location_code"),
                    "location_label": "",
                }
            else:
                claimed.add(id(job))
            pending[task_id] = job

        unqueued.extend(job for job in chunk if id(job) not in claimed)

    if not pending:
        sys.exit("No tasks were queued. Check the credentials, account balance, and the errors above.")

    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        handle.flush()

        for job in unqueued:
            write_row(writer, handle, job, "queue_failed")

        print(f"\nwaiting for {len(pending)} tasks (polling every {args.poll_interval}s, "
              f"giving up after {args.max_wait}s)...")
        ranked = 0
        deadline = time.monotonic() + args.max_wait

        while pending and time.monotonic() < deadline:
            ready = fetch_ready_ids(auth, args.http_timeout) & set(pending)
            if not ready:
                time.sleep(args.poll_interval)
                continue

            for task_id in sorted(ready):
                job = pending.pop(task_id)
                result, error = fetch_task(task_id, auth, args.http_timeout)
                if error:
                    write_row(writer, handle, job, "api_error", task_id=task_id)
                    print(f"  [{job['device']:>7}] {job['keyword']!r} -> api_error: {error}")
                    continue

                ranking = find_ranking(result, target_host)
                if ranking:
                    ranked += 1
                    write_row(writer, handle, job, "ranked", ranking, task_id)
                    print(f"  [{job['device']:>7}] {job['keyword']!r} -> "
                          f"organic {ranking['rank_group']}, "
                          f"on-page {ranking['rank_absolute']}")
                else:
                    write_row(writer, handle, job, f"not_in_top_{args.depth}", task_id=task_id)
                    print(f"  [{job['device']:>7}] {job['keyword']!r} -> "
                          f"not in top {args.depth}")

            if pending:
                print(f"  {len(pending)} still running")

        # Anything left never showed up in tasks_ready. Try a direct read before
        # giving up, since a task can complete without being announced.
        for task_id, job in list(pending.items()):
            result, error = fetch_task(task_id, auth, args.http_timeout)
            if error or result is None:
                write_row(writer, handle, job, "timeout", task_id=task_id)
                print(f"  [{job['device']:>7}] {job['keyword']!r} -> timeout")
                continue
            ranking = find_ranking(result, target_host)
            if ranking:
                ranked += 1
                write_row(writer, handle, job, "ranked", ranking, task_id)
            else:
                write_row(writer, handle, job, f"not_in_top_{args.depth}", task_id=task_id)
            pending.pop(task_id, None)

    total = len(jobs)
    print(f"\ndone. {ranked}/{total} queries returned a position for {target_host}.")
    print(f"results: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
