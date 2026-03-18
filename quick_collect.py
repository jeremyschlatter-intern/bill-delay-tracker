#!/usr/bin/env python3
"""
Quick data collection - fetches all bill listings and summaries with minimal API calls.
Then enriches bills that have summaries with text dates for delay calculation.

Phase 1: Get all summaries (1 API call) - gives us delay data
Phase 2: Get all bill listings (40 calls) - gives us the full bill catalog
Phase 3: For summarized bills, get text dates (32 calls) - gives us actual delays
Phase 4: For a sample of other bills, get details (minimal calls)
"""

import json
import os
import re
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

API_KEY = "CONGRESS_API_KEY"
BASE_URL = "https://api.congress.gov/v3"
DATA_DIR = Path(__file__).parent / "data"
RATE_LIMIT_DELAY = 3.0  # Very conservative - sharing API key with other projects

request_count = 0
last_request_time = 0


def api_get(endpoint, params=None):
    global request_count, last_request_time
    elapsed = time.time() - last_request_time
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)

    if params is None:
        params = {}
    params["api_key"] = API_KEY
    params["format"] = "json"
    url = f"{BASE_URL}/{endpoint}"

    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=30)
            last_request_time = time.time()
            request_count += 1
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            time.sleep(5)
        except Exception as e:
            if attempt == 2:
                print(f"  Error: {e}")
                return None
            time.sleep(5)
    return None


def fetch_all_pages(endpoint, key, params=None):
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
        offset += 250
        if offset >= count or not page_items:
            break
        if offset % 1000 == 0:
            print(f"    {len(items)}/{count}...")
    return items


def estimate_pages(text_url):
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
            return max(1, round(len(text.split()) / 275))
    except Exception:
        pass
    return None


def main():
    DATA_DIR.mkdir(exist_ok=True)
    congress = 119
    now = datetime.now(timezone.utc)

    # ============================================
    # PHASE 1: Get all summaries
    # ============================================
    print("Phase 1: Fetching all summaries...")
    all_summaries = fetch_all_pages(f"summaries/{congress}", "summaries")
    print(f"  Found {len(all_summaries)} summaries")

    # Index summaries by bill
    summary_index = {}
    for s in all_summaries:
        bill = s.get("bill", {})
        key = f"{bill.get('type', '')}{bill.get('number', '')}"
        ud = s.get("updateDate") or s.get("lastSummaryUpdateDate")
        if key not in summary_index or (ud and ud < summary_index[key]["update_date"]):
            summary_index[key] = {
                "update_date": ud,
                "action_date": s.get("actionDate"),
                "action_desc": s.get("actionDesc"),
            }

    print(f"  Indexed {len(summary_index)} unique bills with summaries")

    # ============================================
    # PHASE 2: Get all bill listings
    # ============================================
    print("\nPhase 2: Fetching bill listings...")
    bill_types = ["hr", "s", "hjres", "sjres", "hconres", "sconres"]
    all_bill_listings = []

    for bt in bill_types:
        print(f"  Fetching {bt.upper()}...")
        listings = fetch_all_pages(f"bill/{congress}/{bt}", "bills")
        print(f"    Got {len(listings)} {bt.upper()} bills")
        all_bill_listings.extend(listings)

    print(f"  Total: {len(all_bill_listings)} bills across all types")

    # ============================================
    # PHASE 3: Build bill records
    # ============================================
    print("\nPhase 3: Building bill records...")
    bills = []

    for listing in all_bill_listings:
        bill_type = listing.get("type", "")
        bill_number = listing.get("number", "")
        bill_key = f"{bill_type}{bill_number}"

        record = {
            "congress": congress,
            "type": bill_type,
            "number": bill_number,
            "title": listing.get("title", ""),
            "introduced_date": None,
            "latest_action_date": listing.get("latestAction", {}).get("actionDate"),
            "latest_action_text": listing.get("latestAction", {}).get("text"),
            "has_text": True,  # Will refine below
            "text_count": 0,
            "has_summary": bill_key in summary_index,
            "summary_count": 1 if bill_key in summary_index else 0,
            "text_first_date": None,
            "summary_first_date": summary_index[bill_key]["update_date"] if bill_key in summary_index else None,
            "delay_days": None,
            "pending_days": None,
            "estimated_pages": None,
            "reported_by_committee": False,
            "committee_report_date": None,
            "origin_chamber": listing.get("originChamber"),
            "policy_area": None,
            "update_date": listing.get("updateDate"),
            "update_date_including_text": listing.get("updateDateIncludingText"),
        }

        # Check for committee reporting in latest action
        action_text = (record["latest_action_text"] or "").lower()
        report_phrases = ["reported by", "reported favorably", "ordered to be reported",
                          "reported with", "reported without", "committee discharged",
                          "placed on", "passed house", "passed senate",
                          "resolving differences", "became public law", "signed by president"]
        if any(p in action_text for p in report_phrases):
            record["reported_by_committee"] = True
            record["committee_report_date"] = record["latest_action_date"]

        bills.append(record)

    # ============================================
    # PHASE 4: Enrich summarized bills with text dates + pages
    # ============================================
    summarized_bills = [b for b in bills if b["has_summary"]]
    print(f"\nPhase 4: Enriching {len(summarized_bills)} summarized bills with text dates...")

    for i, b in enumerate(summarized_bills):
        bt = b["type"].lower()
        bn = b["number"]
        try:
            data = api_get(f"bill/{congress}/{bt}/{bn}/text")
            if data:
                tvs = data.get("textVersions", [])
                b["text_count"] = len(tvs)
                b["has_text"] = len(tvs) > 0
                dates = [tv["date"] for tv in tvs if tv.get("date")]
                if dates:
                    dates.sort()
                    b["text_first_date"] = dates[0]

                # Estimate pages from first text version
                for tv in tvs:
                    for fmt in tv.get("formats", []):
                        if fmt.get("type") == "Formatted Text" and fmt.get("url"):
                            b["estimated_pages"] = estimate_pages(fmt["url"])
                            break
                    if b["estimated_pages"]:
                        break

                # Calculate delay
                if b["text_first_date"] and b["summary_first_date"]:
                    try:
                        text_dt = datetime.fromisoformat(b["text_first_date"].replace("Z", "+00:00"))
                        sum_dt = datetime.fromisoformat(b["summary_first_date"].replace("Z", "+00:00"))
                        b["delay_days"] = max(0, (sum_dt - text_dt).days)
                    except Exception:
                        pass
        except Exception as e:
            print(f"    Error enriching {bt.upper()} {bn}: {e}")

        if (i + 1) % 10 == 0:
            print(f"    Enriched {i+1}/{len(summarized_bills)} [{request_count} API calls]")

    # ============================================
    # PHASE 5: Enrich a sample of non-summarized bills
    # ============================================
    non_summarized = [b for b in bills if not b["has_summary"]]
    # Sample: get details for a subset to verify text availability and get pages
    sample_size = min(500, len(non_summarized))
    sample = non_summarized[:sample_size]  # Most recent by latestAction

    print(f"\nPhase 5: Enriching {sample_size} non-summarized bills...")
    for i, b in enumerate(sample):
        bt = b["type"].lower()
        bn = b["number"]
        try:
            # Get detail to confirm text availability
            data = api_get(f"bill/{congress}/{bt}/{bn}")
            if data:
                detail = data.get("bill", {})
                text_count = detail.get("textVersions", {}).get("count", 0)
                b["has_text"] = text_count > 0
                b["text_count"] = text_count
                b["introduced_date"] = detail.get("introducedDate")
                b["policy_area"] = detail.get("policyArea", {}).get("name") if detail.get("policyArea") else None

                # Get text dates for bills with text
                if text_count > 0:
                    text_data = api_get(f"bill/{congress}/{bt}/{bn}/text")
                    if text_data:
                        tvs = text_data.get("textVersions", [])
                        dates = [tv["date"] for tv in tvs if tv.get("date")]
                        if dates:
                            dates.sort()
                            b["text_first_date"] = dates[0]
                            # Calculate pending days
                            try:
                                text_dt = datetime.fromisoformat(b["text_first_date"].replace("Z", "+00:00"))
                                b["pending_days"] = max(0, (now - text_dt).days)
                            except Exception:
                                pass

                        # Page estimation for a subset
                        if i < 200:  # Only estimate pages for first 200
                            for tv in tvs:
                                for fmt in tv.get("formats", []):
                                    if fmt.get("type") == "Formatted Text" and fmt.get("url"):
                                        b["estimated_pages"] = estimate_pages(fmt["url"])
                                        break
                                if b["estimated_pages"]:
                                    break
                else:
                    b["has_text"] = False

                # Check committee status from actions
                if not b["reported_by_committee"]:
                    actions_data = api_get(f"bill/{congress}/{bt}/{bn}/actions")
                    if actions_data:
                        for action in actions_data.get("actions", []):
                            act_text = action.get("text", "").lower()
                            if any(p in act_text for p in ["reported by", "ordered to be reported",
                                                           "reported with", "reported without"]):
                                b["reported_by_committee"] = True
                                b["committee_report_date"] = action.get("actionDate")
                                break
        except Exception as e:
            print(f"    Error: {e}")

        if (i + 1) % 25 == 0:
            pct = f"{len([x for x in sample[:i+1] if x['has_text']])} with text"
            print(f"    [{i+1}/{sample_size}] {pct} [{request_count} API calls]")

        # Checkpoint every 100 bills
        if (i + 1) % 100 == 0:
            with open(DATA_DIR / f"checkpoint_{congress}.json", 'w') as f:
                json.dump({"bills": bills}, f)

    # For remaining non-sampled bills, set conservative defaults
    for b in non_summarized[sample_size:]:
        # We know these bills exist but haven't checked text
        # updateDateIncludingText presence suggests text exists
        if b.get("update_date_including_text"):
            b["has_text"] = True
            # Use introduced date as approximate text date
            if not b["text_first_date"]:
                # Estimate pending days from latest action date as proxy
                if b.get("latest_action_date"):
                    try:
                        proxy_dt = datetime.fromisoformat(b["latest_action_date"] + "T00:00:00+00:00")
                        b["pending_days"] = max(0, (now - proxy_dt).days)
                    except Exception:
                        pass

    # ============================================
    # FINALIZE
    # ============================================
    # Clean up temp fields
    for b in bills:
        b.pop("update_date", None)
        b.pop("update_date_including_text", None)

    stats = compute_stats(bills)
    output = {
        "metadata": {
            "congress": congress,
            "collected_at": now.isoformat(),
            "total_bills_processed": len(bills),
            "api_calls_made": request_count,
            "note": f"Full bill catalog for {congress}th Congress. Detailed delay data available for {len(summarized_bills)} summarized bills and {sample_size} sampled non-summarized bills.",
        },
        "statistics": stats,
        "bills": bills,
    }

    output_file = DATA_DIR / f"bills_{congress}.json"
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nDone! {len(bills)} bills, {request_count} API calls")
    print(f"Stats: {json.dumps(stats, indent=2)}")
    return output


def compute_stats(bills):
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


if __name__ == "__main__":
    main()
