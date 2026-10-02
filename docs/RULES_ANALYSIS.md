# Rules analysis (`tm1dd extract-rules`)

`tm1dd extract-rules` reads the rule text of every included cube and records what the
rules and feeders do: which cubes are rule-driven, which other cubes they read from or
feed, which elements they name, and which functions they use. Its main purpose is to let
an administrator answer *"is it safe to change this?"* before renaming an element,
restructuring a hierarchy or retiring a cube.

TI processes are covered separately, by `tm1dd extract` – see [TI lineage](TI_LINEAGE.md).
The two commands are independent; a failure in one never affects the other.

```powershell
tm1dd extract-rules --env dev
tm1dd extract-rules --env dev --quiet
```

---

## Phases

| Phase | Cube | Question it answers |
|---|---|---|
| 2a | `}Meta_Rule_Cube` | Does this cube have rules and feeders? Which pragmas are set? How big are the rules? |
| 2b | `}Meta_Rule_Dependency` | Which cubes do this cube's rules read from or feed into? Do they all exist? |
| 2c | `}Meta_Rule_Element_Reference` | Which rules and feeders name this element? Does every named element exist? |
| 2d | `}Meta_Rule_Function` | Which functions do this cube's rules use (hierarchy, attribute, lookup ...)? |
| 2e | `}Meta_Rule_Feeder_Finding` | Which rules are not fed? Which feeders feed nothing, or feed cells no rule calculates? |

All five cubes are rebuilt from one read of each cube's rule text. Full measure lists are
in the [schema reference](SCHEMA_REFERENCE.md#rules-cubes).

---

## How rule text is parsed

TM1py's own rule handling upper-cases statements and only skips comment lines that start
with `#`, which is fine for counts but not for exact element names. `tm1dd` therefore has
its own parser, which:

- keeps original case and line numbers;
- strips `#` comments anywhere on a line, but never inside a quoted string;
- handles `''` (escaped quote) inside strings;
- splits the text at `FEEDERS;` into rule and feeder sections;
- splits each rule into **area**, **qualifier** (`N:`, `C:`, `S:`) and **expression**;
- splits each feeder into **source area** and **targets**.

A statement whose shape is not recognised is counted as *malformed* and still scanned for
`DB()` calls and functions, so nothing is silently lost.

---

## 2a – Rule facts: `}Meta_Rule_Cube`

One row for **every** included cube, including cubes with no rules (`HasRules = No` is
itself useful).

| Measure | Meaning |
|---|---|
| `HasRules`, `HasFeeders` | `Yes`/`No` |
| `SkipCheck`, `FeedStrings`, `UndefVals` | Pragma present, `Yes`/`No` |
| `RuleStatementCount`, `FeederStatementCount` | Size of each section |
| `DimensionCount` | Number of dimensions on the cube |

Statement counts come from TM1py and include indented commented-out lines, so treat them
as a size gauge rather than an exact count.

---

## 2b – Cube dependencies: `}Meta_Rule_Dependency`

Every `DB()` call in rules and feeders, one row per (cube, related cube, type):

| Type | Meaning |
|---|---|
| `RuleRead` | A rule reads the related cube |
| `FeederTarget` | A feeder feeds into the related cube (`=> DB(...)`) |
| `FeederLookup` | A `DB()` used *inside* a feeder to look something up |

`RelatedCubeExists = No` marks a **dangling** reference: the rule names a cube that is not
in the instance. Related cubes are matched against every cube, including control cubes, so
a rule reading a `}`-cube is not reported as dangling.

A `DB()` whose cube name is an expression (for example `DB(IF(...), ...)`) cannot be tied
to a cube; it is counted as *unresolved* in the summary and not written.

---

## 2c – Element references: `}Meta_Rule_Element_Reference`

Every literal element name in rules and feeders, resolved to its dimension.

| Reference type | Where the element appears |
|---|---|
| `Area` | Left-hand side of a rule: `['Local','Salaries'] = ...` |
| `RuleReference` | Same-cube reference in an expression: `['Amount','Corporate']` |
| `FeederSource` | Left-hand side of a feeder |
| `FeederTarget` | A feeder target, `[...]` or the arguments of a target `DB()` |
| `DBArgument` | A literal argument of any other `DB()` |
| `Comparison` | `!Dim @= 'Element'` or `!Dim @<> 'Element'` – not a cell reference, but renaming the element changes the rule's result |

Sets (`{'Actual','Budget'}`) and dimension-qualified items (`'Region':'Europe'`) are
expanded. Expressions such as `!Version`, `ATTRS(...)` or a nested `DB()` are skipped:
the tool never guesses.

### Which dimension

| Reference | How the dimension is found |
|---|---|
| `DB()` argument | By position, using the referenced cube's dimension order |
| `'Dim':'Element'` or `!Dim @= 'Element'` | The named dimension |
| Plain area or `[...]` item | Looked up in every dimension of the cube |

For a plain item, exactly one match gives the dimension. More than one match is placed
under `(Ambiguous)`, with the candidate dimensions in `Candidates` – never guessed. No
match is placed under `(Unknown)`.

### ElementExists

| Value | Meaning |
|---|---|
| `Yes` | Found, by principal name or alias. The row is filed under the principal name; `WrittenAs` shows how the rule wrote it |
| `No` | Not in the dimension. The rule or feeder refers to nothing – a real defect |
| `Unknown` | Could not be checked, because the referenced cube does not exist or a dimension could not be read |

### How elements are looked up

Rules may name an element by any alias, in any case and spacing. To avoid false
"missing element" results, lookup is layered:

1. **Principal names** – read once per dimension and cached.
2. **Aliases** – all alias values for a dimension in one REST call
   (`Elements?$select=Name,Attributes`). This does not build MDX from element names, so
   names containing quotes, brackets or backslashes cannot break it. If that call fails,
   each alias is read separately, so one bad alias does not hide the others.
3. **Ask TM1** – an element still not found is resolved with a one-member MDX set, which
   TM1 resolves exactly as the rule engine does. Answers are cached and capped at 500
   queries per run.

Problems are reported, not hidden: *Aliases not readable* lists each alias that failed and
why, and *Elements resolved by TM1 lookup* shows how often step 3 was needed. On a healthy
model that count should be 0.

### Using it

- **Before renaming or deleting an element:** filter the element. No rows means no rule or
  feeder names it. (TI processes are not covered here; check `}Meta_Process_Dimension` and
  the process code as well.)
- **Finding broken references:** filter `ElementExists = No`. A feeder target that does
  not exist feeds nothing, so values may silently disappear once other feeders change.

---

## 2d – Function usage: `}Meta_Rule_Function`

Every function call and keyword in rules and feeders, one row per (cube, function). There
is no watch list: the rules language is small, so everything is recorded. `}Meta_Function`
is shared with TI function usage, so the same function lines up across both.

Detected:

- **Calls** – a name followed by `(`, e.g. `ATTRS(`, `IF(`, `DB(`.
- **Keywords** – `STET`, `CONTINUE` and `ISLEAF`, used without brackets.

Text inside quotes, commented-out code and `!Dim` references are never counted.

| Category | Examples | Why it matters |
|---|---|---|
| Lookup | `DB` | Cross-cube dependency (detailed in 2b) |
| Attribute | `ATTRS`, `ATTRN` | Breaks if the attribute is renamed or deleted |
| Hierarchy | `ELPAR`, `ELISANC`, `ELLEV`, `DIMNM`, `ISLEAF` | Changes behaviour when a hierarchy is restructured, even if no element is renamed |
| Logic | `IF`, `ISUND` | |
| Control | `STET`, `CONTINUE` | Cells left to input or to a later rule |
| Text, Date, Math | `SUBST`, `DAYNO`, `ROUND` | |
| Other | anything not in the catalogue | Still recorded |

Measures: `Count`, `RuleCount`, `FeederCount`, `Category`, `FirstLine`, `FirstStatement`,
and `Lines` (every statement line using it).

---

## 2e – Feeder gaps: `}Meta_Rule_Feeder_Finding`

With SKIPCHECK, TM1 only calculates a rule cell if it is *fed*. A missing feeder makes
values silently disappear; a feeder aimed at the wrong place, or at cells no rule
calculates, wastes memory and slows the server (over-feeding). Phase 2e compares every
rule's area with every feeder's target, across cubes.

| Finding | Severity | Meaning | What to do |
|---|---|---|---|
| `UnfedRule` | Warning | A leaf-level rule in a SKIPCHECK cube that no feeder statement can reach | Add a feeder, or confirm the cells are fed some other way |
| `DeadFeeder` | Error | A feeder target names an element or cube that does not exist, so it feeds nothing | Fix the element or cube name |
| `FeederFeedsNoRule` | Warning | A feeder target overlaps no rule in the target cube | Remove the feeder or point it at the rule it was meant for |
| `FeedersWithoutSkipCheck` | Info | The cube has feeders but no SKIPCHECK, so they do nothing | Add SKIPCHECK or remove the feeders |
| `UncheckedRule` | Info | The rule's area names an element that could not be resolved | See `}Meta_Rule_Element_Reference` for that rule |

One row per (cube, statement, finding type). `}Meta_RuleStatement` holds keys such as
`Line 00057`, so rows sort in rule-text order; cube-level findings use `Cube`. Measures:
`Count`, `Severity`, `Section`, `Line`, `Statement`, `Detail` (which target or element) and
`RelatedCube`.

### How the comparison works

- An area becomes "these elements in these dimensions"; dimensions not named are
  unrestricted. Elements are resolved with the same lookup as 2c, so aliases work.
- A feeder target `[...]` keeps the source area and replaces only the dimensions it names:
  `['Local','Salaries'] => ['Payroll Taxes']` feeds Local / Payroll Taxes.
- A target `DB('Cube', ...)` restricts each dimension whose argument is a literal. `!Dim`
  and expressions are unrestricted.
- Two areas overlap if, in every dimension both restrict, some pair of elements is the same
  or one is an ancestor of the other. Feeding a consolidation feeds every leaf beneath it.
  Each dimension's hierarchy is read once per run.

### What is not reported

- Rules with `C:` or `S:`, rules that are just `STET`, and unqualified rules on
  consolidations only – these do not need feeders.
- Rules in cubes without SKIPCHECK – nothing needs feeding.
- Feeders into a cube that was not read (excluded), and feeders whose target cube is an
  expression. The latter are counted in the summary, and `UnfedRule` details say so when any
  exist, because such a feeder may be the one that feeds the rule.

### Reading the results

`UnfedRule` means no feeder *statement* can reach the area – not that every cell is empty.
Input cells and cells fed by a dynamic-target feeder still show. Treat it as "check this",
and `DeadFeeder` as "fix this". Elements that cannot be placed are treated as unrestricted,
so the check only reports clear gaps.

---

## Exclusions

By default only control cubes (`}*`) are excluded – real business cubes are never assumed
to be noise. Exclusions are set in `rule_exclusions.py` and support glob patterns,
substrings, an explicit exclude list and an explicit include list (which always wins).
Cubes referenced by rules are still checked even when excluded, so a rule reading an
excluded control cube resolves correctly.

---

## Run summary

| Line | Meaning |
|---|---|
| Cubes: total / included / excluded | After exclusions |
| Read OK / failed | Failed cubes are listed; the run continues |
| Rule-cube, rule-dependency, element-reference, rule-function rows | Rows written to each cube |
| Cubes with rules / feeders / SKIPCHECK | From 2a |
| DB() references, unresolved, dangling | From 2b |
| Element references; missing / ambiguous / not checked | From 2c |
| Dimensions read; elements resolved by TM1 lookup | Element lookup health |
| Function uses (distinct functions); cubes using hierarchy functions | From 2d |
| Feeder check: rules, feeders; findings by type; dynamic-target feeders | From 2e |
| Hierarchies read; hierarchies not readable | Feeder-check health |
| Aliases not readable | Only shown when an alias read failed |
| Malformed statements | Only shown when a statement's shape was not recognised |

The same counts are stored in `}Meta_Extraction_Audit` for each run.

---

## Known limits

- Only the default hierarchy of each dimension is read. An element that exists only in an
  alternate hierarchy is reported as missing.
- Rule areas that use `CONTINUE` chains or overlapping areas are recorded as written; the
  tool does not work out which rule wins for a given cell.
- Feeder checks are static: they cannot see cell values, conditional feeders' runtime
  behaviour, or which rule wins where areas overlap.
