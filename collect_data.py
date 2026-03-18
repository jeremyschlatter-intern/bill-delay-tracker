#!/usr/bin/env python3
"""
Bill Delay Tracker - Data Collection Script

Collects data from the congress.gov API to track how long it takes
for bill summaries to be created after bill text is published.
"""

import json
import os
import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

API_KEY = "CONGRESS_API_KEY"
BASE_URL = "https://api.congress.gov/v3"
DATA_DIR = Path(__file__).parent / "data"
CONGRESS = 119  # Current congress
RATE_LIMIT_DELAY = 0.75  # seconds between requests (~80/min, conservative to avoid 429s)
MAX_WORKERS = 4

# Track request count for rate limiting
request_count = 0
last_request_time = 0


def api_get(endpoint, params=None):
    """Make a rate-limited API request."""
    global request_count, last_request_time

    # Rate limiting
    elapsed = time.time() - last_request_time
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)

    if params is None:
        params = {}
    params["api_key"] = API_KEY
    params["format"] = "json"

    url = f"{BASE_URL}/{endpoint}" if not endpoint.startswith("http") else endpoint
    if endpoint.startswith("http"):
        # Add api_key to full URLs
        if "api_key" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={API_KEY}&format=json"
        resp = requests.get(url, timeout=30)
    else:
        resp = requests.get(url, params=params, timeout=30)

    last_request_time = time.time()
    request_count += 1

    if resp.status_code == 429:
        print("  Rate limited, waiting 60s...")
        time.sleep(60)
        return api_get(endpoint, params)

    resp.raise_for_status()
    return resp.json()


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
        page_items = data.get(key, [])
        items.extend(page_items)
        count = data.get("pagination", {}).get("count", 0)

        if max_items and len(items) >= max_items:
            items = items[:max_items]
            break

        offset += 250
        if offset >= count or not page_items:
            break

        print(f"  Fetched {len(items)}/{count}...")

    return items


def fetch_summaries(congress):
    """Fetch all summaries for a given congress."""
    print(f"Fetching summaries for {congress}th Congress...")
    summaries = fetch_all_pages(f"summaries/{congress}", "summaries")
    print(f"  Found {len(summaries)} summaries")
    return summaries


def fetch_bill_detail(congress, bill_type, bill_number):
    """Fetch detailed bill information."""
    try:
        data = api_get(f"bill/{congress}/{bill_type.lower()}/{bill_number}")
        return data.get("bill", {})
    except Exception as e:
        print(f"  Error fetching {bill_type}{bill_number}: {e}")
        return None


def fetch_bill_text_versions(congress, bill_type, bill_number):
    """Fetch text versions for a bill."""
    try:
        data = api_get(f"bill/{congress}/{bill_type.lower()}/{bill_number}/text")
        return data.get("textVersions", [])
    except Exception as e:
        return []


def fetch_bill_summaries(congress, bill_type, bill_number):
    """Fetch summaries for a specific bill."""
    try:
        data = api_get(f"bill/{congress}/{bill_type.lower()}/{bill_number}/summaries")
        return data.get("summaries", [])
    except Exception as e:
        return []


def fetch_bill_actions(congress, bill_type, bill_number):
    """Fetch actions for a bill to check committee reporting."""
    try:
        data = api_get(f"bill/{congress}/{bill_type.lower()}/{bill_number}/actions")
        return data.get("actions", [])
    except Exception as e:
        return []


def estimate_pages_from_text(text_url):
    """Estimate page count from text content (roughly 250 words per page)."""
    try:
        resp = requests.get(text_url, params={"api_key": API_KEY}, timeout=30)
        if resp.status_code == 200:
            # Strip HTML tags for word count
            import re
            text = re.sub(r'<[^>]+>', ' ', resp.text)
            words = len(text.split())
            return max(1, round(words / 275))  # ~275 words per printed page
    except Exception:
        pass
    return None


def is_reported_by_committee(actions):
    """Check if a bill has been reported by a committee."""
    for action in actions:
        text = action.get("text", "").lower()
        if any(phrase in text for phrase in [
            "reported by",
            "reported favorably",
            "ordered to be reported",
            "reported with",
            "reported without",
            "committee discharged"
        ]):
            return True
    return False


def get_committee_report_date(actions):
    """Get the date a bill was reported by committee."""
    for action in actions:
        text = action.get("text", "").lower()
        if any(phrase in text for phrase in [
            "reported by",
            "reported favorably",
            "ordered to be reported",
            "reported with",
            "reported without",
        ]):
            return action.get("actionDate")
    return None


def process_bill(bill_info, congress):
    """Process a single bill: get text, summary, and action details."""
    bill_type = bill_info.get("type", "").lower()
    bill_number = bill_info.get("number", "")

    # Get bill detail for text/summary counts
    detail = fetch_bill_detail(congress, bill_type, bill_number)
    if not detail:
        return None

    text_count = detail.get("textVersions", {}).get("count", 0)
    summary_count = detail.get("summaries", {}).get("count", 0)

    result = {
        "congress": congress,
        "type": bill_type.upper(),
        "number": bill_number,
        "title": bill_info.get("title", detail.get("title", "")),
        "introduced_date": detail.get("introducedDate"),
        "latest_action_date": None,
        "latest_action_text": None,
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

    # Get latest action
    latest = detail.get("latestAction", {})
    result["latest_action_date"] = latest.get("actionDate")
    result["latest_action_text"] = latest.get("text")

    # Get text version dates
    if text_count > 0:
        text_versions = fetch_bill_text_versions(congress, bill_type, bill_number)
        if text_versions:
            # Find earliest text date
            dates = []
            text_url = None
            for tv in text_versions:
                if tv.get("date"):
                    dates.append(tv["date"])
                # Get URL for page estimation (prefer HTML format)
                if not text_url:
                    for fmt in tv.get("formats", []):
                        if fmt.get("type") == "Formatted Text":
                            text_url = fmt.get("url")
                            break
            if dates:
                dates.sort()
                result["text_first_date"] = dates[0]

            # Estimate pages from first available text
            if text_url:
                result["estimated_pages"] = estimate_pages_from_text(text_url)

    # Get summary dates
    if summary_count > 0:
        summaries = fetch_bill_summaries(congress, bill_type, bill_number)
        if summaries:
            # Find earliest summary creation date
            update_dates = []
            for s in summaries:
                ud = s.get("updateDate") or s.get("lastSummaryUpdateDate")
                if ud:
                    update_dates.append(ud)
            if update_dates:
                update_dates.sort()
                result["summary_first_date"] = update_dates[0]

    # Calculate delay
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

    # Check committee reporting
    actions = fetch_bill_actions(congress, bill_type, bill_number)
    result["reported_by_committee"] = is_reported_by_committee(actions)
    result["committee_report_date"] = get_committee_report_date(actions)

    return result


def collect_data(congress=119, bill_types=None, max_bills_per_type=None):
    """Main data collection function."""
    if bill_types is None:
        bill_types = ["hr", "s", "hres", "sres", "hjres", "sjres", "hconres", "sconres"]

    DATA_DIR.mkdir(exist_ok=True)
    all_bills = []
    checkpoint_file = DATA_DIR / f"checkpoint_{congress}.json"

    # Load checkpoint if exists
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
            f"bill/{congress}/{bill_type}",
            "bills",
            max_items=max_bills_per_type
        )
        print(f"  Found {len(bills_list)} {bill_type.upper()} bills")

        for i, bill_info in enumerate(bills_list):
            bill_id = f"{bill_info.get('type', '')}{bill_info.get('number', '')}"
            if bill_id in processed_ids:
                continue

            result = process_bill(bill_info, congress)
            if result:
                all_bills.append(result)
                processed_ids.add(bill_id)

            # Progress update and checkpoint every 50 bills
            if (i + 1) % 50 == 0:
                print(f"  Processed {i+1}/{len(bills_list)} {bill_type.upper()} bills "
                      f"({len(all_bills)} total, {request_count} API calls)")
                # Save checkpoint
                with open(checkpoint_file, 'w') as f:
                    json.dump({"bills": all_bills}, f)

        # Save checkpoint after each type
        with open(checkpoint_file, 'w') as f:
            json.dump({"bills": all_bills}, f)

    # Compute statistics
    stats = compute_stats(all_bills)

    # Save final data
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

    print(f"\nDone! Processed {len(all_bills)} bills with {request_count} API calls")
    print(f"Data saved to {output_file}")

    return output


def compute_stats(bills):
    """Compute summary statistics from collected bill data."""
    with_text = [b for b in bills if b["has_text"]]
    with_summary = [b for b in bills if b["has_summary"]]
    with_text_no_summary = [b for b in bills if b["has_text"] and not b["has_summary"]]
    with_delay = [b for b in bills if b["delay_days"] is not None]
    with_pending = [b for b in bills if b["pending_days"] is not None]
    reported = [b for b in bills if b["reported_by_committee"]]

    delays = [b["delay_days"] for b in with_delay]
    pending = [b["pending_days"] for b in with_pending]
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
        delays.sort()
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
        pending.sort()
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect bill delay data from congress.gov")
    parser.add_argument("--congress", type=int, default=119, help="Congress number")
    parser.add_argument("--types", nargs="+", default=["hr", "s"],
                        help="Bill types to collect")
    parser.add_argument("--max-per-type", type=int, default=None,
                        help="Max bills per type (for testing)")
    args = parser.parse_args()

    collect_data(
        congress=args.congress,
        bill_types=args.types,
        max_bills_per_type=args.max_per_type,
    )
