# Velocity Benchmarks — Evidence Trail

This is the evidence behind every number in `calibration.yaml`. If a number looks wrong, this is where to check the derivation — and where to add new evidence as more features ship.

Two independent ticket-level anchors, never the Feature/Epic card's own timeline:

1. **Unit tickets** — active-execution days, first entering "In Development" → Published.
2. **Bug/Defect tickets** — full ticket-level elapsed days, created → resolved.

A Feature/Epic card's own created→released span is not used anywhere in this model. It's inflated by refinement time, every child unit's queueing, and batched release cutovers — none of which is a build-time signal. (Step 1 §2: "Lead time" and "dev-to-pub" are measured per-Unit for exactly this reason.)

## 1. Unit archetypes (Step 1, 2026-06-01)

Source: `datasets/product/agent-output/2026-06-01_home-velocity-data.csv`, 56 per-unit rows across 5 features (Dynamic Forms, WYSIWYG Landing Page, Mobile Resident Experience, PerkSpot Offers, Community Feed). Full narrative: `2026-06-01_home-team-velocity-analysis.md`.

**Classification rule:** foundation = no analogous Service/Contract/component/page exists yet; increment = building on an existing pattern. Cross-repo = unit touches ≥2 repos. Mobile-touching units are carved out to their own `human_native` regime (see below) rather than polluting the ai_leveraged archetypes.

| Archetype | n | p50 (d) | p75 (d) | Raw values |
|---|---|---|---|---|
| increment_single_system | 5 | 2.0 | 2.5 | 2.3, 2.3, 2.3, 2.3, 2.3 |
| increment_cross_repo | 4 | 1.5 | 12.0 | 1.1, 1.1, 1.1, 12.4 |
| foundation_single_system | 5 | 8.5 | 17.0 | 3.2, 8.4, 8.4, 8.4, 17.0 |
| foundation_cross_repo | 7 | 17.0 | 29.4 | 7.6, 17.0, 17.0, 17.0, 29.4, 29.4, 49.2 |
| human_native (Mobile) | 2 | 27.2 | 27.9 | 26.6, 27.9 |

**foundation_tax evidence (excluded from the base numbers above, applied as a flagged multiplier instead):** Dynamic Forms' foundation cohort (VNT-40598–40602, all 48.3d) is excluded from `foundation_single_system` because Step 1 attributes it specifically to an unresolved cross-team CMP-ownership gap (red-team C-5), not generic "first of its kind" — the settled-spec foundation units in the same window (Community Feed, WYSIWYG) ran 7–17d. 48.3 / 17 ≈ **2.8x** → `foundation_tax.multiplier = 2.75`, gated behind an explicit trigger (genuinely unresolved ownership/architecture, not just novelty).

**ef_migrations evidence (new lever):** every unit whose notes mention "+migrations" ran meaningfully slower than its same-archetype peers: Dynamic Forms display-hooks (29.4d, +migrations) vs. WYSIWYG cross-repo peer (17.0d) ≈ 1.7x; Dynamic Forms templates-transition (12.4d, +migrations) vs. bolt-3 cross-repo peer (1.1d) ≈ 11x. n=3, directional only — re-derive once more migration-touching units ship.

**Why Mobile is its own regime:** Step 1's central Mobile finding — 2 units shipped clean (~27d), then 4 more units simultaneously stuck 47–55 days in late-stage gates (PR Review / Needs Push / Validation) behind a single React Native developer. This is a capacity ceiling, not a unit-complexity signal, and the `workflow-velocity-estimate` skill explicitly excludes capacity modeling — so Mobile work gets a flat, low-confidence `base_days` and an out-of-model flag, never false precision.

## 2. Defect/bug ticket cycle time (this session, 2026-07-15/17)

Source: Home HXP-component tickets, `statusCategory = Done`, resolved 2026-05-01 through 2026-07-16 (the same dataset behind `home-release-shipping-mix.html`'s rolling-throughput chart). n=66 defect tickets (Bug + Work Item Defect + Regression Defect + Security Defect).

| Type | n | p50 (d) | p75 (d) |
|---|---|---|---|
| Bug | 57 | 15.2 | 23.0 |
| Regression Defect | 6 | 10.1 | 17.9 |
| Work Item Defect | 3 | 0.8 | 1.1 |
| **All defects combined** | **66** | **14.7 → 15.0 (rounded)** | **22.1 → 22.0 (rounded)** |

Median/p75 used deliberately over mean: one Bug ticket in the sample sat 575 days (a stale/reopened ticket), which would badly distort a mean. This is **created→resolved elapsed time**, not active-execution-only like units — defects don't have a comparable long pre-work architecture-limbo phase, so the full ticket lifecycle is the meaningful "cost" number.

For comparison, the Unit ticket-level cycle time over the same May–Jul window (calendar created→resolved, includes backlog queueing — a *different, noisier* measure than the archetype active-execution numbers above, shown here only as a sanity bound): n=50, p50=21.7d, p75=43.0d.

## 3. Defect ratio per unit shipped

Two independent sources, both pointing the same direction — QA/defect churn scales with public exposure and surface area (Step 1 driver #6):

**Per-feature (Step 1, cumulative over each feature's life):**
| Feature | Units | Defects | Ratio | Surface |
|---|---|---|---|---|
| Community Feed | 4 | 0 | 0.00 | Internal/gated |
| Mobile Resident Experience | 2 (of 9) | 0 | 0.00 | Internal/gated |
| Dynamic Forms | 13 | 5 | 0.38 | Internal (CAI-readiness) |
| WYSIWYG Landing Page | 8 | 11 | 1.4 | **Public, pre-login** |

**Team-wide monthly (this session, shipped-only, matches the release chart):**
| Month | Defects | Units/Features | Ratio |
|---|---|---|---|
| May 2026 | 17 | 21 | 0.81 |
| June 2026 | 19 | 8 | 2.38 (inflated — Q2 bug-burndown epic, not steady-state feature work) |
| July 2026 (to date) | 31 | 22 | 1.41 |

Calibrated to three tiers: **low 0.3** (internal/gated, matches Community Feed/Mobile), **typical 0.9** (default when surface is unclear), **high 1.4** (public-facing/security-sensitive, matches WYSIWYG exactly). June's 2.38 is excluded from the "typical" default as a known outlier month (bug-burndown, not representative of steady-state feature delivery).

## 4. Latency overheads

Step 1 §4.2 bottleneck map, unchanged this round: Backlog→Ready ≈0, Merged→Staging→Published ≈0 ("near-instant batch cutover" in every one of the 5 studied features — **not a bottleneck, don't model it as risk**). Pre-dev queueing (before a unit enters In Development, on an already-settled spec) is carried forward as a directional prior (p50=3d, p75=10d) — distinct from `foundation_tax`, which is architecture-limbo on an *unsettled* spec, already captured in the foundation archetype numbers above.

## 5. Team throughput context (informational only)

From the same session's rolling 4-week ticket-completion analysis (`home-release-shipping-mix.html`, "4-wk rolling" / "By developer" views): team-wide trailing-4-week completions ranged 16–72 tickets across 2026 YTD, with the window ending 2026-07-16 at the year's peak (45 defects + 27 features = 72), roughly 2x the Mar–May steady-state baseline (~25–30). Four named developers (Michael DeGennaro, Anika Viswanathan, Joshua Noel, Sagar Thakore) accounted for ~93% of that recent throughput.

**This is a sanity-check figure, not an input to the estimate.** The `workflow-velocity-estimate` skill deliberately does not model team capacity or parallelism (an estimate's effort-sum vs. actual wall-clock elapsed is exactly the gap that will later reveal the real parallelism factor — see Step 3 in the project history). Use this section only to gut-check plausibility: e.g., "does a 40-active-day, 12-unit estimate look sane against a team currently producing ~25-30 units-equivalent a month, or does it imply monopolizing the whole team for 6+ weeks?"

## Revision log

- **2026-07-17.v1** — Initial reconstruction. `calibration.yaml`/`benchmarks.md`/template did not exist on disk (lost or never committed after the 2026-06-02 Step 2 build — only the skill file and empty `estimates/` dir survived). Rebuilt unit archetypes from the surviving Step-1 CSV; added the defect-ticket layer, defect-ratio lever, and team-throughput context section net-new, using the 2026-07-15/17 Home release analysis session.
- **2026-07-17.v2** — First program-scale run (New-Resident Onboarding, 40 PRD-derived units) exposed two real gaps, both now fixed in the skill/calibration rather than worked around inline:
  1. **Defect-ticket summing broke past the evidence ceiling.** §3's per-feature table above tops out at 11 defects on a single feature (WYSIWYG, 8 units). Applying the same ratio to a 40-unit program and summing 56 tickets' elapsed-day medians (15-22d each) as if additive labor produced an ~840-1232-day line — nonsensical, because `defect_ticket` days are *created→resolved elapsed* time (concurrent queue/triage wait baked in across many simultaneously-open tickets), not per-ticket effort. Fixed via `defect_ratio_per_unit.volume_ceiling_before_flag: 15` in calibration.yaml — above that count, expected defect volume is reported as a workload/staffing signal, never summed into the headline.
  2. **No critical-path layer existed.** The skill's prior design deliberately excluded ALL parallelism/dependency modeling (old Guiding Principle 3), on the theory that team shape changes too often to model. That was too broad — it also excluded *structural* critical-path analysis (a property of the work's own stated dependencies, not an assumption about team capacity), and without it a program-scale effort-sum reads like "1-2 years" when the real, dependency-aware, realistically-staffed timeline was ~4.5-8.5 months. Fixed by adding SKILL.md Phase 2b (dependency capture) + Phase 5b (critical-path/longest-path calculation) + Phase 5c (explicit-staffing-assumption wall-clock translation) + a required director-brief deliverable organized by the PRD's own vertical slices. See `.claude/skills/workflow-velocity-estimate/templates/velocity-director-brief-template.md` and the New-Resident Onboarding director brief (`datasets/product/packages/2026/new-resident-onboarding/2026-07-17_new-resident-onboarding_director-brief.md`) for the worked example this was generalized from.

- **2026-07-17.v3** — Two more fixes, both from real use the same day: (1) a unit (Vantaca Identity Provider's U1) was defaulted to `foundation` on a non-inspectable repo even though the inception transcript already read for that estimate had the engineer explicitly describing it as a reused pattern — Phase 2.3/2.4 now require checking for that testimony before defaulting, and require actual code changes (not API calls) to count as cross-repo. (2) Estimates moved out of a standalone `datasets/velocity/estimates/` directory into each feature's own product package folder (`datasets/product/packages/{year}/{feature-slug}/`), alongside its PRD — this file, `calibration.yaml`, and `templates/` moved into the skill's own directory at the same time, since they're the shared model, not per-feature output.
