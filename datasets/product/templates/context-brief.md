# Context Brief: {Initiative Name}

| Field | Value |
|-------|-------|
| **PM** | {display name from profile} |
| **Date** | {YYYY-MM-DD} |
| **Package** | `datasets/product/packages/{YYYY}/{slug}/` |
| **Status** | Draft / Ready for Vision |
| **Gate 1** | PASS / FAIL — {one line: which problem, backed by which data} |

> Phase 1 of the product package. Every later artifact (press releases, FAQ, AI agent scenarios, PRD, expansion proposals, red team, business case) reads this file. Facts carry a citation `[E#]` that resolves in the Evidence table. Anything uncited is marked *(PM assertion)* or *(assumption)*.

---

## 1. Problem Statement

**Problem**: {1–3 sentences. Specific, concrete, observable. In the customer's language, not engineering language.}

**Customer statement**:
- I am: {narrow description of the customer, with motivations}
- I'm trying to: {desired outcome}
- But: {problem or barrier}
- Because: {root cause, if known}
- Which makes me feel: {emotion}

**Backed by**: {the strongest 1–3 evidence IDs, e.g. [E1], [E3]}

---

## 2. Who Has It

**Primary segment**: {named segment with characteristics — not "everyone"}

**Reach**: {how many customers / users / accounts are affected, with citation or *(estimate)*}

### Personas

> 3–5 distinct people affected. The Red Team persona-lens review and the internal press release draw from this list.

| Persona | Role / context | Technical literacy | What they need | Evidence |
|---------|----------------|--------------------|----------------|----------|
| {name} | {role} | {low / medium / high} | {goal} | [E#] |

### Named customers

| Customer | How it shows up for them | Evidence |
|----------|--------------------------|----------|
| {customer} | {symptom} | [E#] |

---

## 3. Evidence

| ID | Type | Source | Date | What it shows |
|----|------|--------|------|---------------|
| E1 | Meeting | `{path/to/transcript}` | {YYYY-MM-DD} | {finding} |
| E2 | Usage data | {analytics source + query or report} | {date range} | {metric and value} |
| E3 | Support | {ticket source + query or ticket IDs} | {date range} | {count / theme} |
| E4 | Sales | {call source + call IDs} | {date range} | {theme} |

### Customer voice

> "{verbatim quote}" — {speaker role}, {customer} [E#]

### Behavioral baselines

> Current numbers the Success Signal moves from. "Not measured" is a valid answer; say so.

| Metric | Current value | Source |
|--------|---------------|--------|
| {metric} | {value} | [E#] |

### Sources not available

{List any source that was unavailable or not connected (e.g. "Product analytics: not connected") so readers know what was not checked.}

---

## 4. Current Workaround

**How they solve it today**: {the workaround, tool, or manual process}

**Why it hurts**: {time, cost, errors, risk — quantified where possible} [E#]

---

## 5. Use Cases / Jobs to Be Done

> Concrete jobs the customer is trying to get done. The AI Agent Scenarios designer and PRD user scenarios build on these.

1. **{Job}** — {who, trigger, desired outcome} [E#]
2. **{Job}** — {who, trigger, desired outcome} [E#]

---

## 6. Why Now

{What changed, what it costs to wait, what deadline or market shift makes this the right time.} [E#]

---

## 7. Market & Competitive Context

- **Alternatives customers use or compare against**: {competitors, adjacent tools, in-house builds} [E#]
- **Market signal**: {sizing hints, segment counts, pricing signals the business case can use} [E#] or *(not researched)*

---

## 8. Constraints

- **Data boundaries**: {what data exists / does not}
- **Technical / platform**: {known limits or dependencies}
- **Commercial / contractual**: {commitments, pricing, packaging}
- **Compliance / access**: {who can see what}
- **Timeline**: {fixed dates, if any}

{Delete bullets that genuinely do not apply.}

---

## 9. Success Signal

| Signal | Baseline | Target | How measured |
|--------|----------|--------|--------------|
| {leading or outcome metric} | {from Behavioral baselines} | {target} | {source} |

**Qualitative signal**: {what a customer would say or do if this worked}

---

## 10. Open Questions

| # | Question | Why it matters | Owner |
|---|----------|----------------|-------|
| 1 | {question} | {what it blocks} | {name or "PM"} |
