# {{feature_name}} — Build Length, Sequencing & Scope Brief

**Date:** {{date}} · **Backing detail (per-unit evidence):** `{{date}}_{{feature-slug}}_estimate.md`

## Shipping strategy at a glance

| Slice | What ships | When | Estimate |
|---|---|---|---|
| **1 — {{slice name}}** | {{one-line description of what this slice delivers}} | {{sequence position — "Now" / "After Slice N" / "parallel with Slice M"}} — **{{realistic timeline range, e.g. "~7–16 weeks"}}** | {{labor p50–p75}} person-days |
| **2 — {{slice name}}** | {{...}} | {{...}} | {{...}} |
| {{one row per slice found in the PRD's shipping strategy (Phase 1b). If no shipping strategy existed and milestones were used as a fallback, add a row here noting that, and recommend the PRD add explicit vertical slices before the next planning pass.}} | | | |

**Call it {{one-sentence plain-language headline anchoring the total — against a stated gut-check if one exists, otherwise against the scope in plain terms}}.**

{{One sentence, no more: the single most load-bearing structural fact in this estimate — name the critical-path chain if a real one exists ("floor is driven by the chain A → B → C"), or the biggest single cut lever, or the biggest open risk. Whichever fact would most change the reader's plan if they only read one more sentence.}}

---

*Everything below is supporting detail — the full labor/floor/timeline breakdown, the dependency chain, staffing plan, a comparison against any stated gut-check, and concrete cut/de-risk levers. The table above is the answer; read on only if you have a follow-up question.*

## The full number breakdown

| | p50 | p75 |
|---|---|---|
| **Total labor** (all units + expected defects — a budget/headcount number, not a timeline) | {{labor_p50}} person-days | {{labor_p75}} person-days |
| **Absolute floor** (infinite engineers, still can't go faster than this) | {{floor_p50}} days ≈ {{floor_p50_wk}} wk | {{floor_p75}} days ≈ {{floor_p75_wk}} wk |
| **Realistic timeline** (stated staffing assumption, staged/parallelized per the sequence below) | **~{{realistic_p50_wk}} weeks** | **~{{realistic_p75_wk}} weeks** |

The absolute floor is {{"not staffing-driven" if a real critical path exists else "effectively equal to labor / assumed team size, since no single dependency chain dominates"}} — {{if a chain exists: "it's one specific dependency chain: " + chain description + ". That chain is N units deep and each depends on the last one finishing. No amount of headcount fixes a strictly serial chain — only cutting or re-sequencing it does. See 'Where to cut' below."}}

{{If defect count was above the volume ceiling: "Expected defect-ticket volume: ~{{n}} tickets — a workload/staffing signal, not summed into the numbers above. See 'Staffing' section."}}

{{If a named stakeholder/engineer's own build estimate exists for any part of this scope, add a subsection here: "## Anchoring against your own gut-check" — state their estimate, the model's equivalent-scope number, the ratio, and a plain explanation of the gap (real scope differences vs. a genuine model error found and fixed — never hand-wave a >1.5x gap without naming a specific cause).}}

---

## Recommended sequence

```
{{ASCII or simple week-range diagram showing which slices are serial vs. parallel, per the actual cross-slice dependency check in Phase 5c — not just the PRD's stated slice numbering}}
```

{{1-2 sentences: confirm/refute whether the PRD's own stated slice order matches what the dependency graph actually requires. If slices the PRD lists as sequential can actually run in parallel, or vice versa, say so explicitly — this is a real finding, not restating the PRD.}}

**Staffing implication:** peak concurrent need is ~{{n}} engineers (during the {{X}}/{{Y}} overlap window), not the whole team. {{Call out any units dependent on a system/team outside the requester's own control — e.g. CMP, another team's platform — and state plainly that this is a separate staffing ask, not covered by the requester's own headcount.}}

{{If expected defect volume was above the ceiling: state it here as a staffing/capacity signal — e.g. "the ~N expected defect tickets are roughly X% of the team's recent single-month defect volume — plan validation/support capacity for that, it's a real number worth planning around even though it isn't summed into the timeline above."}}

---

## Where to cut / de-risk scope (the actual prioritization levers)

1. **Spike {{unit on critical path with lowest confidence}} first, before quoting any date.** {{why — e.g. non-inspectable system, explicitly flagged open question in the PRD, root of multiple long chains}}. If it comes in bigger than estimated, the floor moves with it; if smaller, the floor drops immediately.
2. **{{Second lever — usually deferring/cutting the largest unit on the critical path}}.** {{Quantify: "cutting X drops the floor from A to B days."}} {{Cite the PRD's own language if it already calls this deferrable.}}
3. **{{Third lever — carve out anything blocked on an external/other-team dependency}}.** {{Name it, state its estimate here is a placeholder pending the other team's own timeline.}}
4. **{{Fourth lever — cheap, low-risk cuts that don't move the floor at all}}.** {{Name specific units/slices, quantify labor saved.}}

---

## What I'd tell you to greenlight, concretely

- {{Ship-first recommendation, usually the cheapest/lowest-risk slice}}
- {{Spike recommendation, in parallel with the above}}
- {{Next commit, staffed how, expected range}}
- {{The one decision that most changes the timeline if made now (usually the cut/defer lever) — state the two resulting scenarios explicitly, e.g. "with X: ~A-B weeks. Without X: ~C-D weeks."}}
