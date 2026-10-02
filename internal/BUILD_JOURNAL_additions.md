<!--
Paste into internal/BUILD_JOURNAL.md:
 1. Replace the "Status:" line at the end of Part T with the T-status block below.
 2. Add Part W after Part V (before Part Z).
 3. In the Contents table, add:  | W | Phase 2e: feeder-gap detection |
 4. In Part Z, update "Status at last update" to: schema 1.7; rules Phases 2a–2e complete.
 5. In the Part Z backlog, delete the "Phase 2e" row.
Then delete this file.
-->

**Status:** confirmed on dev – 35 rule-function rows, 129 function uses, 8 distinct
functions, 1 cube using hierarchy functions. Earlier phases unchanged (61 `DB()`
references, 1 missing element).

---

## Part W — Phase 2e: feeder-gap detection

### W1. Design
**What:** `}Meta_Rule_Feeder_Finding` = `}Meta_Cube` × `}Meta_RuleStatement` ×
`}Meta_RuleFeederFindingType` × measures (schema 1.7). Built by
`parser/rules/rule_feeders.py`, `hierarchy_index.py` and `writers/rule_feeder_writer.py`.
**Why:** with SKIPCHECK, a rule cell that is not fed disappears from zero-suppressed views
and is left out of consolidations; a feeder aimed at the wrong place wastes memory.
**How:** every rule area and every feeder target is turned into
`{dimension: {elements}}` and compared across cubes:
- a `[...]` target keeps the source area and replaces the dimensions it names;
- a `DB()` target restricts each dimension with a literal argument;
- areas overlap if, in every shared dimension, two elements are equal or one is an
  ancestor of the other (hierarchy edges read once per dimension, one REST call);
- unresolvable elements are treated as unrestricted, so only clear gaps are reported.
Findings: `UnfedRule`, `DeadFeeder`, `FeederFeedsNoRule`, `FeedersWithoutSkipCheck`,
`UncheckedRule`, each with a severity. `}Meta_RuleStatement` keys like `Line 00057` make rows
sort in rule-text order. Four views added: Errors, Unfed Rules, Dead Feeders, Over-feeding.

### W2. Gotchas
- **mypy list variance:** a list built with `None` in a tuple slot is typed
  `list[tuple[..., None]]` and will not pass where `list[tuple[..., int | None]]` is
  expected. Annotate the list with the wider type when it is created.
- **Black/ruff rewrite files at commit:** after a failed pre-commit run, `git add -A` again
  before re-committing.

### W3. First dev run
49 rules and 26 feeders checked, 9 hierarchies read, **4 findings, 0 false positives**:

| Cube / line | Finding | Cause |
|---|---|---|
| Retail 96 | DeadFeeder (Error) | Freight feeder targets GL account `Freight`, which does not exist (same bug as 2c) |
| General Ledger 67 `Base Amount` | UnfedRule | Planning feeders are described in comments but never written |
| General Ledger 110 `Factor` | UnfedRule | Same |
| Employee 77 `Enter Full Time Base Salary` / `Year_Enter` | UnfedRule | Its feeder exists but is commented out |

### W4. Conditional feeder targets
**What:** all three unfed rows carried the note "feeders with a dynamic target cube were
not checked". The only dynamic feeder was Employee's `DB(IF(<condition>, 'Employee', ''),
...)`, which can never reach the General Ledger.
**Fix:** `candidate_cubes()` resolves a target cube written as `IF(...)` whose branches are
all text (nested IFs too; `''` = feed nothing) and checks the feeder against each named
cube. Only genuinely unknowable targets (e.g. `ATTRS(...)`) are counted as dynamic.
**Expected result on dev:** dynamic feeder targets 1 → 0; the note disappears; the Employee
feeder is now checked, so `FTE` is confirmed as fed.
**Lesson:** a static checker can still evaluate the *shape* of an expression – a
conditional between literals has a known, finite set of results.
