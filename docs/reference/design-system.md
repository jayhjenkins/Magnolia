# Design system — card registry + theme tokens

The design system is two things: the **declarative card registry** (what a card is made of) and the **theme tokens** (how it looks). This doc is the model and the boundaries; the gate that enforces it and the step-by-step authoring guides are linked, not copied.

## 1. The token-only HARD RULE

A card definition references **theme tokens only** — never a hardcoded color, radius, or transition. This is what makes every card 100% theme-aware across all Moods.

This is invariant #3 — see [`invariants.md`](./invariants.md). The gate is `scripts/card_schema.py`; run it and expect `registry.json OK`.

> The same token-only rule extends to **Cadence** program-type presentation chips (`cadence/programtypes/registry.json` → `presentation.chip_tokens`), enforced by the sibling gate `scripts/program_schema.py` (invariant #9). When you edit a program type's chips, the rule and the boundary below apply identically. See [`cadence.md`](./cadence.md).

## 2. The card schema

A card type is a declarative entry in `ui/task-board/cardtypes/registry.json` of shape `{ signals, actions, body }`. Slots render in a fixed order (`SLOT_ORDER` in `card_schema.py`):

```
head → title → context → signals → body → actions
```

- **signals** — predicate-driven status chips. Available ids: `due`, `overdue`, `waiting_on`, `waiting_due`, `schedule`, `message`, `jira_draft`, `cron`.
- **actions** — buttons mapped to handlers. Available ids: `mark_done`, `open_output`, `publish_jira`, `accept`, `reject`, `keep`, `undo`, `graduate`, `confirm`.
- **body** — one of the `BODY_RENDERERS` (`diff`, `preview`, `agreement`) or `null` for no card face.

`head`, `title`, and `context` are framework-owned slots rendered automatically from the task — they are not part of the `{ signals, actions, body }` an author fills in.

Current card types: `task`, `recommendation`, `receipt`, `graduation`, `confirm`. See `registry.json` for the field-by-field shape.

## 3. The composition boundary

This is the rule an agent most needs before adding a card type:

- **Composing** existing signals, actions, and body renderers into a new card type is **pure registry work — zero JS**. This is exactly what `meta-create-card-type` does: add an entry to `registry.json`, pass the gate, done.
- A **new signal predicate**, a **new action handler**, or a **new body renderer** is **JavaScript work in `ui/task-board/js/`** and is **OUT of the composition path**. It's a code change, not a card-type addition.

If your new card type only references ids that already exist (sections above), you are composing. The moment you need a signal/action/renderer that does not yet exist, you have left the card-type path and entered a JS code change.

## 4. Moods / theme tokens

A **Mood** is a token-only stylesheet `ui/task-board/themes/<id>.css` scoped to `[data-theme="<id>"]`. Derived tokens (`*-soft` tints, legacy `*-bg` aliases) are computed once in `index.html`'s `:root` from a Mood's primitives — a Mood file never repeats them. Switching a Mood only swaps tokens; it never changes interactions or UX.

To add a Mood, follow the 3 steps in [`themes/README.md`](../../ui/task-board/themes/README.md): copy `_TEMPLATE.css` → `<id>.css`, add a `<link>` in `index.html`, register it in `js/themes.js`'s `MOODS` array.

---

**Canonical source:** `scripts/card_schema.py` (the gate); `ui/task-board/cardtypes/registry.json` (the registry); `ui/task-board/themes/README.md` + `ui/task-board/themes/_TEMPLATE.css` (theme authoring).
