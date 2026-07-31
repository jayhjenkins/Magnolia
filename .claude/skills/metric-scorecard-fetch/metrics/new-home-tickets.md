---
slug: new-home-tickets
source: auto
mcp: databricks
---
# New Home Tickets (weekly absolute count)
Long-lived, self-contained scorecard definition. Databricks catalog — resolve via
`python3 scripts/profile_lib.py --databricks-catalog`, never embed a literal; READ-ONLY.
Reports the RAW weekly count of new Home support tickets — intentionally NOT normalized per WAU.
The absolute number is the intuitive scorecard signal; week-to-week noise is expected and fine.
This metric does NOT depend on home-wau (there is no division).

Count Home tickets created in the trailing 7 days inclusive of as_of:
  SELECT COUNT(*) AS c FROM {catalog}.zendesk.ticket
  WHERE custom_product_field = 'home__'
    AND created_at >= DATE_SUB(DATE('<as_of>'), 6)
    AND created_at <  DATE_ADD(DATE('<as_of>'), 1)
value = c   # integer count
raw = c     # same integer
Caveat to record in notes: most tickets are untagged (NULL product field), so this is a
tagging-dependent floor, not a complete Home ticket count. Sanity: ~50–120 per week.

Return ONLY this JSON:
{"slug":"new-home-tickets","value":<int|null>,"raw":<int>,"status":"ok|error|stale","notes":"<short>"}
