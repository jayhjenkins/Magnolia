---
slug: home-qav
source: auto
mcp: jira
---
# Home Quality-Adjusted Velocity (bi-weekly, 2-week escape lag)
Long-lived, self-contained scorecard definition. Jira cloudId `vantaca.atlassian.net`
(Vantaca Jira instance) — this is the first Jira-sourced metric in this scorecard; no
profile_lib resolver exists for it yet, use the literal cloudId above.

QAV = Completed HXP tickets − Net-new escape bugs, for the most recently **finished**
bi-weekly Home release period. "Finished" means that period's own 2-week escape-collection
window has itself fully elapsed as of `as_of` — this metric's value therefore only changes
once per ~14 days, not every scorecard week. Reporting the same value across consecutive
weeks between period boundaries is correct behavior, not staleness.

Work Item Defects (found and fixed during development, before ship) are NOT escapes —
they reflect in-process quality and are excluded entirely. Only Bug + Regression Defect
issues count as escapes, and only Severity 1–3 (Severity 4 / cosmetic issues excluded so a
wave of minor tickets can't swamp the signal).

Historical baseline (pre-improvement, Mar–May 2025, same methodology, 6 bi-weekly periods):
average QAV = 22/period. See `datasets/product/home-release-shipping-mix.html` (Quality
velocity tab) for the full historical chart and per-period breakdown this scorecard row
summarizes into a single current value.

## Determine the period (do this first, before any Jira query)
Anchor: period 0 = 2026-01-01 through 2026-01-14 (14-day periods, no gaps, matching Home's
release cadence). Substitute the literal `as_of` string (e.g. `2026-09-05`) for `{{AS_OF}}`
below — do NOT split it into separate year/month/day integers, Python 3 rejects zero-padded
int literals like `d.date(2026,09,05)` as a syntax error:

```
python3 -c "
import datetime as d
anchor = d.date(2026,1,1)
as_of = d.date.fromisoformat('{{AS_OF}}')
days = (as_of - anchor).days
n = (days - 27) // 14
if n < 0:
    print('NO_FINISHED_PERIOD')
else:
    ps = anchor + d.timedelta(days=14*n)
    pe = ps + d.timedelta(days=13)
    ew = pe + d.timedelta(days=14)
    print('period_start', ps.isoformat())
    print('period_end', pe.isoformat())
    print('escape_window_end', ew.isoformat())
    print('completed_end_excl', (pe + d.timedelta(days=1)).isoformat())
    print('escape_end_excl', (ew + d.timedelta(days=1)).isoformat())
"
```

If this prints `NO_FINISHED_PERIOD` (only possible before mid-Feb 2026), return
`{"slug":"home-qav","value":null,"raw":null,"status":"stale","notes":"no period has finished yet"}`.

Otherwise this gives five values, all pre-computed — do not do any further date arithmetic,
just paste them into the queries below verbatim: `period_start`, `period_end`,
`escape_window_end` (for `notes` only), `completed_end_excl`, `escape_end_excl`.

## Query 1 — Completed
Via `searchJiraIssuesUsingJql`, cloudId `vantaca.atlassian.net`, `searchResultMode: "count"`:

```
project = VNT AND component = "Vantaca HXP" AND resolution = Done
  AND resolved >= "{period_start}" AND resolved < "{completed_end_excl}"
```
`completed` = `totalCount`.

## Query 2 — Escape bugs (Severity 1–3 only)
Severity lives in custom field `customfield_10269` (values: "Severity 1".."Severity 4";
verified against live Jira data 2026-09-03 — option ids 10641="Severity 2", 10642="Severity 3",
10643="Severity 4"). Many Bug/Regression-Defect tickets have this field unset; unset counts
as non-escape (excluded), same as Severity 4.

```
project = VNT AND component = "Vantaca HXP"
  AND issuetype in (Bug, "Regression Defect")
  AND cf[10269] in ("Severity 1","Severity 2","Severity 3")
  AND created >= "{period_start}" AND created < "{escape_end_excl}"
```
`escapes` = `totalCount`. Note the escapes window extends 14 days past `period_end` (through
`escape_window_end`), not just to `period_end` — that extra window is exactly what makes this
period "finished" and safe to report.

## Value
```
value = completed - escapes   # can be negative
raw = {"completed": completed, "escapes": escapes, "period_start": period_start, "period_end": period_end}
```
Sanity: historically -12 to +108 per period (wide range; 2026 has trended sharply downward
from a mid-Feb peak of ~108 as escapes climbed — do not treat a low/negative value as an
error, it may be a real regression). Baseline for comparison = 22 (pre-2026 average).

Return ONLY this JSON:
{"slug":"home-qav","value":<int|null>,"raw":<object|null>,"status":"ok|error|stale","notes":"<period_start>–<period_end>, escapes through <escape_window_end>"}
