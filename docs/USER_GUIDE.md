# TM1 Data Dictionary — User Guide

`tm1dd` reads your TM1 TI processes and builds a **queryable lineage dictionary** inside
native `}Meta_*` cubes — plus an interactive **data-flow map** you can open in a browser.
It answers questions like *"which processes write to this cube?"*, *"what does this process
trigger?"*, and *"what breaks if I change this process?"*.

This guide assumes you've completed the **Installation Guide** (venv active, `config.yaml`
set up, `check_environment.py` all green).

---

## 1. Concepts in 60 seconds

- **Static analysis:** `tm1dd` reads TI *source code* — it never runs your processes and
  never changes business data. It only creates/writes the `}Meta_*` dictionary objects.
- **`}Meta_*` cubes:** the dictionary lives in TM1 control cubes you can slice in PAfE:
  - `}Meta_Process_Cube` — which processes **read/write** each cube.
  - `}Meta_Process_Chain` — which processes **trigger** which (dependencies).
  - `}Meta_Extraction_Audit` — a log of extraction runs.
- **Exclusions:** framework/system processes (Pulse `}APQ.*`, planning `}tp_*`, Bedrock,
  anything `}`-prefixed, plus `test`/`temp` work) are skipped by default, so the dictionary
  stays focused on *business* processes.
- **Dry-run:** set `run.dry_run: true` in `config.yaml` to parse and report **without
  writing anything**. Great for a first, safe look.

---

## 2. Everyday workflow

```powershell
# once per session: activate the venv (VS Code often does this for you)
.venv\Scripts\Activate.ps1

# ensure the schema exists (safe to run anytime - idempotent)
tm1dd bootstrap

# parse all business processes and (re)populate the lineage cubes
tm1dd extract

# see the whole model as an interactive diagram
tm1dd export-graph --out data_flow.html
```

`tm1dd extract` does a **full clear-and-reload** of the lineage cubes, so re-running it
gives a fresh, accurate picture after model changes.

---

## 3. Command reference

Run `tm1dd --help` for the list, or `tm1dd <command> --help` for options. All commands
accept `--config PATH` (defaults to `config.yaml`).

### Setup / connection
| Command | What it does |
|---|---|
| `tm1dd set-credential` | Store the TM1 password securely in the OS keyring. |
| `tm1dd bootstrap` | Create the `}Meta_*` dimensions and cubes (idempotent). |

### Inspecting a single process (read-only)
| Command | What it does |
|---|---|
| `tm1dd list-processes [--contains TEXT]` | List process names (optionally filtered). |
| `tm1dd inspect-process "NAME"` | Show a process's datasource, variables, parameters, block sizes. |
| `tm1dd show-vars "NAME" [--all-assignments]` | The variable dictionary — where each variable's value comes from. |
| `tm1dd extract-refs "NAME"` | Print the raw lineage (every cube read/write, chain, etc.) with resolved targets. |

### Whole-model extraction (writes to TM1)
| Command | What it does |
|---|---|
| `tm1dd extract` | Parse all included processes; write cube **and** chain lineage. |
| `tm1dd extract-cube "NAME"` | Write just one process's cube lineage (spot check). |
| `tm1dd extract-chain "NAME"` | Write just one process's chain lineage (spot check). |
| `tm1dd record-run` | Write one row into `}Meta_Extraction_Audit` (used by extract). |

### Diagnostics (read-only)
| Command | What it does |
|---|---|
| `tm1dd diagnose-unresolved` | Top "unresolved" cube-target expressions, by frequency. |
| `tm1dd diagnose-unresolved --process "NAME"` | One process's unresolved references, with line numbers. |
| `tm1dd diagnose-unresolved --expression="EXPR"` | Every process/line where EXPR is the unresolved target (use `--expression=""` for blanks). |

### Visualisation (read-only)
| Command | What it does |
|---|---|
| `tm1dd export-graph --out FILE.html` | Self-contained interactive data-flow map. |

---

## 4. Reading the dictionary in Planning Analytics

Open your model in **Architect / PAW / Arc** and enable **Display Control Objects** (so the
`}`-prefixed cubes are visible).

### "Which processes write to / read from a cube?"  → `}Meta_Process_Cube`
Dimensions: `}Meta_Process` × `}Meta_Cube` × `}Meta_Role` × `}Meta_ProcessCubeMeasure`.
- Put **`}Meta_Cube`** on the rows, **`}Meta_Process`** on the columns.
- Filter `}Meta_Role` to `CubeWrite` to see *"who writes to this cube?"* (or `CubeRead`).
- The `Count` measure shows how many references; `FirstBlock`/`FirstLine` locate the first one.

### "What does a process trigger? What triggers it?"  → `}Meta_Process_Chain`
Dimensions: `}Meta_Process` (caller) × `}Meta_Process_Callee` (callee) × measure.
- **Downstream** ("what does X trigger?"): `}Meta_Process` (caller) on rows.
- **Upstream / impact** ("what triggers X? what breaks if I retire X?"):
  `}Meta_Process_Callee` on rows, `}Meta_Process` on columns.

### "When did extraction last run?"  → `}Meta_Extraction_Audit`
One row per run: extractor version, start/end time, duration, status.

---

## 5. The interactive data-flow map

### 5.1 Generate it (simplest — online-capable machine)

```powershell
tm1dd export-graph --out data_flow.html --title "My Model — Data Flow"
```

Open `data_flow.html` in any browser. You get a clickable diagram:

- **Blue ellipses = processes**, **orange boxes = cubes**.
- **Green edges = writes** (process → cube), **purple dashed = reads** (cube → process),
  **red = triggers** (process → process). Edge numbers show reference counts.
- **Click a node** to fade everything except it and its direct neighbours (trace flow).
  **Reset highlight** to clear. **Fit** to re-centre.
- **Search** a process or cube name and press Enter to jump to it.

In this default mode the page fetches the vis-network graph library from a CDN the first
time it's opened (then the browser caches it). This is fine on a machine with internet.
**You do NOT need the `--vis-js` option for this.**

### 5.2 Make it fully offline (for locked-down sites / emailing)

If the file must open on a machine with **no internet at all**, inline the graph library so
nothing is fetched. This is a **two-step, one-time** setup.

**Step 1 — download the library file once (on any machine with internet):**

Open this URL in a browser:

```
https://unpkg.com/vis-network/standalone/umd/vis-network.min.js
```

Your browser will show a wall of minified JavaScript. Save it to disk:
- **Right-click the page → "Save as…"**, or press **Ctrl+S**.
- Save it as **`vis-network.min.js`** somewhere easy, e.g. into your project folder:
  `C:\TM1_Models\tm1-data-dictionary\vis-network.min.js`
- Make sure the saved file name ends in **`.js`** (not `.txt`). It should be roughly
  0.5–1 MB.

> Alternative if the browser fights you: in PowerShell you can download it directly —
> ```powershell
> Invoke-WebRequest -Uri "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js" -OutFile "vis-network.min.js"
> ```
> (Run this from your project folder; it saves `vis-network.min.js` there.)

**Step 2 — pass that real file path to `--vis-js`:**

Use the **actual path** to the file you just saved. If you saved it in the project folder
and you're running from there, it's simply:

```powershell
tm1dd export-graph --out data_flow.html --vis-js vis-network.min.js
```

Or with a full path:

```powershell
tm1dd export-graph --out data_flow.html --vis-js C:\TM1_Models\tm1-data-dictionary\vis-network.min.js
```

> **Important:** `--vis-js` must point to a file that **actually exists**. In earlier docs
> the example showed `path\to\vis-network.min.js` — that was a *placeholder*, not a real
> path. Replace it with wherever you saved the file. If you pass a path that doesn't exist,
> you'll get `FileNotFoundError: ... vis-network.min.js`.

The resulting `data_flow.html` now has the library embedded — it opens with **no internet
required** and can be emailed or copied to any offline machine.

### 5.3 Which mode should I use?
- **On your own machine (has internet):** use **5.1** (no `--vis-js`). Simplest.
- **Delivering to a locked-down client / offline server:** do **5.2** once and ship the
  fully-offline file.

---

## 6. Understanding "unresolved" references

Some cube/process targets are chosen at **runtime** — e.g. a generic utility that writes to
a cube passed as a parameter (`pCubeName`). Static analysis genuinely cannot resolve those
to a concrete name, so they are counted as *unresolved* and not written to the dictionary
(the tool never guesses a wrong cube). This is expected and correct.

Use `tm1dd diagnose-unresolved` to see what's behind the count. If most are `p`-prefixed
parameters, that's the honest limit of static analysis for those utility processes.

---

## 7. Tips & good practice

- **Start with a dry run.** Set `run.dry_run: true`, run `tm1dd extract`, read the summary,
  then set it back to `false` when you're happy.
- **Re-run after model changes.** `tm1dd extract` is a full refresh; run it whenever
  processes change to keep the dictionary current. Consider scheduling it (nightly).
- **Force-include a `}`-prefixed business process** (rare) by adding it to the exclusion
  rules' `explicit_include` list.
- **Spot-check a process** with `inspect-process` / `extract-refs` / `show-vars` before
  trusting an odd result — these are read-only and show exactly what the parser sees.
- **The tool is read-only against your business model.** It only ever writes `}Meta_*`
  objects; dry-run blocks even those.

---

## 8. Typical first-day-on-a-new-model recipe

1. `tm1dd export-graph --out flow.html` — open it and get the shape of the model at a glance.
2. Find the cube you care about in the map (or in `}Meta_Process_Cube`) — see what feeds it.
3. `tm1dd inspect-process "TheLoader"` — see its datasource (where the data comes from).
4. In `}Meta_Process_Chain`, put the loader on the callee axis — see what triggers it (the
   chore/parent), and on the caller axis — see what it triggers next.
5. `tm1dd show-vars "TheLoader"` — understand its internal logic and derived values.

That takes you from "I've never seen this model" to "I understand this data flow" in minutes.

---

## 9. Troubleshooting the map

| Symptom | Cause / fix |
|---|---|
| `FileNotFoundError: ... vis-network.min.js` | You passed `--vis-js` with a path that doesn't exist (or the literal placeholder `path\to\...`). Either omit `--vis-js` (section 5.1), or download the file first and point to its real path (section 5.2). |
| Blank page / "vis is not defined" when offline | You used the default (CDN) mode on a machine with no internet. Regenerate with `--vis-js` pointing at a downloaded `vis-network.min.js` (section 5.2). |
| The map is very dense / hard to read | Expected for large models — use **Search** to jump to a node, **click** it to highlight just its neighbours, and **Reset highlight** to clear. Filtering/grouping options can be added in a later version. |
| Nothing to show / very few nodes | Run `tm1dd extract` first isn't required (the map re-parses), but check your exclusion rules aren't hiding everything, and that `check_environment.py` passes. |
