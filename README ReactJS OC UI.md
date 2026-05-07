# NSE Option Chain — ReactJS Interface

A pixel-faithful ReactJS recreation of an NSE NIFTY Option Chain Excel dashboard. The UI replicates all conditional formatting, color coding, summary panels, and OC Profile resistance/support calculations from the source Excel workbook.

## Features

- **41-strike option chain table** (NIFTY 22500–26500, 100-point intervals)
- **Full Excel conditional formatting** reproduced in React:
  - Top-3 OI / CHNG OI / VOLUME highlighting for OTM calls (red gradient) and OTM puts (green gradient)
  - IV > 16% cell highlight (#F2DCDB / #9C0006)
  - % CHNG IN OI 3-color scale (green/yellow/red) for near-ATM rows
  - PCR coloring: bullish (green) / neutral (amber) / bearish (red)
  - Summary ratio 4-tier color scale (green = dominant, red = even/unclear)
- **ITM/OTM row background colors** — dark navy (#1F497D) for ITM, white for OTM, steel blue (#4F81BD) strike column
- **ATM row** visually highlighted with amber outline and background tint
- **Summary Panel** — Call/Put totals, near-ATM CHNG OI, total volume, ratio analysis, OI/VOL diffs, PCR
- **OC Profile Panel** — Consolidated resistance (min of top-1 OI/CHNG OI/VOL strikes) and support (max) levels
- **Indian number formatting** — OI values in en-IN locale (lakhs notation)
- **Deterministic dummy data** — No Math.random(), consistent on every render
- **No external UI libraries** — Plain React + CSS Modules only
- **Sticky table header** with two-level column labels (CALL / STRIKE / PUT sections)

## Getting Started

### Prerequisites

- Node.js 18 or higher
- npm 8 or higher

### Installation

```bash
cd option-chain-ui
npm install
```

### Run Development Server

```bash
npm run dev
```

Opens at http://localhost:5173

### Build for Production

```bash
npm run build
npm run preview
```

## Data Structure

Each row in `src/data/dummyData.js` follows:

```js
{
  strike: 24500,
  call: {
    oi, chngOI, pctChngOI, volume, iv, ltp, chngLTP, pctChngLTP,
    buyQty, sellQty, bidQty, bid, askQty, ask
  },
  put: { /* mirror of call fields */ }
}
```

Key data characteristics:
- Strike 25000: highest call OI (~12,48,600) — main resistance
- Strike 24000: highest put OI (~4,12,600) — main support
- Strike 24500 (ATM): high OI on both sides (~8,24,600 call / ~7,96,400 put)
- Strike 23500: second major put OI level (~1,98,600)
- IV follows a volatility smile: ~14.62% at ATM, rising to ~26% at deep ITM/OTM

## Color Coding Guide

| Element | Color | Meaning |
|---|---|---|
| Dark navy row (calls) | #1F497D | ITM call / OTM put rows |
| White row (calls) | #FFFFFF | OTM call / ITM put rows |
| Steel blue strike | #4F81BD | Strike column (always) |
| Amber ATM highlight | #FBBF24 | ATM strike row |
| Red gradient rank 1 | #D99695 | Highest OTM call OI/CHNG OI/VOL |
| Red gradient rank 2 | #E6B9B8 | Second highest |
| Red gradient rank 3 | #F2DCDB | Third highest |
| Green gradient rank 1 | #C3D79B | Highest OTM put OI/CHNG OI/VOL |
| Green gradient rank 2 | #D7E4BD | Second highest |
| Green gradient rank 3 | #EBF1DE | Third highest |
| IV highlight | #F2DCDB / #9C0006 | IV > 16% |
| PCR bullish | #C6EFCE / #006100 | PCR > 1.1 |
| PCR neutral | #FFEB9C / #9C5700 | PCR 0.9–1.1 |
| PCR bearish | #FFC7CE / #9C0006 | PCR < 0.9 |
| Ratio green | #C6EFCE | Ratio <= 0.75 (dominant signal) |
| Ratio amber | #FFEB9C | Ratio 0.75–0.83 |
| Ratio light red | #FFE4E1 | Ratio 0.83–0.93 |
| Ratio red | #FFC7CE | Ratio > 0.93 (unclear dominance) |
