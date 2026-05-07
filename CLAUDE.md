# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Commands

```bash
npm run dev       # start Vite dev server at http://localhost:5173
npm run build     # production build → dist/
npm run preview   # serve dist/ locally to verify the production build
```

No test runner, no linter config beyond the default Vite ESLint scaffold (`eslint.config.js`).

---

## What Has Been Implemented

This is a ReactJS recreation of an NSE NIFTY Option Chain Excel workbook (`NSE Option Chain.xlsx`). The source Excel has three sheets; only **Option Chain** (the main view) has been implemented. The other two sheets (lookup, OC Profile) are implemented as pure JS calculations.

### Data Shape

`src/data/dummyData.js` exports `OPTION_CHAIN_DATA`:
```
{ meta: { symbol, expiry, spotPrice, lotSize, timestamp }, rows: [...41 items] }
```
Each row: `{ strike, call: { oi, chngOI, pctChngOI, volume, iv, ltp, chngLTP, pctChngLTP, buyQty, sellQty, bidQty, bid, askQty, ask }, put: { same fields } }`

41 rows, strikes 22500–26500 at 100-point intervals. ATM = index 20 (strike 24500). All values are deterministic (no `Math.random()`). Key landmarks: peak call OI at 25000 (index 25), peak put OI at 24000 (index 15).

### Excel → Code Mapping

**Row index zones (critical for conditional formatting):**
- Indices 0–20 (Excel rows 12–32): call side gets dark navy bg `#1F497D`; put side is white — these are ITM calls / OTM puts
- Indices 21–40 (Excel rows 33–52): call side is white; put side gets `#1F497D` — OTM calls / ITM puts
- ATM row (index 20): amber `#FBBF24` strike cell + `atmRow` CSS class
- Strike column always: `#4F81BD` (steel blue)

**Summary formula index ranges** (`src/utils/calculations.js`):
- `totalCallOI / totalPutOI` → `rows.slice(11, 31)` (Excel `SUM(A23:A42)`)
- `nearCallChngOI / nearPutChngOI` → `rows.slice(18, 24)` (Excel `SUM(B30:B35)`)
- `pcr` = totalPutOI / totalCallOI

**OC Profile formula index ranges** (`computeOCProfile`):
- OTM calls → `rows.slice(atmIndex + 1)` (Excel rows 33–52)
- OTM puts → `rows.slice(0, atmIndex)` (Excel rows 12–32)
- `resistance[i]` = `MIN` of i-th largest strike across call OI / CHNG OI / VOL
- `support[i]` = `MAX` of i-th largest strike across put OI / CHNG OI / VOL

**% CHNG OI color scale** (`PCT_CHNG_OI_ROW_MIN = 10`, `PCT_CHNG_OI_ROW_MAX = 30`): color scale is computed over `rows[10..30]`, applied only to rows within that same range.

### Conditional Formatting Colors (from Excel theme)

| Purpose | Colors |
|---|---|
| OTM call top-3 OI/ChngOI/Vol | `#D99695` / `#E6B9B8` / `#F2DCDB` (rank 1→3, Excel theme5 tints 0.4/0.6/0.8) |
| OTM put top-3 OI/ChngOI/Vol | `#C3D79B` / `#D7E4BD` / `#EBF1DE` (theme6 tints) |
| IV > 16% | bg `#F2DCDB`, text `#9C0006` |
| PCR > 1.1 / 0.9–1.1 / < 0.9 | `#C6EFCE`/`#006100` · `#FFEB9C`/`#9C5700` · `#FFC7CE`/`#9C0006` |
| Ratio ≤ 0.75 / 0.83 / 0.93 / > 0.93 | green · amber · light-red · red |

Conditional formatting **overrides** base row color via `mergeStyle(base, override)` in `StrikeRow.jsx` — override is spread after base, so it wins.

### Component Tree

```
App
├── Header          ← meta display only (no state)
├── SummaryPanel    ← computeSummary + computeOCProfile results, both memoized in App
│   └── SideSummary (×2, shared component for CALL and PUT sides)
└── OptionChainTable
    ├── TableHeader ← static two-level header
    └── StrikeRow (×41) ← all formatting logic lives here, zero state
```

`OCProfilePanel` component exists at `src/components/OCProfilePanel/` but is **not mounted** — it was removed from App.jsx per user request (values are highlighted in-chain). The folder was intentionally left untracked in git.

### Key Utility Functions

```
calculations.js
  computeSummary(rows, spotPrice)    → { totalCallOI, totalPutOI, nearCallChngOI, nearPutChngOI, totalCallVol, totalPutVol, totalCallChngOI, totalPutChngOI, pcr, atmIndex }
  computeOCProfile(rows, spotPrice)  → { callTop3ByOI, callTop3ByChngOI, callTop3ByVol, putTop3ByOI, ..., resistance[3], support[3], callOIRatios, callChngOIRatios, callVolRatios, putOIRatios, ..., callOIDiff, callVolDiff, putOIDiff, putVolDiff }
  getTop3ByField(dataSlice, fieldFn) → sorted top-3 row objects
  formatIndian(num)                  → en-IN locale string (lakhs)
  formatIV / formatLTP / formatPct   → display formatters

colorUtils.js
  getTop3Color(scheme, rank)         → { background, color, fontWeight } | null  — scheme = 'call' | 'put'
  getIVStyle(value)                  → style object | null
  getPCRStyle(pcr)                   → style object (3 tiers)
  getRatioStyle(ratio)               → style object (4 tiers)
  getPctChngOIColorScale(value, min, max) → 3-color linear interpolation (green→yellow→red)
```

---

## Pending Work — Python Packaging (`optionchainui` CLI)

### Goal
Package the React app as a Python module so that `pip install optionchainui` gives users an `optionchainui` CLI command that launches the UI. npm must be available on the target system (Model B, source + `npm run dev`).

### Recommended Approach: Model A (pre-built, no npm required at runtime)
Run `npm run build` before publishing. Ship only `dist/` as Python package data. The CLI serves `dist/` via Python's stdlib `http.server` and opens the browser. No npm needed at install or runtime.

### Alternative: Model B (source + npm, as originally requested)
Ship full React source (without `node_modules/`). CLI checks for npm, runs `npm install` on first launch, then `npm run dev`. Slower cold start (~50 MB npm install on first run).

### TODO List — Implementation Steps

**Step 1 — Python package scaffold**
- Create `optionchainui/` directory at repo root (sibling of `option-chain-ui/`)
- Add `optionchainui/__init__.py` (empty or version string)
- Add `optionchainui/cli.py` with `main()` entry point

**Step 2 — Bundle React assets**
- Model A: run `npm run build` in `option-chain-ui/`, copy `dist/` → `optionchainui/dist/`
- Model B: copy entire `option-chain-ui/` source (excluding `node_modules/`, `dist/`) → `optionchainui/ui/`

**Step 3 — CLI logic in `optionchainui/cli.py`**
- Use `importlib.resources` (Python ≥ 3.9) to resolve the bundled path at runtime — avoids hardcoded filesystem paths
- Port selection: try 5173, fall back to any free port via `socket`
- **Model A path:** start `http.server.HTTPServer` in a daemon `threading.Thread`, call `webbrowser.open()`, block on `input()` or `signal`
- **Model B path:**
  - `shutil.which('npm')` — exit with clear message if absent
  - Check if `node_modules/` exists in the unpacked ui dir; if not, run `subprocess.run(['npm', 'install'], cwd=ui_path)`
  - `subprocess.Popen(['npm', 'run', 'dev', '--', '--port', str(port)], cwd=ui_path, stdout=PIPE)`
  - Poll stdout for Vite's `Local:` line to know when server is ready, then `webbrowser.open()`
  - `process.wait()` to keep alive

**Step 4 — `pyproject.toml` at repo root**
```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "optionchainui"
version = "1.0.0"
requires-python = ">=3.9"
dependencies = []

[project.scripts]
optionchainui = "optionchainui.cli:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["optionchainui*"]

[tool.setuptools.package-data]
optionchainui = ["dist/**/*"]          # Model A
# optionchainui = ["ui/**/*"]          # Model B
```

**Step 5 — `MANIFEST.in`** (ensures sdist also includes assets)
```
recursive-include optionchainui/dist *    # Model A
recursive-exclude optionchainui/ui/node_modules *  # Model B
```

**Step 6 — `.gitignore` additions**
```
optionchainui/dist/          # re-generated from npm run build
optionchainui/ui/node_modules/
```

**Step 7 — Build & test the wheel**
```bash
pip install build
python -m build          # produces dist/*.whl and dist/*.tar.gz
pip install dist/*.whl
optionchainui            # should open browser
```

**Step 8 — CI pre-publish hook (Model A only)**
In whatever CI/CD pipeline is used, add a step before `python -m build`:
```bash
cd option-chain-ui && npm ci && npm run build
cp -r dist ../optionchainui/dist
```

---

## Agent History & Migration Notes

### Agents Used in This Session (in order)

| # | Role | What it did |
|---|---|---|
| Agent 1 | **Code Writer** (`general-purpose`) | Created entire project from scratch: all 24 files, dummyData with 41 rows, all components, utils, CSS modules, README. Ran `npm install` and verified build. |
| Agent 2 | **Code Reviewer** (`general-purpose`) | Read all source files, identified DRY violations and production issues. Applied 12 targeted fixes: merged duplicate color functions, extracted `computeRatios`/`computeDiffs` helpers, deduplicated `getRank()`, merged `SideSummary`, added `useMemo` in App and OptionChainTable, removed dead CSS. |
| Agent 3 | **Tester** (`general-purpose`) | Validated all calculations against Excel spec, checked data landmarks, verified color function outputs, found and fixed 2 bugs (unused import in StrikeRow, incorrect peak put OI at index 15). Confirmed final build with zero errors. |

### What Was Done After Agents (manually by Claude Code)

1. Removed `OCProfilePanel` from `App.jsx` (user request — panel removed, component folder left untracked)
2. Rewrote `SummaryPanel.jsx` + `SummaryPanel.module.css` — replaced verbose ratio/diff rows with compact stat blocks matching PCR card style (`StatBlock` component, 7 blocks per side)
3. Initialized git repo, staged 24 files, created detailed initial commit `288c586`

### Agents Required for Python Packaging (next phase)

| # | Role | Task |
|---|---|---|
| Agent 4 | **Code Writer** | Implement Steps 1–6 above: scaffold `optionchainui/` Python package, write `cli.py` with chosen model (A or B), write `pyproject.toml`, `MANIFEST.in`, update `.gitignore` |
| Agent 5 | **Reviewer** | Review `cli.py` for cross-platform path handling (Windows `Scripts/` vs Unix `bin/`), port-collision edge cases, subprocess stderr handling, and `importlib.resources` correctness for Python 3.9–3.12 |
| Agent 6 | **Tester** | Run `python -m build`, install the wheel in a fresh venv, invoke `optionchainui`, verify browser opens, verify the app loads correctly; test npm-not-found error path |

### Context for the Next Claude Instance

- The React app is **complete and committed** at `option-chain-ui/` (git root)
- The Python packaging work starts from **scratch** — no `pyproject.toml` exists yet
- Model A (pre-built + `http.server`) is recommended; confirm choice with user before Agent 4 starts
- The `OCProfilePanel` component (`src/components/OCProfilePanel/`) exists on disk but is intentionally excluded from git and not mounted — do not re-add it unless the user asks
- `src/App.css` and `src/assets/` are Vite scaffold remnants — unused, untracked, do not commit
- The dummy data landmark values that matter: index 15 (strike 24000) = peak put OI, index 25 (strike 25000) = peak call OI, index 20 (strike 24500) = ATM
