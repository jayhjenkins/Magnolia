---
slug: new-home-tickets
source: auto
mcp: databricks
---
# New Home Tickets (weekly absolute count)
Long-lived, self-contained scorecard definition. Databricks catalog — resolve via
`python3 scripts/profile_lib.py --databricks-catalog`, never embed a literal; READ-ONLY.
Reports the RAW weekly count of Home/Portal support tickets — intentionally NOT normalized per WAU.
The absolute number is the intuitive scorecard signal; week-to-week noise is expected and fine.
This metric does NOT depend on home-wau (there is no division).

## Classification approach (Aug 2026)
Uses deterministic subject-line classification across 6 Zendesk SME support groups rather than
the `custom_product_field` tag, which undercounted due to inconsistent agent tagging (only 35-55%
of real Home/Portal volume was tagged). The classification rules were iteratively vetted against
~120 real ticket subjects; see the product-ticket-categorization skill for the full rationale.

## Query
Determine UTC boundaries for the as_of week (Sun-Sat in Eastern Time):
- as_of is a Saturday. The week runs Sunday (as_of - 6) 00:00 ET through end of Saturday (as_of).
- EDT (mid-Mar to early Nov): ET + 4h = UTC. Sunday 00:00 ET = Sunday 04:00 UTC.
- EST (rest of year): ET + 5h = UTC. Sunday 00:00 ET = Sunday 05:00 UTC.
- Start UTC = (as_of - 6 days) at 04:00 (EDT) or 05:00 (EST)
- End UTC = (as_of + 1 day) at 04:00 (EDT) or 05:00 (EST)

Substitute {catalog}, {{START_UTC}}, {{END_UTC}} and run via execute_sql_read_only:

  WITH scoped AS (
    SELECT t.id, g.name as group_name, LOWER(COALESCE(t.subject,'')) as subj
    FROM {catalog}.zendesk.ticket t
    JOIN {catalog}.zendesk.group g ON t.group_id = g.id
    WHERE t.created_at >= '{{START_UTC}}'
      AND t.created_at < '{{END_UTC}}'
      AND t.group_id IN (34116658941851, 34116597628955, 45977470776219, 34116552104475, 43453857098395, 34116672229403)
      AND t.id NOT IN (
        SELECT ticket_id FROM {catalog}.zendesk.ticket_tag WHERE tag IN ('zoom_created_ticket','zoom_phone_call')
      )
      AND t.subject NOT LIKE 'Incoming call from%Auto Created%'
      AND LOWER(COALESCE(t.subject,'')) NOT LIKE '%hoai%'
  ),
  classified AS (
    SELECT id,
      CASE
        WHEN group_name = 'Vantaca Pay - MC Support' THEN 'Vantaca Pay'
        WHEN group_name = 'Association Management SME Team' THEN
          CASE
            WHEN subj RLIKE 'inspection|violation|work order|\\barc\\b|architectural review|mobile app|offline|common area' THEN 'Manage Mobile App'
            WHEN subj RLIKE 'vantaca iq|\\biq\\b|business intelligence|report builder|data group|command center' THEN 'Vantaca IQ'
            WHEN subj RLIKE 'revenue manager|management billing|management contract|admin billing|admin contract|contract template' THEN 'Revenue Manager'
            ELSE 'CMI'
          END
        WHEN group_name = 'Financials and SP SME Team' THEN
          CASE
            WHEN subj RLIKE 'vantaca iq|\\biq\\b|business intelligence|report builder|data group|command center' THEN 'Vantaca IQ'
            WHEN subj RLIKE 'revenue manager|management billing|management contract|admin billing|admin contract|contract template' THEN 'Revenue Manager'
            WHEN subj RLIKE 'vantaca pay|payout' THEN 'Vantaca Pay'
            ELSE 'Accounting'
          END
        WHEN group_name = 'Homeowner and Portal/AR SME Team' THEN
          CASE
            WHEN subj RLIKE 'vantaca pay|autopay|auto.?pay|payout' THEN 'Vantaca Pay'
            WHEN subj RLIKE '\\bach\\b' THEN 'Banking'
            WHEN subj RLIKE 'not working|isn.t working' THEN 'CMI'
            WHEN subj RLIKE 'out.{0,4}balance|bankruptcy|ledger issue|discrepancy|incorrect.{0,20}(charge|amount|balance|payment)|invalid.{0,3}charge|audit|reimbursement|refund|coded to gl' THEN 'Accounting'
            WHEN subj RLIKE 'ledger|statement|assessment|owner charge|owner balance|accounts receivable|collections|charge|import' THEN 'CMI'
            ELSE 'Home/Portal'
          END
        WHEN group_name = 'Integrations SME Team' THEN
          CASE
            WHEN subj RLIKE 'ach|lockbox|positive pay|auto.?recon|reconcil|bank rec|bank statement|bank transfer|bank return|banking|transfer fund' THEN 'Banking'
            ELSE 'Integrations'
          END
        ELSE 'Unclassified'
      END as category
    FROM scoped
  )
  SELECT COUNT(*) AS c FROM classified WHERE category = 'Home/Portal'

value = c   # integer count
raw = c     # same integer
Sanity: ~150-200 per week (mean ~180; tight stable band).

Return ONLY this JSON:
{"slug":"new-home-tickets","value":<int|null>,"raw":<int>,"status":"ok|error|stale","notes":"<short>"}
