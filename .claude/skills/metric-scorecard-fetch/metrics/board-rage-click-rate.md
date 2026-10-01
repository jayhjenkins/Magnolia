---
slug: board-rage-click-rate
source: auto
mcp: pendo
---
# Frustrated Session Rate — Board Members (Rage-Click Rate per 1,000 Board WAU)
Long-lived, self-contained scorecard definition. Pendo subId — resolve via
`python3 scripts/profile_lib.py --pendo-subid`, never embed a literal; appId=5961191088521216 (web).
Self-contained: computes its OWN board WAU denominator (no depends_on; does NOT use home-wau).
Segment "Home | Board Members" segmentId="ruTWzJHTx86HCgbOfdAP80T85UQ" (canonical board-member
definition — always use the segmentId, not a metadata.isboardmember filter). Windows inclusive of as_of.

## Scope (METHODOLOGY — changed Sep 2026, in force since the week of 2026-09-05)
Numerator counts rage clicks on the **8 board action pages ONLY**, within the board segment.
It does NOT count all Home pages (Dashboard, Documents, Pre-Login, Calendar, Directory, etc.).
This is a different, narrower measure than `rage-click-rate` (all residents, all pages) — do not copy
that metric's "sum everything" rule here.

Board action pages — match by pageId (names get renamed in Pendo; the label is display-only):

| Label (store in raw.pages) | Pendo pageId | Current Pendo name |
|---|---|---|
| Invoices | U8eoH_lwxZ2V3aRm7yKtu8gXsSg | Home \| Board Pages \| Invoices |
| Homeowners | LL7ySUWID4liPU4EQfFgGrDqb5A | Home \| Board Pages \| Homeowners |
| ARC Page | 6Xa2xJApxCDpTU4oiJFHETo9LoY | Board \| ARCs \| ARC Page |
| Violations | -J3MkthWo14XU3DqweyoINlEERk | Home \| Board Pages \| Violations |
| Work Orders | yQZZ09U51xXmbn8QmUZn1als6RQ | Home \| Board Pages \| Work Orders |
| Board Tasks | G3DFCGBnI5S4YCwY0rST4EXBbs8 | Home \| Board Pages \| To-Do List |
| Collections | sMMy-j5QL-MBdm-MOc7UOeqguIc | Home \| Board Pages \| Collections |
| Bank Accounts | qK3Slxuex13kF0RL0Tl09gMWves | Home \| Board Pages \| Bank Accounts |

Deliberately EXCLUDED (decided 2026-09-28; revisit only with the operator): `Board | ARCs | Specific ARC`
(gFHeHr4eMvV2Xu1hALWqMwKYHgA), `Home | Board Pages | Reports`, `Home | Board Pages | Inspections`,
and every non-"Board Pages" page. The set matches what was reported 2026-09-05 through 2026-09-19;
changing it breaks the time series, so any change must be logged as a methodology change (see below).

## Calls
1) Rage (trailing 7d) — Pendo MCP `aggregateEntityUsage`:
   subId={subId}, appId=5961191088521216, entityType="page",
   dateRange={type:"absolute", startDate:<as_of-6d>, endDate:<as_of>},
   segmentPipeline='{"id":"ruTWzJHTx86HCgbOfdAP80T85UQ"}', sortBy="totalRageClickCount", limit=40.
   Read `totalRageClickCount` from the ROWS whose `entityId` is in the table above (the response
   `summary` covers every page — do NOT use it). Sum those 8 rows. A page missing from the rows = 0.
   (If you pass `items=[{entityType:"page", ids:[...]}]` to scope the call, that works too but has timed
   out once; the unscoped ranked call above is the reliable path.)
   total_rage = sum of the 8 rows. Record each page's count in raw.pages under its Label.

2) Board WAU (4-week rolling average) — Pendo MCP `appUsageTimeSeries`:
   subId={subId}, appId=5961191088521216, period="weekly",
   dateRange={type:"absolute", startDate:<as_of-27d, the Sunday>, endDate:<as_of, the Saturday>},
   segmentPipeline='{"id":"ruTWzJHTx86HCgbOfdAP80T85UQ"}'.
   Read `totalNumVisitors` per bucket. Exactly 4 buckets. NOTE: buckets can come back out of date
   order — sort by `bucket`. board_wau = round(sum(4 counts) / 4).

value = round(total_rage / (board_wau / 1000), 1)
raw = {"rage": total_rage, "board_wau_4wk": board_wau, "wau_buckets": [<4 ints, date order>],
       "scope": "board-action-pages-only", "pages": {<Label>: <count>, ...8 entries}}

Sanity: ~40–130 (action-page history: 125.7 GA-week spike 09-05, 70.4, 62.6, 73.8). Target <= 62.
Invoices is typically ~75% of the total (pagination behavior) — a shift in that share is worth noting.

## Guardrails (read before returning)
- `raw.scope` MUST be "board-action-pages-only" and `raw.pages` MUST have exactly the 8 labels above.
  If you cannot produce both, return status "error" — never fall back to an all-pages number.
- Compare to the prior week's value. If |change| > 2x (or < 0.5x), do NOT silently record it:
  re-check that you summed the 8 pages, not the segment total (the segment-total/all-pages figure runs
  ~2.5x higher — e.g. 171 vs 74 in the week of 2026-09-26), then explain the move in notes.
- Any change to the page list, segment, or denominator is a METHODOLOGY CHANGE: prefix notes with
  "METHODOLOGY CHANGE:", store the prior-method value as raw.prior_<method>_rate, and update this file
  and the registry definition in the same commit.

Return ONLY this JSON:
{"slug":"board-rage-click-rate","value":<float|null>,"raw":<obj>,"status":"ok|error|stale","notes":"<short>"}
