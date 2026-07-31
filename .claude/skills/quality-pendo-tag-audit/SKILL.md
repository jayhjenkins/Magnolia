---
name: quality-pendo-tag-audit
description: Use when a Pendo metric looks wrong, inconsistent, or suspiciously extreme (e.g. 0% or 100% conversion, a sudden spike, mismatched numbers between two similarly-named pages/features) and before trusting any Pendo page/feature ID as a monitoring signal — audits whether the underlying tagging (Include/Exclude rules, CSS selectors) actually isolates the intended element rather than over- or under-matching.
allowed-tools: Read, Grep, Glob, Bash, Agent
---

# Pendo Tag Audit

## Purpose

Pendo page/feature tags are CSS-selector-based, and CSS selectors drift silently when a UI is redesigned or two similar elements share the same styling. A metric that looks like a real regression is very often a mistagged element instead — and the only way to tell the difference is to check the actual instrumentation, not just the numbers it produces. This skill is the reusable version of a real incident: a Pendo feature's rule matched all three choices in a modal instead of just one, and a separate "Submit" feature's rule matched Submit-styled buttons across unrelated pages app-wide — together producing a false "0% conversion" alarm for two days before the tagging, not the product, was found to be the problem.

## When to Use

- A funnel/conversion metric reads 0%, 100%, or otherwise implausibly extreme
- Two pages or features with similar names return suspiciously close-but-not-identical numbers
- You're about to baseline or monitor a metric for a launch and haven't verified the tag is trustworthy yet (this is invoked automatically as a hard gate by `workflow-launch-monitor`)
- A number moved a lot right after a UI change, and you're not sure if it's a real behavior shift or the tag no longer matching what it used to

**When NOT to use:** the metric looks plausible and stable — don't audit tags reflexively on every number you look at, only when something is unexplained or about to be load-bearing for a decision.

## Workflow Steps

1. **Inventory.** Search Pendo for every page/feature whose name matches or relates to the target area — use `searchEntities` and `listCountables` with broad name/keyword matches, not just the one ID already in hand. Duplicates hide under near-identical names (e.g. three different page IDs all claiming to be "Requests").

2. **Usage fingerprint.** Pull each candidate's usage (`entityUsage`, `entityUsageTimeSeries`) over the same window. Suspiciously similar-but-not-identical numbers across two candidates is the signature of overlapping/duplicate tagging splitting or double-counting the same real traffic — flag it, don't wave it off as noise.

3. **Get the actual rule.** Pendo's analytics MCP tools do not expose Include/Exclude match rules or CSS selectors — confirmed directly (`searchEntities` returns only name/ID/description even with explicit `itemIds`). Ask the human to paste or screenshot the rule from the Pendo Admin UI for each candidate.

4. **Reality-check against the live DOM.** Ask the human to inspect the actual page/element in a browser and paste the HTML. Specifically look for:
   - A rule keyed on a shared/generic CSS class that ALSO matches other, visually-different elements nearby (the classic false positive — three different buttons sharing the same styling classes, one rule matching all three)
   - Fragile text-content selectors (`:contains('...')`) — these silently break on copy changes, localization, or icon-only variants, and can false-positive-match unrelated elements with the same text elsewhere
   - Whether the live markup already has a stable, purpose-built identifier (`data-feature`, `data-testid`, a unique `id`) that would make a far cleaner tag than whatever rule is currently configured

5. **Identify canonical vs. duplicate.** Cross-reference which ID is already referenced by existing reports/dashboards/monitoring in use — that's usually canonical by default. A populated description/Product Area is a weaker secondary signal, not proof on its own.

6. **Check dependencies before touching anything.** Before deleting or merging a page/feature, check what's "used by" it (other features scoped to that page, guides, dashboards) — Pendo will block a delete with a dependency error, and reassigning the dependency first is the fix. Prefer merge over hard delete when available, since merge preserves history.

7. **When a numeric ID/enum/category needs interpreting, don't guess.** If resolving the right tag requires knowing what a backend ID or category number actually means, don't infer it from visual order or naming plausibility. Dispatch a fresh-context Explore subagent into the actual application codebase (frontend + backend) to find hard evidence — an enum definition, a constant, a comment, a switch statement mapping id → label — and report the exact file path + line number. Say "unconfirmed" rather than guess if the evidence isn't there.

8. **Sequence any cutover carefully.** If there's an active measurement/monitoring effort already running on the current (even known-flawed) tags, don't rip-and-replace mid-stream. Stand up the new, corrected tags in parallel, keep the old ones alive for continuity until the new ones accumulate their own comparable data, and explicitly mark any numbers built on the old tags as provisional/superseded in whatever report is tracking them — append a note, never silently rewrite history.

## Output

A findings summary covering: which ID is canonical vs. duplicate/broken and why, the exact corrected rule to use going forward (if a fix is needed), and whether it's safe to cut over now or should wait per step 8. When invoked by another skill (e.g. `workflow-launch-monitor`'s tag-validation phase), return this summary directly so the caller can fold it into its own output rather than re-deriving it.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Trusting a metric because the ID "sounds right" | Always check the actual rule and live DOM — names are not proof |
| Assuming a flat/zero/100% metric means a real regression | Audit the tag first; this exact pattern (false "0% conversion") is the incident this skill exists to prevent |
| Deleting a duplicate page/feature immediately | Check dependencies first — Pendo will block it, and reassigning first is faster than discovering the block |
| Guessing a backend category/enum mapping from naming or list order | Use a fresh-context Explore subagent for hard code evidence instead |
| Cutting over to new tags mid-investigation | Keep old tags running for continuity; cut over only once new tags have their own accumulated data |

## Related Skills

- `workflow-launch-monitor` — invokes this skill as a hard gate before establishing any baseline
- `context-pendo-analytics` — the underlying Pendo MCP tool reference this skill's queries rely on
