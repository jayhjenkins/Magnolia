---
name: workflow-launch-monitor
description: Use when shipping a feature and you need to set up rollout-health monitoring — identifies what's launching and what to measure, validates the Pendo tagging via quality-pendo-tag-audit, establishes a baseline, computes how many observations are needed to call statistical significance, writes a monitor file, and sets up a recurring check that tracks it until enough data accumulates to evaluate.
allowed-tools: Read, Grep, Glob, Bash, Agent, Write, Skill
---

# Launch Monitor

## Purpose

Every feature launch needs an answer to two questions once it ships: is it doing harm, and is it working? This skill sets up a rollout monitor that answers both — a goal metric to prove the feature is succeeding, one to three do-no-harm metrics to catch a regression early, a verified (not assumed) Pendo instrumentation baseline, a calculated sample size and duration so "enough data" is a number decided in advance rather than a vibe, and a recurring check that tracks it and calls the result once that number is hit.

This skill exists because of a real incident: a Home rollout's ARC-conversion funnel read 0% for two straight days before anyone checked whether the Pendo tag itself was broken. It was — a CSS rule was matching all three choices in a modal instead of just one. Once the tag was fixed, the real conversion rate turned out to be healthy and, with enough accumulated data, statistically better than the old experience. The lesson: never trust a launch metric you haven't verified the instrumentation for, and never call statistical significance on a whim at whatever moment a number happens to look convincing.

## When to Use

- You're about to ship a feature (flagged rollout, phased ramp, or straight launch) and want monitoring set up before or right after it goes live
- You already have monitoring numbers moving and want a properly-calculated sample size / stopping point instead of eyeballing it

**When NOT to use:** a metric already looks wrong and you just need to figure out why — use `quality-pendo-tag-audit` directly for that; this skill invokes it as one phase, not a replacement for it.

## Guiding Principles

1. **Be flexible about Pendo's actual state, not an idealized one.** Real Pendo instrumentation is never perfectly set up. Your job is to pick the best metrics that are actually available or cheaply fixable — not to block the whole process demanding a perfect signal that doesn't exist yet. If the ideal metric isn't there, pick the best proxy and say so plainly.
2. **No mandatory external "required reading."** If a do-no-harm/rollout standard, past baseline doc, or metrics spec happens to exist in this repo for the relevant feature or surface, skim it opportunistically — reusable Pendo IDs, an existing baseline, an agreed threshold are all a head start. But never block on it, never assume it's current, and always be ready to define a sound metric set from scratch using Pendo directly when no such doc exists.
3. **Validate before you baseline.** Every page/feature ID this skill is about to rely on gets checked by `quality-pendo-tag-audit` first. A baseline built on a mistagged element is worse than no baseline at all — it produces confident, wrong conclusions.
4. **Decide the stopping point before you start watching.** Compute the sample size needed for significance up front, and commit to evaluating once at that target — not by re-testing at every periodic check and declaring victory at the first crossing (the "peeking" problem: this inflates the real false-positive rate well above the nominal one).
5. **This is portable.** No local server, no bespoke state files, no dependency on any process running on your machine. A markdown file plus a recurring check via the `loop` skill is the whole mechanism — it works the same for anyone with this skill and Claude Code, not just on this machine.

## Workflow

### Phase 1 — Identify the launch

Read the PRD/spec. `$ARGUMENTS` may be a feature slug or a direct file path.

- If given a slug, first check for an existing product package at `datasets/product/packages/<year>/<slug>/` — this repo's per-feature folder convention (`PRD_<slug>.md`, `metrics_<slug>.md`, `one-pager.md`, etc. all live there). Read the PRD and any existing `metrics_*.md` from it.
- If no package is found, ask for the PRD/spec path directly, or ask the user to describe the launch in a sentence if no written spec exists yet.
- Extract: feature name, affected pages/surfaces, primary user action, any cohort/flag info if this is a flagged/phased rollout, and the package's directory path (needed for Phase 6).

### Phase 2 — Determine what to measure

Land on exactly **one goal/objective metric** — usually a funnel step-completion rate or an adoption signal answering "did the thing this feature is for actually get used successfully." Then name **one to three do-no-harm metrics** for the affected surface — typically a frustration signal (rage/dead-click rate) and an active-usage floor.

Pick metrics based on what's realistically available in Pendo for this surface right now, not an idealized list. If a perfect signal doesn't exist yet, pick the best available proxy (a `button:clicked` filter, a page-level frustration rate, time-on-page) and say so plainly in the output rather than blocking on instrumentation that doesn't exist.

### Phase 3 — Validate the metrics (hard gate)

For every page/feature ID chosen in Phase 2, invoke the `quality-pendo-tag-audit` skill via the Skill tool to confirm it actually isolates the intended element. **Do not proceed to Phase 4 on an unaudited ID.** Fold the audit's findings into this launch's monitor file (Phase 6) — including any corrections made and whether a cutover needs sequencing per that skill's step 8.

### Phase 4 — Establish baseline

Pull as much recent pre-launch history as is reasonably available for each validated metric — aim for 1 to 3 weeks, adapt to whatever actually exists (a brand-new surface may have none; use whatever comparable window you can find, or note there isn't one). Compute a simple mean and observed range for each. Note day-of-week or hour-of-day patterns only if they'd materially change how you read a partial day's numbers later — don't over-formalize this into a rigid methodology.

### Phase 5 — Compute sample size and estimated duration

**First, check whether a real historical sample exists for the goal metric** — an actual measured rate with a known n behind it (not a guess, not an unvalidated range someone wrote in a PRD). This determines which of two very different things Phase 5 does:

- **A real historical sample exists:** run the two-proportion z-test below, comparing the historical rate against the target/current rate. This legitimately answers "is the new number better than the old one" and is the preferred framing whenever it's available — it's what do-no-harm metrics with real legacy data should also use (see Phase 2/8).
- **No real historical sample exists** (the common case for a brand-new goal metric on a fully-replaced feature — the old system was often never instrumented for it): **do not substitute a hypothesis test against the PRD's target rate as a stand-in.** A one-sample test against a fixed point value (H0: p = target) will reject almost automatically once n is even moderately large, regardless of whether the true rate is close to or far from target — it only tells you the estimate isn't *exactly* equal to the target, which is already obvious from comparing the two numbers directly. It creates false rigor without answering anything useful, and it cannot support any claim about whether the new system is better or worse than the old one (there's no "old one" in the test). Instead: report the current estimate with a confidence interval (`p̂ ± z·√(p̂(1−p̂)/n)`), state plainly whether that interval clears the target, and say explicitly that "improved vs. legacy" is not a claim this data can support. The only sample-size question that remains is "how many observations until the confidence interval is tight enough to act on" (e.g., ±5 points) — not statistical power against an arbitrary constant.

If a real historical sample exists, compute the sample size needed for a **one-sided** two-proportion z-test:

```
n = (z_α + z_β)² × [p1(1−p1) + p2(1−p2)] / (p1−p2)²
```

- **Standard confidence:** α=0.05 one-sided (z_α=1.645), 80% power (z_β=0.84)
- **High confidence** (offer as an option, e.g. before a ramp-to-everyone decision): α=0.01 one-sided (z_α=2.326), 90% power (z_β=1.2816)

This is standard statistics — reason through it directly or run a quick `python3 -c "..."` through Bash for the arithmetic. No separate reference file or persisted script is needed.

Translate the required N into an estimated calendar duration using Phase 4's observed daily volume of the relevant event (e.g. daily count of the modal-open or funnel-entry event this metric is measured on). If no real historical sample exists (the branch above), there is no "required N" in this power-calculation sense — translate the CI-precision sample size instead, and be explicit in the monitor file that this is an estimation target, not a significance-test target.

**This N applies to the goal metric only.** Do not extend it to do-no-harm metrics — they're evaluated against fixed thresholds continuously (Phase 8 note below), not via a one-time hypothesis test, so they don't share the goal metric's N or its stopping point. If a legacy baseline for a do-no-harm metric happens to include a real sample size (not just a mean/range), a two-proportion test against it can usually be run immediately using data already on hand — large legacy samples (weeks of page traffic, thousands of visitors) tend to already be far more powerful than anything the rollout itself will accumulate quickly, so don't invent a waiting period for these either.

**Do not add a calendar floor on top of N unless you can name the specific statistical reason for it** (e.g., known day-of-week seasonality not yet represented in the sample, or the underlying system changed very recently and its stability is still unknown). "Let's be extra safe" or "let's watch a full cycle just in case" are operational instincts, not statistical requirements — if you want to state one as a soft recommendation, label it clearly as separate from the N-based stopping rule, never blended into it as if it were part of the same calculation. A large effect size can make N resolve in hours; that's a reason to double-check the effect size and the data quality, not a reason to reflexively add days.

State plainly in the output: **this is a one-shot evaluation planned for a specific future N.** The whole point of pre-committing to a target is that re-testing at every periodic check and stopping at the first crossing inflates the real false-positive rate above the stated alpha — the recurring check (Phase 7) tracks progress toward N and reports do-no-harm status along the way, but only formally evaluates statistical significance once N is reached (Phase 8). Once N is reached, evaluate immediately — don't hold the verdict for an unrelated calendar target.

### Phase 6 — Write the monitor file

Write one markdown file into the feature's product package: `datasets/product/packages/<year>/<feature-slug>/rollout-monitor_<feature-slug>.md` — sitting alongside the PRD and other package artifacts, matching this repo's `<doctype>_<slug>.md` package-file naming convention. If no package folder exists for this feature, fall back to `datasets/product/agent-output/YYYY-MM-DD_<feature-slug>-rollout-monitor.md` (date-first, per that folder's convention) and say so explicitly.

**This file must read like a science report, not a narrative log.** The single biggest failure mode is scattering baseline numbers, targets, and current readings across separate prose sections — a reader has to piece together "was 53% good or bad?" by cross-referencing three places. Every number belongs in exactly one place: a master tracking table. Structure, in this order:

1. **Purpose / launch info** (Phase 1)
2. **Tag audit findings** (Phase 3) — which IDs were validated, any corrections made
3. **Metrics Tracking Table** — the load-bearing artifact of the whole file. One row per tracked metric (goal + each do-no-harm), one column each for:
   - `Metric` (name + one-line definition/formula)
   - `Role` (Goal / Do-No-Harm)
   - `Historical Baseline` (the Phase 4 mean + observed range — or "none exists" stated plainly, never silently omitted)
   - `Target / Threshold` (the actual inequality — `≥70%`, `≤5.0% sustained`, `no change from baseline` — never just a bare number with the direction implied)
   - `Current Value` (latest reading, kept in sync with the Monitoring Log's last row)
   - `Observations (n / target N)` — goal metric only needs this; do-no-harm metrics can show "ongoing" since they're evaluated continuously, not at a single N
   - `Status` — exactly one of: `Tracking` (N not yet reached), `Hit target`, `Breached`, `Normal` (do-no-harm, no breach) — a single word/phrase, not a sentence
4. **Stopping Rule** — a short, explicitly-labeled callout (blockquote or bolded paragraph, not buried in prose) stating the target N (from a real two-sample test if a historical sample exists, or a CI-precision target if not — see Phase 5), confidence tier if applicable, and the sentence: **"Every row in the Metrics Tracking Table and Monitoring Log before that point is progress tracking, not a verdict — no call is made until N is reached."** No calendar floor beyond N unless a specific statistical reason is named (Phase 5).
5. **Monitoring Log** — the detailed append-only time-series (raw data supporting the summary table above), columns: `#`, `Time`, one column per tracked metric's current cumulative reading, `Flag`. This is supporting detail for the table above, not a replacement for it — the table is what a reader checks first.
6. **Report Out** — the verdict section, always present even before it can be filled in. Before N is reached: state plainly `PENDING — N observations needed, <n so far> collected`. Once N is reached (Phase 8), fill in: observed N vs. target N, the goal metric's result (significance test result if a historical sample exists, or a plain CI-vs-target comparison if not — never a hypothesis test against the target), a pass/fail line for every do-no-harm metric independently, and one **Bottom Line** (Phase 8's four outcomes).

### Phase 7 — Set up the recurring check

Invoke the bundled `loop` skill via the Skill tool with:
- **Interval:** default hourly, adjusted down (e.g. every 4-6 hours or daily) if the expected daily volume from Phase 4 is low enough that hourly checks would mostly be empty
- **Prompt:** a fully self-contained instruction that, on each fire, tells the agent to: re-read the monitor file (by path), pull fresh numbers for every tracked metric, append one row to the Monitoring Log with a plain-language flag (normal / elevated / escalate), **and update the `Current Value`, `Observations (n / target N)`, and `Status` columns in the Metrics Tracking Table** so the summary table never goes stale relative to the log. Compare the accumulated goal-metric observation count against the target N recorded in the file. If the target has been reached, run Phase 8 instead of just appending a row.

`/loop` already handles the session-only-vs-cloud-schedule decision on its own (it asks directly when the interval implies multi-day duration) — don't reimplement that judgment here, just hand it a good interval and a complete, self-contained prompt.

### Phase 8 — Evaluate at target N, then stop

Triggered by a `/loop` fire that finds the accumulated observation count has reached the target N (or the CI-precision target, if no real historical sample exists for the goal metric). What "evaluate" means depends on which branch Phase 5 took:
- **Real historical sample exists:** run the one-sided two-proportion z-test once, comparing the accumulated current-period data against the historical sample.
- **No real historical sample exists:** compute the confidence interval on the current estimate. **Do not run a one-sample test against the target as a substitute** — see Phase 5's reasoning. State whether the CI clears the target, full stop; do not describe this as "significant" or attach a z-score/p-value to it, since no hypothesis test was run.

Fill in the file's **Report Out** section (replacing the `PENDING` placeholder) with: observed N vs. target, the goal metric's result (significance test result, or CI-vs-target comparison — whichever applies), a pass/fail line for every do-no-harm metric independently, and one explicit **Bottom Line** — four possible outcomes, not three:
- **Proceed** — goal metric clears its bar (significantly better than historical, or CI clears target), no do-no-harm metric breached its band
- **Hold, gather more data** — reserved for a real historical-comparison test that is genuinely inconclusive (not yet significant either direction) with nothing breaching. Does not apply to the no-historical-sample case — a precise CI is a real answer even without a hypothesis test, so there's nothing to "gather more data" toward once the CI is already tight
- **Short of target — investigate, don't just wait** — the goal metric (via significance test, or via CI clearly excluding the target) is below where it needs to be, but no do-no-harm metric breached. More monitoring time will not change this; the next step is root-causing the product gap (e.g., break the metric down by segment/category to find where the shortfall concentrates), not extending the observation window. If no historical sample exists, be explicit that this outcome says nothing about "better or worse than before" — only "short of the goal"
- **Revert / investigate** — any do-no-harm metric breached its band at any point during monitoring (this is an independent check — flag it regardless of what the goal metric's result says)

Also update the Metrics Tracking Table's `Status` column to reflect the actual call (e.g. `Hit target`, `Short of target`, `Breached`) — the table and the Report Out must agree at a glance. Then stop the recurring check.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Baselining against an ID you haven't tag-audited | Phase 3 is a hard gate — always run `quality-pendo-tag-audit` first |
| Treating a scary-looking early number as proven before enough data has accumulated | Compute the target N in Phase 5 and wait for it — a single bad day is not a verdict |
| Re-checking significance at every hourly pull and stopping at the first crossing | Evaluate once, at the pre-computed target N (Phase 8) — this is the "peeking" problem |
| Demanding perfect Pendo instrumentation before starting | Pick the best available proxy metric and say so; don't block the whole launch on ideal tagging |
| Writing the monitor file to a generic output folder when a product package exists | Phase 6 writes into the feature's package so it sits with the PRD, not in a disconnected folder |
| Adding a calendar floor ("wait 14 days to be safe") on top of the computed N, without a stated statistical reason, and applying it to do-no-harm metrics that were never gated by that N to begin with | N is goal-metric-specific; do-no-harm runs on its own threshold/test logic. Evaluate the moment N is actually met — a large effect size resolving fast is a reason to check your math, not to add days |
| Labeling a statistically significant *shortfall* as "hold, gather more data" | That phrase is for genuine ambiguity only. A well-powered result showing the metric is significantly below target is a real answer (see Phase 8's outcomes) — more data won't change it |
| Running a one-sample hypothesis test of the goal metric against the PRD's target rate when no real historical sample exists, then reporting the resulting z-score as if it proved something | With a large enough n this test rejects almost automatically regardless of the true rate — it only shows the estimate isn't *exactly* the target, which comparing the two numbers already showed. It also cannot support any "better/worse than legacy" claim, since there's no legacy sample in the test. Use a confidence interval on the estimate instead (Phase 5), and say plainly that "vs. legacy" is unanswerable when no historical sample was ever measured |

## Success Criteria

- Goal metric and do-no-harm metrics are named and each has a verified (tag-audited), not assumed, Pendo ID
- A real baseline (mean + observed range) exists for every tracked metric
- A specific target N and estimated duration exist for the goal metric, computed via a one-sided two-proportion z-test, before monitoring starts
- The monitor file lives with the feature's PRD/package, not orphaned in a generic output folder
- The recurring check is self-contained (re-reads the file and re-pulls data itself) and will evaluate exactly once, at the target N, then stop itself
- A reader can answer "is this good or bad, and are we done yet" from the Metrics Tracking Table and Report Out alone, without cross-referencing the Monitoring Log — baseline, target, current value, and observation count all sit in one row per metric, not scattered across prose

## Related Skills

- `quality-pendo-tag-audit` — invoked as a hard gate in Phase 3
- `context-pendo-analytics` — underlying Pendo MCP tool reference
- `workflow-prd-creation` — where the PRD this skill reads typically comes from
