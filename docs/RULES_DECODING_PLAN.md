# TM1 Rules Decoding — Phase 2 Planning Document

**Status:** Draft for discussion — no code written yet.
**Companion to:** Phase 1 (TI process lineage), which this phase extends.

---

## 1. Why this is a different, and arguably higher-risk, problem

Phase 1 answered "what does this process do?" Rules answer a question Phase 1
cannot: **"what does this cube calculate, and is that calculation trustworthy?"**

TI process bugs tend to be loud — a process fails, an error appears, someone
notices. Rule bugs tend to be **silent**. A missing feeder doesn't throw an
error; it just makes a calculated cell show zero or blank forever, and nobody
finds out until a number in a board report looks wrong. This is the single
most common class of "the model is quietly broken" issue in real TM1 estates,
and today there is no scalable way to find it except opening rules one cube at
a time.

There's also a blind spot in what we've already built: **Phase 1 cannot see
cubes that are populated by rules instead of TI.** A cube with no TI writing
to it looks "unloaded" in `}Meta_Process_Cube` today, even though it's fully
live-calculated. Rules phase closes that gap.

---

## 2. The consultant's checklist — what to actually look for

This is the master list, organised by risk type. Not everything on this list
will make it into v1 — see §9 for phasing.

### 2.1 Correctness risks (silent wrong-number risk)
- **Missing feeders** — a rule calculates a value for an area with no feeder
  statement covering it. In a sparse cube, that cell never populates.
- **Underfeeding** — the feeder's target area is *narrower* than the rule's
  calculated area (e.g. rule applies to all Versions, feeder only feeds
  `Actual`).
- **Overfeeding** — the feeder's target area is *broader* than needed (e.g.
  feeds an entire consolidation when the rule only ever calculates for a
  handful of leaves). Not wrong, but wasteful — see performance risks.
- **Orphan feeders** — a feeder statement whose target area has no
  corresponding rule statement at all (feeding something that was never
  calculated — dead weight, or a sign the rule was since removed/changed).
- **`SKIPCHECK` present** — this pragma disables TM1's own built-in feeder
  consistency warnings for that cube. If it's set, our static check becomes
  the *only* net catching feeder problems — worth flagging prominently
  wherever it appears.
- **Circular rule dependencies** — Cube A's rule reads Cube B via `DB()`,
  and Cube B's rule reads Cube A. Can cause calculation-order surprises.
- **Rule statement shadowing** — TM1 evaluates rule statements top-down and
  (without `CONTINUE`) applies the *first* match. A broad statement placed
  before a more specific one can silently shadow it.
- **`CONTINUE` / `STET` usage** — both are commonly misunderstood.
  `CONTINUE` stacks onto the previous matching statement rather than
  replacing it; `STET` says "ignore the rule, use the stored value" for a
  sub-case. Worth surfacing every use of both as a "read this carefully"
  flag.
- **Dangling references** — a rule or feeder hardcodes a cube, dimension, or
  element name that may no longer exist (same class of risk as the
  "unresolved reference" work we built for TI, but the opposite direction —
  here the risk is a *stale* reference, not a *dynamic* one).
- **N: vs C: level overrides** — a rule intentionally overriding a
  consolidated value instead of letting it sum naturally. Often intentional,
  sometimes a leftover mistake.
- **Attribute-driven logic** (`ATTRS()`/`ATTRN()`) — creates a hidden
  dependency on dimension attribute values that lineage tools (including our
  own Phase 1 cubes) don't currently surface.

### 2.2 Performance risks
- **Overfeeding at scale** — every fed cell consumes real memory on the
  server. A feeder that touches an entire large consolidation when only a
  few leaves are ever calculated is a classic, very common cause of TM1
  memory bloat.
- **Expensive functions inside heavily-fed areas** — repeated `DB()` calls
  across cubes, `ATTR*` lookups, or deep `IF()` nesting inside a rule that
  applies across a huge area.
- **Deep rule chains** — Cube A's rule reads Cube B, whose rule reads Cube C,
  and so on. Each hop adds calculation dependency and makes troubleshooting
  slower.

### 2.3 Maintainability / documentation risks
- Rules with no comments explaining intent.
- Deeply nested `IF()` logic with no clear structure.
- The same logic duplicated across multiple cubes' rules, suggesting a
  refactor opportunity.

### 2.4 Lineage / knowledge risks (extends Phase 1's mission)
- **Cross-cube read dependency** — which cubes does this rule pull from via
  `DB()`? This is the rules equivalent of `}Meta_Process_Cube`, and answers
  "if I change Cube B, what calculated cubes break?"
- **Rule-driven vs TI-driven cubes** — today, a cube with no TI writer looks
  unpopulated. This phase would flag "this cube is calculated, not loaded" —
  closing a real gap in the current model.

---

## 3. What a rule actually looks like (for context)

```tm1rule
SKIPCHECK;

['Net Sales'] = N: ['Gross Sales'] - ['Returns'];
['FX Rate'] = N: DB('FX Rates', !Year, !Month, 'Rate');
['Budget Flag'] = S: IF(['Version'] @= 'Budget', 'Y', 'N');

FEEDERS;
['Gross Sales'] => ['Net Sales'];
['Returns'] => ['Net Sales'];
```

Two sections: the **rule body** (calculation logic, evaluated top-down,
first match wins unless `CONTINUE`), and **FEEDERS** (tells TM1 which input
cells should "wake up" the calculation for sparse consolidation).

⚠️ **Note:** exact grammar nuances (e.g. whether `IF()...ENDIF` block form is
valid inside FEEDERS, exactly how cross-cube feeder targets are written)
need to be validated against real rule text samples before the parser is
finalised. I don't want to guess at grammar edge cases and get it wrong —
this is a validation step in Phase 2a, not an assumption baked in now.

---

## 4. What's genuinely feasible statically vs what needs a live server

Being honest about this distinction up front, because it directly affects
how confidently we can label a "feeder gap" as a real bug.

### Feasible with static analysis only (no live data query)
- Reading rule text per cube (TM1py exposes this directly — no execution
  needed).
- Parsing rule statements and feeder statements into structured facts.
- Extracting `DB()` cross-cube references → cube-to-cube lineage.
- Detecting pragmas (`SKIPCHECK`, `FEEDSTRINGS`), `CONTINUE`, `STET` usage.
- **Structural** feeder-gap / over-feed / under-feed detection — comparing
  the *named* area a rule statement targets against the *named* area a
  feeder statement covers. This includes reading dimension hierarchies
  (structure only, not cube data) to reason about consolidation size, which
  is still a safe, read-only operation.
- Template-based English description of rule logic (see §6).

### Requires live server interaction (out of scope unless explicitly agreed)
- **Proving** a feeder gap actually produces a wrong number in practice —
  that depends on whether real data ever populates the affected area at all.
- TM1's own **"Check Feeders" / "Trace Feeders"** functions, which sample
  real cube data server-side to validate feeders numerically. These are a
  complementary, more authoritative check — our tool would flag *candidates*
  for review; TM1's native check (or a developer manually verifying) gives
  the final word.

**Honest framing for any gap we report:** these are *structural heuristic
flags for manual review*, not proof of a bug. That's consistent with how
we already treat unresolved references in Phase 1 — surfaced, not asserted.

---

## 5. Proposed capture model (draft — will evolve during build, same as Phase 1 did)

| Cube (draft name) | Dimensioning (draft) | Captures |
|---|---|---|
| `}Meta_Rule_Cube` | Cube × Measure | Is this cube rule-enabled? Pragmas present (SkipCheck, FeedStrings)? Rule line count? |
| `}Meta_Cube_Rule_Dependency` | SourceCube × TargetCube × Measure | Cross-cube reads via `DB()` — the rules equivalent of process chain lineage |
| `}Meta_Rule_Feeder` | Cube × FeederRef × Measure | Each feeder statement: source area, target area, conditional Y/N, line |
| `}Meta_Rule_Feeder_Gap` | Cube × StatementRef × Measure | GapType (NoFeeder / Underfeed / Overfeed / OrphanFeeder), detail, line — the actionable work queue |
| `}Meta_Rule_Function` | Cube × Function × Measure | Same watch-list pattern as `}Meta_Process_Function`, applied to rule functions (`DB`, `ATTRS`, `STET`, etc.) |

English descriptions are proposed as an **extra measure on the statement-level
cube** rather than a separate cube — same lesson learned in Phase 1 about not
over-fragmenting the model.

This table is intentionally a starting point, not a final schema — exactly
how Phase 1's dimension/attribute design evolved (we started assuming two
cubes, ended up with one cube plus a role dimension, because it was cleaner).
Expect the same refinement here once we're looking at real rule text.

---

## 6. English decoding — approach and honest limits

**Default approach: template-based, not LLM-based** — consistent with Phase
1's "no LLM" principle. Deterministic pattern matching against the parsed
rule structure:

| Pattern | Example output |
|---|---|
| Simple assignment | *"Where Version = Budget, Net Sales = Gross Sales minus Returns."* |
| `DB()` reference | *"Pulls FX Rate from the 'FX Rates' cube for the current Year/Month."* |
| `IF()` | *"If Version is Budget, sets Budget Flag to 'Y', otherwise 'N'."* |
| `STET` | *"Uses the stored/loaded value instead of calculating, for this case."* |
| `CONTINUE` | *"Adds to the result of the previous matching rule, rather than replacing it."* |

**Honest fallback for complex logic:** past a certain nesting/complexity
threshold, the tool should say *"Complex rule — manual review recommended"*
alongside the raw statement, rather than attempt a plain-English translation
that might be confidently wrong. This mirrors exactly how unresolved TI
references are handled — surfaced for a human, not guessed at.

**Open question:** whether an LLM-assisted summarisation pass for the
*complex* cases only is ever wanted as an opt-in enhancement later. That
would be a deliberate scope decision (data leaving the static, fully offline
model), not a default — flagged in §8 as something to explicitly decide, not
assume.

---

## 7. Feeder gap / over-feed / under-feed detection — design sketch

1. Parse each rule statement → determine its **target area** (which
   dimension elements or wildcards it calculates for).
2. Parse each feeder statement → determine its **source area** and
   **target area** (same cube or cross-cube).
3. For each rule statement's target area, check whether *any* feeder's
   target area overlaps it.
   - No overlap found → **No Feeder** flag.
4. Where a feeder does cover the area, compare specificity:
   - Feeder area is a strict superset of what's actually calculated →
     **Possible Overfeed**.
   - Feeder area is a strict subset of the rule's target area →
     **Possible Underfeed**.
5. Feeder statements with no corresponding rule statement at all →
   **Orphan Feeder**.

Every flag carries the cube, the rule/feeder line number, and a plain-text
detail — same "work queue" shape as `}Meta_Unresolved_Reference`.

---

## 8. Open decisions needed before we start building

1. **Static-only, confirmed?** — I'm assuming yes (no live "Check Feeders"
   query, no execution), consistent with everything built so far. Please
   confirm.
2. **No LLM, confirmed — even for English decoding?** — Template-based only,
   per §6. Confirm, since "decode in English" is the one feature where the
   temptation to reach for an LLM is real.
3. **Scope of cubes** — all rule-enabled cubes, or exclude system/control
   cubes (`}`-prefixed) the same way TI parsing excludes control processes?
4. **Build order** — my recommendation is to start with the cube-to-cube
   lineage (§5, `}Meta_Cube_Rule_Dependency`) and feeder-gap detection
   together, since those are the two genuinely new capabilities; English
   decoding and the function watch-list are lower-risk, incremental
   add-ons. Agree, or reorder?
5. **Naming** — continue the `}Meta_Rule_*` prefix, consistent with
   `}Meta_Process_*`? Assumed yes unless you'd rather something else.

---

## 9. Proposed phasing

| Phase | Scope |
|---|---|
| **2a** | Rule reader (per-cube rule text) + statement/feeder parser + `}Meta_Rule_Cube` (pragmas, line counts). Proves the plumbing, same role as Phase 1's audit-cube-first approach. |
| **2b** | `}Meta_Cube_Rule_Dependency` — cross-cube `DB()` lineage. |
| **2c** | `}Meta_Rule_Feeder_Gap` — structural feeder gap / over-feed / under-feed detection. *(Highest-value new capability.)* |
| **2d** | `}Meta_Rule_Function` — watch-list pattern reused from Phase 1. |
| **2e** | English rule descriptions (template-based, with explicit complex-rule fallback). |

---

## 10. What this phase will NOT claim

- It will not claim to *prove* a feeder gap causes wrong numbers in
  production — only that the structure looks incomplete and warrants review.
- It will not replace TM1's native Check Feeders / Trace Feeders — it
  complements them by working across the whole model, offline, and
  producing a persistent, queryable list instead of a one-cube-at-a-time
  manual action.
- It will not use an LLM by default for anything, including English
  descriptions.

---

*This is a planning document only. No schema, parser, or writer code has
been written yet — that begins once the open decisions in §8 are confirmed.*
