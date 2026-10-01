# Demo script — tm1dd for colleagues (about 25 minutes)

Private: uses the dev demo model. Run the preparation the day before.

## Preparation

```powershell
pytest -q
tm1dd bootstrap --env dev --drop-legacy
tm1dd extract --env dev
tm1dd extract-rules --env dev
tm1dd create-views --env dev
tm1dd export-graph --env dev --out data_flow.html --title "Demo model data flow"
```

In PAfE or Architect, turn on **Display Control Objects** so the `}Meta_*` cubes show.
Keep `data_flow.html`, PAfE and one PowerShell window open.

## 1. The problem (2 min)
"You inherit a model with hundreds of processes and rule-driven cubes. Which process loads
this cube? What breaks if I rename this element? Today the answer is reading code."

## 2. One command per area (3 min)
Run `tm1dd extract-rules --env dev --quiet` live. Point out:
- it reads, never runs, anything;
- the summary: cubes with rules, `DB()` references, element references, missing elements;
- the run is recorded in `}Meta_Extraction_Audit` (open `tm1dd All` on it).

## 3. TI lineage (6 min)
1. `}Meta_Process_Cube` → **tm1dd Cube Writers**: pick a cube, see which processes load it.
2. `}Meta_Process_Datasource` → **tm1dd File Loaders**: where data enters.
3. `}Meta_Process_Chain` → **tm1dd All**: put a process on the callee axis – "what
   triggers this, and what breaks if I retire it?"
4. `}Meta_Process_Chore` → **tm1dd All**: what runs on a schedule, in which order.
5. Open `data_flow.html`; pick a cube in the dropdown and follow it back to its file.

## 4. Rules analysis (8 min)
1. `}Meta_Rule_Cube` → **tm1dd SkipCheck Cubes**: which cubes depend on feeders.
2. `}Meta_Rule_Dependency` → **tm1dd All**: cube-to-cube dependencies from `DB()`.
   **tm1dd Dangling Cubes** shows a rule reading a cube that does not exist.
3. `}Meta_Rule_Element_Reference` → **tm1dd All**, filter `}Meta_Element` to `Salaries`:
   "every rule and feeder that names Salaries – this is the rename check".
4. **tm1dd Broken References** – the headline finding: a Retail feeder targets account
   `Freight` in the General Ledger, which does not exist. Show line 96 in the Retail
   rules. The feeder feeds nothing; values only appear because another feeder covers it.
5. Point out `WrittenAs`: rules can use aliases; the tool still finds the element.
6. `}Meta_Rule_Function` → **tm1dd Hierarchy Functions**: rules affected by a hierarchy
   restructure even when no element is renamed.

## 5. How it is built (3 min)
Static parsing in Python + TM1py; results in native cubes; offline install as a wheel; no
passwords stored (OS keyring); dry-run mode; tests for every module.

## 6. What's next (3 min)
Feeder-gap detection (rules with no feeder, feeders that feed nothing), a combined findings
view, and following parameters from caller to called process. Ask for ideas.

## Likely questions
- **Does it change anything in the model?** Only the `}Meta_*` cubes. Dry-run writes nothing.
- **How long does it take?** Seconds to a few minutes; it reads metadata only.
- **Does it run processes?** Never.
- **Can it see values flowing at run time?** No – that is a static-analysis limit; parameters
  chosen at run time are listed as unresolved, not guessed.
