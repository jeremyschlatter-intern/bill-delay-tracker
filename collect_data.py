#!/usr/bin/env python3
"""
Bill Delay Tracker - Data Collection Script

Collects data from the congress.gov API to track how long it takes
for bill summaries to be created after bill text is published.

Optimized for minimal API calls:
- Phase 1: Fetch all bill listings (uses pagination, ~40 requests)
- Phase 2: Fetch bill details to get text/summary counts (~1 call per bill)
- Phase 3: For bills with text, fetch text dates (~1 call per bill with text)
- Phase 4: For bills with summaries, fetch summary dates (~1 call per bill with summary)
- Phase 5: For bills with text, estimate pages (~1 call, skipped for speed if needed)
- Phase 6: For bills with interesting actions, check committee status (~1 call)
"""

import json
import os
import re
import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

API_KEY = "CONGRESS_API_KEY"
BASE_URL = "https://api.congress.gov/v3"
DATA_DIR = Path(__file__).parent / "data"
RATE_LIMIT_DELAY = 1.0  # Conservative - API key shared with other projects

request_count = 0
last_request_time = 0


def api_get(endpoint, params=None, retries=3):
    """Make a rate-limited API request with retry logic."""
    global request_count, last_request_time

    elapsed = time.time() - last_request_time
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)

    if params is None:
        params = {}
    params["api_key"] = API_KEY
    params["format"] = "json"

    url = f"{BASE_URL}/{endpoint}" if not endpoint.startswith("http") else endpoint

    for attempt in range(retries):
        try:
            if endpoint.startswith("http"):
                sep = "&" if "?" in url else "?"
                full_url = f"{url}{sep}api_key={API_KEY}&format=json"
                resp = requests.get(full_url, timeout=30)
            else:
                resp = requests.get(url, params=params, timeout=30)

            last_request_time = time.time()
            request_count += 1

            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s... (attempt {attempt+1})")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            print(f"  Timeout, retrying... (attempt {attempt+1})")
            time.sleep(5)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5)
            else:
                raise

    return None


def fetch_all_pages(endpoint, key, params=None, max_items=None):
    """Fetch all pages of a paginated API response."""
    if params is None:
        params = {}
    params["limit"] = 250
    items = []
    offset = 0

    while True:
        params["offset"] = offset
        data = api_get(endpoint, params)
        if not data:
            break
        page_items = data.get(key, [])
        items.extend(page_items)
        count = data.get("pagination", {}).get("count", 0)

        if max_items and len(items) >= max_items:
            items = items[:max_items]
            break

        offset += 250
        if offset >= count or not page_items:
            break

        if offset % 1000 == 0:
            print(f"  Fetched {len(items)}/{count}...")

    return items


def estimate_pages_from_text(text_url):
    """Estimate page count from text content."""
    global last_request_time, request_count
    try:
        elapsed = time.time() - last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)

        resp = requests.get(text_url, params={"api_key": API_KEY}, timeout=30)
        last_request_time = time.time()
        request_count += 1

        if resp.status_code == 200:
            text = re.sub(r'<[^>]+>', ' ', resp.text)
            words = len(text.split())
            return max(1, round(words / 275))
    except Exception:
        pass
    return None


def process_bill_detail(congress, bill_type, bill_number, title=""):
    """Fetch and process a single bill's detail."""
    try:
        data = api_get(f"bill/{congress}/{bill_type.lower()}/{bill_number}")
        if not data:
            return None
        detail = data.get("bill", {})
    except Exception as e:
        return None

    text_count = detail.get("textVersions", {}).get("count", 0)
    summary_count = detail.get("summaries", {}).get("count", 0)
    latest = detail.get("latestAction", {})

    result = {
        "congress": congress,
        "type": bill_type.upper(),
        "number": bill_number,
        "title": title or detail.get("title", ""),
        "introduced_date": detail.get("introducedDate"),
        "latest_action_date": latest.get("actionDate"),
        "latest_action_text": latest.get("text"),
        "has_text": text_count > 0,
        "text_count": text_count,
        "has_summary": summary_count > 0,
        "summary_count": summary_count,
        "text_first_date": None,
        "summary_first_date": None,
        "delay_days": None,
        "pending_days": None,
        "estimated_pages": None,
        "reported_by_committee": False,
        "committee_report_date": None,
        "origin_chamber": detail.get("originChamber"),
        "policy_area": detail.get("policyArea", {}).get("name") if detail.get("policyArea") else None,
    }

    # Quick check: does the latest action text mention committee reporting?
    action_text = (latest.get("text") or "").lower()
    report_phrases = ["reported by", "reported favorably", "ordered to be reported",
                      "reported with", "reported without", "committee discharged"]
    if any(p in action_text for p in report_phrases):
        result["reported_by_committee"] = True
        result["committee_report_date"] = latest.get("actionDate")

    return result


def enrich_text_dates(result, congress):
    """Fetch text version dates for a bill."""
    bill_type = result["type"].lower()
    bill_number = result["number"]
    try:
        data = api_get(f"bill/{congress}/{bill_type}/{bill_number}/text")
        if not data:
            return
        text_versions = data.get("textVersions", [])
        dates = [tv["date"] for tv in text_versions if tv.get("date")]
        if dates:
            dates.sort()
            result["text_first_date"] = dates[0]

        # Get text URL for page estimation
        for tv in text_versions:
            for fmt in tv.get("formats", []):
                if fmt.get("type") == "Formatted Text" and fmt.get("url"):
                    result["estimated_pages"] = estimate_pages_from_text(fmt["url"])
                    return
    except Exception:
        pass


def enrich_summary_dates(result, congress):
    """Fetch summary dates for a bill."""
    bill_type = result["type"].lower()
    bill_number = result["number"]
    try:
        data = api_get(f"bill/{congress}/{bill_type}/{bill_number}/summaries")
        if not data:
            return
        summaries = data.get("summaries", [])
        update_dates = []
        for s in summaries:
            ud = s.get("updateDate") or s.get("lastSummaryUpdateDate")
            if ud:
                update_dates.append(ud)
        if update_dates:
            update_dates.sort()
            result["summary_first_date"] = update_dates[0]
    except Exception:
        pass


def enrich_committee_status(result, congress):
    """Check if bill was reported by committee via actions."""
    if result["reported_by_committee"]:
        return  # Already detected from latest action
    bill_type = result["type"].lower()
    bill_number = result["number"]
    try:
        data = api_get(f"bill/{congress}/{bill_type}/{bill_number}/actions")
        if not data:
            return
        actions = data.get("actions", [])
        report_phrases = ["reported by", "reported favorably", "ordered to be reported",
                          "reported with", "reported without", "committee discharged"]
        for action in actions:
            text = action.get("text", "").lower()
            if any(p in text for p in report_phrases):
                result["reported_by_committee"] = True
                result["committee_report_date"] = action.get("actionDate")
                return
    except Exception:
        pass


def calculate_delays(result):
    """Calculate delay or pending days."""
    now = datetime.now(timezone.utc)
    if result["text_first_date"] and result["summary_first_date"]:
        try:
            text_dt = datetime.fromisoformat(result["text_first_date"].replace("Z", "+00:00"))
            summary_dt = datetime.fromisoformat(result["summary_first_date"].replace("Z", "+00:00"))
            result["delay_days"] = max(0, (summary_dt - text_dt).days)
        except Exception:
            pass
    elif result["text_first_date"] and not result["has_summary"]:
        try:
            text_dt = datetime.fromisoformat(result["text_first_date"].replace("Z", "+00:00"))
            result["pending_days"] = max(0, (now - text_dt).days)
        except Exception:
            pass


def compute_stats(bills):
    """Compute summary statistics."""
    with_text = [b for b in bills if b["has_text"]]
    with_summary = [b for b in bills if b["has_summary"]]
    with_text_no_summary = [b for b in bills if b["has_text"] and not b["has_summary"]]
    reported = [b for b in bills if b["reported_by_committee"]]

    delays = sorted([b["delay_days"] for b in bills if b["delay_days"] is not None])
    pending = sorted([b["pending_days"] for b in bills if b["pending_days"] is not None])
    pages = [b["estimated_pages"] for b in bills if b["estimated_pages"]]

    stats = {
        "total_bills": len(bills),
        "bills_with_text": len(with_text),
        "bills_with_summary": len(with_summary),
        "bills_with_text_no_summary": len(with_text_no_summary),
        "bills_reported_by_committee": len(reported),
        "delay": {},
        "pending": {},
        "pages": {},
    }

    if delays:
        stats["delay"] = {
            "count": len(delays),
            "mean": round(sum(delays) / len(delays), 1),
            "median": delays[len(delays) // 2],
            "min": min(delays),
            "max": max(delays),
            "p25": delays[len(delays) // 4],
            "p75": delays[3 * len(delays) // 4],
        }

    if pending:
        stats["pending"] = {
            "count": len(pending),
            "mean": round(sum(pending) / len(pending), 1),
            "median": pending[len(pending) // 2],
            "min": min(pending),
            "max": max(pending),
        }

    if pages:
        stats["pages"] = {
            "total": sum(pages),
            "mean": round(sum(pages) / len(pages), 1),
            "median": sorted(pages)[len(pages) // 2],
            "max": max(pages),
        }

    return stats


def collect_data(congress=119, bill_types=None, max_bills_per_type=None):
    """Main data collection function.

    Strategy: minimize API calls by:
    1. Fetch bill listings (cheap - ~40 calls for full congress)
    2. Fetch details for each bill (1 call each - get text/summary counts)
    3. Only fetch text/summary/action details where needed
    """
    if bill_types is None:
        bill_types = ["hr", "s", "hjres", "sjres", "hconres", "sconres"]

    DATA_DIR.mkdir(exist_ok=True)
    all_bills = []
    checkpoint_file = DATA_DIR / f"checkpoint_{congress}.json"

    # Load checkpoint
    processed_ids = set()
    if checkpoint_file.exists():
        with open(checkpoint_file) as f:
            checkpoint_data = json.load(f)
            all_bills = checkpoint_data.get("bills", [])
            processed_ids = {f"{b['type']}{b['number']}" for b in all_bills}
            print(f"Loaded {len(all_bills)} bills from checkpoint")

    for bill_type in bill_types:
        print(f"\nFetching {bill_type.upper()} bills for {congress}th Congress...")
        bills_list = fetch_all_pages(
            f"bill/{congress}/{bill_type}", "bills",
            max_items=max_bills_per_type
        )
        print(f"  Found {len(bills_list)} {bill_type.upper()} bills")

        for i, bill_info in enumerate(bills_list):
            bill_id = f"{bill_info.get('type', '')}{bill_info.get('number', '')}"
            if bill_id in processed_ids:
                continue

            # Phase 1: Get bill detail (1 API call)
            result = process_bill_detail(
                congress, bill_info.get("type", bill_type),
                bill_info.get("number", ""),
                bill_info.get("title", "")
            )
            if not result:
                continue

            # Phase 2: Get text dates if bill has text (1-2 API calls)
            if result["has_text"]:
                enrich_text_dates(result, congress)

            # Phase 3: Get summary dates if bill has summary (1 API call)
            if result["has_summary"]:
                enrich_summary_dates(result, congress)

            # Phase 4: Check committee status via actions (1 API call)
            # Only check if not already detected from latest action
            if not result["reported_by_committee"]:
                # Heuristic: only check bills that passed committee-related stages
                action_text = (result.get("latest_action_text") or "").lower()
                if any(w in action_text for w in ["committee", "reported", "passed", "agreed", "floor", "enrolled"]):
                    enrich_committee_status(result, congress)

            # Phase 5: Calculate delays
            calculate_delays(result)

            all_bills.append(result)
            processed_ids.add(bill_id)

            # Progress and checkpoint
            if (i + 1) % 25 == 0:
                pct_text = f"{len([b for b in all_bills if b['has_text']])} with text"
                pct_sum = f"{len([b for b in all_bills if b['has_summary']])} with summary"
                print(f"  [{i+1}/{len(bills_list)}] {len(all_bills)} total ({pct_text}, {pct_sum}) [{request_count} API calls]")
                with open(checkpoint_file, 'w') as f:
                    json.dump({"bills": all_bills}, f)

        # Checkpoint after each type
        with open(checkpoint_file, 'w') as f:
            json.dump({"bills": all_bills}, f)
        print(f"  Completed {bill_type.upper()}: {len(all_bills)} total bills")

    # Final output
    stats = compute_stats(all_bills)
    output = {
        "metadata": {
            "congress": congress,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "total_bills_processed": len(all_bills),
            "api_calls_made": request_count,
        },
        "statistics": stats,
        "bills": all_bills,
    }

    output_file = DATA_DIR / f"bills_{congress}.json"
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nDone! {len(all_bills)} bills, {request_count} API calls")
    print(f"Data saved to {output_file}")
    print(f"Stats: {json.dumps(stats, indent=2)}")
    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect bill delay data from congress.gov")
    parser.add_argument("--congress", type=int, default=119)
    parser.add_argument("--types", nargs="+", default=["hr", "s", "hjres", "sjres", "hconres", "sconres"])
    parser.add_argument("--max-per-type", type=int, default=None)
    args = parser.parse_args()
    collect_data(congress=args.congress, bill_types=args.types, max_bills_per_type=args.max_per_type)
