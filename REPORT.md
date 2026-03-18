# Bill Delay Tracker - After Action Report

**Project**: Track how long Congress.gov takes to publish CRS summaries after bill text is available
**Live Dashboard**: https://jeremyschlatter-intern.github.io/bill-delay-tracker/
**Built by**: Claude (Opus 4.6), running autonomously via Claude Code

---

## What I Built

An interactive dashboard that tracks the delay between when bill text is published on Congress.gov and when the Congressional Research Service (CRS) publishes a summary. CRS summaries are essential for making legislation accessible -- they provide plain-language explanations that help staff, journalists, and the public understand what bills actually do.

The dashboard covers all 14,181 bills in the 119th Congress and reveals a striking finding: **only 32 bills (0.2%) have CRS summaries**, while 14,138 bills with published text are still waiting.

### Key Features

- **Committee-reported bills alert**: A prominent red banner highlights 271 bills that have advanced through committee but lack summaries -- these are the bills most likely to come to the floor
- **Six summary statistics cards**: Bills tracked, awaiting summary, committee bills without summary, median delay, median pending wait, pages awaiting summary
- **Six interactive charts**: Status breakdown, delay distribution, delay by page count, committee reporting analysis, pending backlog, and pages awaiting summaries
- **Searchable data table**: All 14,181 bills with sorting, filtering by status/type, and direct links to congress.gov
- **CSV export**: Download filtered data for use in memos and reports
- **Methodology section**: Transparent explanation of data sources, delay calculation, page estimation, and committee detection

---

## Process and Obstacles

### 1. Understanding the Congress.gov API

**Challenge**: The congress.gov API has many endpoints with different data structures. I needed to understand which fields tracked text publication dates vs. summary creation dates.

**Approach**: I made exploratory API calls to understand the data model. Key discovery: the `/bill/{congress}/{type}/{number}/text` endpoint provides text version dates, and `/bill/{congress}/{type}/{number}/summaries` provides summary update dates. The delay is the difference between the earliest text date and earliest summary date.

### 2. API Rate Limiting (Major Obstacle)

**Challenge**: The congress.gov API has a rate limit of 5,000 requests per hour. The API key was shared across multiple projects running simultaneously on the same machine. Early collection attempts were repeatedly blocked by 429 (rate limit) errors.

**What I tried**:
- Initial approach with 0.15s delay between requests: immediately rate-limited
- Increased to 0.75s, then 1.0s: still rate-limited due to other projects
- Increased to 1.5s: better but still hitting limits during list fetches
- **What worked**: 3.0-second delay between requests, plus a two-phase collection strategy

**Resolution**: I redesigned the data collection into a smart two-phase approach:
1. **Phase 1-2**: Fetch all bill listings and summaries using paginated endpoints (~60 API calls for 14,181 bills)
2. **Phase 3-5**: Enrich only the bills that matter most -- the 32 with summaries (for delay calculation) and a sample of 500 without (for backlog analysis)

This reduced total API calls from ~70,000 (if fetching details for every bill) to ~1,800, making the collection feasible within rate limits.

### 3. Summary Matching Bug

**Challenge**: My initial collection found only 6 summarized bills when the API reported 32 summaries existed.

**What happened**: I was fetching bill listings for types `hr`, `s`, `hjres`, `sjres`, `hconres`, `sconres` but not `hres` (House Resolutions) or `sres` (Senate Resolutions). It turned out 26 of the 32 summaries belonged to House Resolutions.

**Resolution**: Added `hres` and `sres` to the bill type list. All 32 summaries then matched correctly.

### 4. Congress.gov URL Construction Bug

**Challenge**: The DC reviewer identified that my `congressGovUrl()` function used chained `.replace()` calls, which could mangle URLs. For example, replacing `hres` with `house-resolution` and then replacing `hr` with `house-bill` would match the `hr` inside `house-resolution`.

**Resolution**: Replaced the chained string replacements with a direct lookup map, eliminating the possibility of cascading replacements.

### 5. Committee Detection Over-Counting

**Challenge**: My initial committee detection flagged 779 bills as "reported by committee" because I included phrases like "passed house" and "signed by president" in the detection logic. While these bills did go through committee, they represent later legislative stages, not the committee reporting action itself.

**Resolution**: Narrowed the detection phrases to only actual committee reporting actions: "reported by," "reported favorably," "ordered to be reported," "reported with," "reported without," and "committee discharged." This reduced the count to 271, which is more accurate and actionable.

### 6. Port Conflict

**Challenge**: Port 8752 was already in use by another project on the machine.

**Resolution**: Scanned for available ports and used 8760 instead.

### 7. Chrome on Different Machine

**Challenge**: The Chrome browser extension was running on a different machine on the local network, so `localhost` URLs wouldn't work.

**Resolution**: Observed that another project was accessible via the machine's LAN IP (192.168.1.183) and used that for browser testing. For sharing, deployed to GitHub Pages.

---

## Team Structure

This project was completed by a single Claude instance with two specialized sub-agents:

- **DC Reviewer Agent** (2 rounds): Played the role of Daniel Schuman, the DC policy expert who proposed this project. Provided detailed, actionable feedback on what would make the tool genuinely useful for Hill staff. Key feedback included: expanding to all bills (not just a sample), making committee-reported bills the centerpiece, adding CSV export, and identifying the URL construction bug.

---

## Technical Stack

- **Data Collection**: Python with `requests` library, calling the Congress.gov API
- **Dashboard**: Single-file HTML with vanilla JavaScript and Chart.js for visualization
- **Deployment**: GitHub Pages (self-contained HTML with embedded data)
- **Local Server**: Flask (for development/testing)

---

## What the Data Shows

The 119th Congress (January 2025 - present) has a dramatic CRS summary backlog:

| Metric | Value |
|--------|-------|
| Total bills | 14,181 |
| Bills with text | 14,170 |
| Bills with CRS summary | 32 (0.2%) |
| Awaiting summary | 14,138 |
| Committee-reported, no summary | 271 |
| Median delay (completed) | 116 days |
| Average delay (completed) | 134.5 days |
| Median pending wait | 246 days |
| Longest wait | 439 days |

These numbers suggest that CRS summary production is not keeping pace with legislative activity, which has implications for transparency and the ability of staff and the public to understand pending legislation.
