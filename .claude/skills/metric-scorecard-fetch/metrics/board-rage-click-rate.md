---
slug: board-rage-click-rate
source: auto
mcp: pendo
---
# Frustrated Session Rate — Board Members (Rage-Click Rate per 1,000 WAU)
Long-lived, self-contained scorecard definition. Pendo subId — resolve via
`python3 scripts/profile_lib.py --pendo-subid`, never embed a literal; appId=5961191088521216 (web).
Board-member variant of `rage-click-rate` — same calculation, restricted to the board-member segment.
Self-contained: it computes its OWN board WAU denominator (no depends_on; does NOT use home-wau).
segment "Home | Board Members" segmentId="ruTWzJHTx86HCgbOfdAP80T85UQ" (canonical board-member
definition — always use the segmentId, not a metadata.isboardmember filter). Windows inclusive of as_of.

Two calls:
1) Rage (trailing 7d), entityType="page":
   activityQuery(subId={subId}, appId=5961191088521216, entityType="page",
     group=["pageId"], count=false, frustrationMetrics=true, segmentId="ruTWzJHTx86HCgbOfdAP80T85UQ",
     period="dayRange", dateRange={range:"custom", startDate:<as_of-6d>, endDate:<as_of>}, limit=1000)
   total_rage = sum of rageClickCount across ALL returned page rows (includes the Pre-Login page and
   any duplicate pageIds — sum everything, exactly like the all-resident metric).

2) Board WAU (4-week rolling average), entityType="visitor":
   activityQuery(subId={subId}, appId=5961191088521216, entityType="visitor",
     group=["visitorId"], count=true, segmentId="ruTWzJHTx86HCgbOfdAP80T85UQ", period="weekly",
     dateRange={range:"custom", startDate:<as_of-27d, the Sunday>, endDate:<as_of, the Saturday>})
   Returns exactly 4 weekly buckets in results[].rows[].count. board_wau = round(sum(4 counts) / 4).
   (Same denominator construction as home-wau, board segment only. Must be exactly 4 buckets.)

value = round(total_rage / (board_wau / 1000), 1)
raw = {"rage": total_rage, "board_wau_4wk": board_wau, "wau_buckets": [<4 ints>]}
Sanity: ~40–110 (runs higher than the all-resident row; flag recent elevation toward ~140 driven by
the board Invoices + Homeowners pages). Windows inclusive of as_of.

Return ONLY this JSON:
{"slug":"board-rage-click-rate","value":<float|null>,"raw":<obj>,"status":"ok|error|stale","notes":"<short>"}
