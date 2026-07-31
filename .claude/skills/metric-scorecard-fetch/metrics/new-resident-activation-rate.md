---
slug: new-resident-activation-rate
source: deferred
mcp: pendo+databricks
---
# New Resident Activation Rate (30d) — DEFERRED until denominator source lands (Q3 Rock, starts July 2026)

DO NOT fetch yet. Methodology is LOCKED below so this flips deferred→auto with no rediscovery.
Full design + sourcing rationale: datasets/product/agent-output/2026-06-11_new-resident-activation-rate-metric-design.md

## Definition
Of residents who became eligible for the Home portal in a cohort (community has the portal enabled,
resident login provisioned), the % who successfully logged in >=1x within 30 days of eligibility.
Counts Home web AND native mobile (a login on either = activated). Residents/homeowners only.

  activation_rate = distinct eligible residents who logged in within 30d / distinct eligible residents

## Numerator — sourceable now (Pendo)
A successful login = resident becomes an identified Pendo visitor with ishomeowner==true.
Conventions: Pendo subId — resolve via `python3 scripts/profile_lib.py --pendo-subid`, never embed
a literal; ishomeowner via metadata.agent.ishomeowner==true;
Home web appId=5961191088521216; Home mobile appIds 5355360917585920 + 6607066993197056.
Per surface (union the visitorId sets across the 3 apps to de-dup; summing counts is an upper-bound approx):
  activityQuery(subId, appId=<each Home app>, entityType="visitor", group=["visitorId"],
    visitorMetadataFilter="metadata.agent.ishomeowner==true",
    dateRange={range:"custom", startDate:<eligibility>, endDate:<eligibility+30d>}, count=true)
Cohort timing: metadata.auto.firstidentifiedvisit / metadata.auto.createdat = first successful login.

## Denominator — BLOCKED (the dependency)
"All residents who COULD log in" cannot come from Pendo (a never-logged-in resident is never a
Pendo visitor) and Pendo account metadata has NO units/doors/resident-count field. It must come
from the Vantaca production roster: eligible resident logins per portal-enabled community + eligibility
date. As of 2026-06-11 the candidate Databricks schemas ({catalog}.migration, {catalog}.pendo) return
PERMISSION_DENIED for this principal. Requires either a granted backend export table (preferred) or
USE SCHEMA grants. See design doc §4. Until then: value null, status "deferred".

## Flip-to-live (July): grant+verify denominator table → set registry source="auto" → enable a fetch
block here computing numerator(Pendo)/denominator(Databricks roster) → sanity-check vs Pay-team ~40%
proxy (expected lower across ALL eligible residents). Sanity band TBD once first real value observed.

Return ONLY this JSON (until flipped live):
{"slug":"new-resident-activation-rate","value":null,"raw":null,"status":"deferred","notes":"Q3 — denominator source pending (see design doc)"}
