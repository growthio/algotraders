# Nifty 50 & Bank Nifty — Intraday Options-Buying Algorithmic Trading System
## Implementation Specification (Handoff Document)

> **Purpose of this document.** This is a complete, self-contained build specification for an automated intraday options-*buying* system for the Indian indices Nifty 50 and Bank Nifty. It is written to be handed directly to a coding agent (e.g. Claude) to implement against an existing Python codebase. It contains the objective, honest market findings, hard constraints, the full list of required data points, the system architecture, all reference code, the 15 strategy specifications, the risk/capital framework, the cost model, and the validation gates that must pass before any live capital is deployed.
>
> **Read Sections 2 and 3 before writing any code.** They contain non-negotiable findings and 2026 market-structure facts that change which strategies are even viable.

---

## 0. How to use this document (instructions for the implementing agent)

1. Treat the code in Sections 6–7 as the **canonical interface**. Implement new strategies against `core/strategy.py::Strategy`; route all orders through `brokers/base.py::BrokerAdapter`; never call a broker SDK directly from strategy code.
2. The system ships with **two reference strategies** (`ORB-15`, `DirectionalDebitSpread`). The remaining 13 are specified in Section 8 and must be implemented as small classes against the same interface.
3. There are exactly **two open seams** you must wire to the host codebase: `resolve_option(...)` (instrument-master → tradable symbol) and the **bar feed** into `engine.on_bar(...)`. See Section 15.
4. Ship and run in `paper_trading=True` first. Do not flip to live until the validation gates in Section 13 pass.
5. Do not "improve" the win rates or downgrade the risk controls to chase higher returns. The risk controls are the edge.

---

## 1. Objective & success criteria

| Item | Specification |
|---|---|
| Universe | Nifty 50 and Bank Nifty index options only |
| Instrument type | **Strictly option buying.** Single-leg long options **and** defined-risk debit spreads (a debit spread is net-buying with one sold leg) are permitted. No naked option selling, no undefined-risk positions. |
| Timeframe | 5-minute and 15-minute primary; 1-minute used only for aggregation and expiry-day micro-management |
| Risk:Reward | Each strategy is specified with two profiles — a **High-Win** profile (~1:1 to 1:1.4) and a **High-RR** profile (target 1:2). Operator selects per strategy. |
| Capital | Fixed base of **₹1,00,000**. Profits are **swept out** so the working base is reset to ₹1,00,000 (caps compounding by design). |
| Return target | **15–25% yearly XIRR** (≈ ₹15,000–25,000/yr ≈ ₹60–100 net per trading day) |
| Philosophy | Long-term consistency from positive expectancy + regime routing + strict risk control; **not** large abrupt profits |
| Automation | Fully automated execution via broker APIs. Operator tracks positions daily but does not place orders manually. |
| Brokers | **Zerodha Kite Connect** and **Fyers API v3**, both via official Python SDKs, behind one broker-agnostic abstraction |
| Data | All historical and live market data assumed available **except a news/event feed**. Strategies must therefore be purely price / volume / volatility-structure driven. |

---

## 2. Critical findings & honest expectations (READ FIRST)

The original brief requested 15 buying strategies with **>75% win rate, 1:2 reward:risk, and ≥85% rated VERY HIGH/HIGH confidence**. That combination is not deliverable honestly, and building toward it is the single most common way retail option-buyers destroy capital. The reasons, to be preserved in the codebase as guardrails:

1. **Win rate and reward:risk trade off against each other.** A genuine 75% win rate at 1:2 RR implies a per-trade expectancy of `0.75×2 − 0.25×1 = +1.25R`. Even at 1% risk and 2 trades/day that compounds to ~+600%/yr — i.e. it would be one of the most profitable systems on earth, not a 15–25% target. The numbers in the original brief are internally inconsistent; the inconsistency is the tell that 75% is unrealistic for buying.
2. **Option buying carries structural drag** that lowers realized win rates: negative theta (time decay; on expiry day OTM options can lose 80–90% of value in the final hour), bid/ask spread + slippage on the option leg, and transaction costs (STT, brokerage, exchange charges, GST, stamp duty). Costs alone can consume a large fraction of a 1:2 scalp edge.
3. **Empirical base rate.** SEBI's FY25 study found roughly **91% of individual equity-derivatives traders made net losses**, with aggregate net losses of about **₹1.06 lakh crore (₹1.05 trillion)** in FY25 — up ~41% on FY24. Most of those traders were running exactly the "high-probability option buying" playbooks. This ~91% is the base rate the system is fighting.
4. **Honest confidence distribution for this portfolio: 0 VERY HIGH, 4 HIGH, 9 NORMAL, 2 LOW.** Nothing single-leg-long deserves VERY HIGH. The HIGH ratings cluster on the **debit-spread** and **regime-routing** strategies because defined risk + theta hedging + regime gating are the only durable edges available to a retail buyer.
5. **The route to 15–25% is not a high win rate.** It is: positive net expectancy from the HIGH-confidence core, run through a regime classifier so the system only trades setups suited to the current day type, with strict per-trade risk, a daily kill switch, tight cost control, and profit sweeps.

> All win-rate and RR figures in this document are **regime-dependent estimate ranges to be validated on the operator's own backtest with the operator's own cost assumptions** (Section 13). They are not guarantees and must not be presented as such.

---

## 3. Market-structure constraints — 2026 (load-bearing)

These facts changed in 2024–2025 and invalidate large amounts of older "Nifty/BankNifty scalping" content. Verify against the broker's instrument master before go-live — expiry rules have moved more than once.

| Fact | Value (as of early 2026) | Consequence for the system |
|---|---|---|
| Nifty 50 lot size | **65** (reduced from 75, effective Jan 2026 cycle) | Position sizing and per-lot premium outlay |
| Bank Nifty lot size | **30** (reduced from 35, effective Jan 2026 cycle) | Position sizing |
| Nifty weekly expiry | **Every Tuesday** (shifted from Thursday w.e.f. 1 Sep 2025) | Nifty is the vehicle for expiry-day gamma/theta plays |
| Nifty monthly expiry | **Last Tuesday** of the month | — |
| Bank Nifty weekly expiry | **Discontinued** (Nov 2024). **Monthly only**, expires **last Tuesday** | Bank Nifty options carry more time value on most days → heavier theta for buyers → used only for slightly-ITM trend setups, **never** for cheap-OTM expiry plays |
| Holiday rule | If expiry day is a holiday, expiry shifts to the previous trading day | Expiry calendar logic must handle this |

**Capital reality on ₹1,00,000.** A single long Nifty lot is roughly ₹6,000–13,000 of premium depending on moneyness/IV/DTE; a Bank Nifty monthly lot is pricier and more theta-heavy. Practical concurrency is therefore **1–2 positions at a time**, and realistic granularity is **1 lot per signal**. Debit spreads have a smaller net debit and are the capital-efficient core.

---

## 4. Hard constraints & limitations

- **Buying only** (single-leg long + debit spreads). The order layer must refuse any net-credit / undefined-risk structure.
- **No news/event feed.** No earnings, macro, or headline inputs. All signals derive from price, volume, and volatility structure. Do not add news-dependent logic.
- **Fixed capital + sweep.** Working base is held at ₹1,00,000; the sweep routine moves realized profit above that out of the trading allocation. This intentionally caps compounding.
- **Concurrency cap** (1–2) is a capital constraint, not a preference.
- **Latency / slippage.** Retail API access with no co-location. Assume non-trivial slippage on option legs, especially away from ATM and on Bank Nifty. Model it (Section 11) and prefer ATM / spreads.
- **Compliance gate.** SEBI's retail-algo framework requires algos routed through broker APIs to be registered and order-tagged with the exchange. Both Kite Connect and Fyers implement this. **Confirm current registration, static-IP, and order-tagging requirements with each broker before going live.** This is a blocking item.
- **Broker rate limits.** Respect each broker's orders-per-second and API request quotas; throttle in the adapter layer.
- **Square-off.** Intraday product (MIS/INTRADAY). Hard square-off by **15:10** (configurable) to avoid broker auto-square-off slippage.
- **Single-account assumption.** No multi-account / spreading across accounts.

---

## 5. Required data points (explicit)

Every input the system consumes, its source, cadence, format, and which strategies depend on it. "Broker" = Kite/Fyers REST + WebSocket. "Derived" = computed by the system from other inputs.

| # | Data point | Source | Frequency | Format / notes | Used by |
|---|---|---|---|---|---|
| 1 | Underlying spot (NIFTY 50, NIFTY BANK) | Broker WS + historical REST | Tick (live) / 1-min history | float; index LTP | All |
| 2 | OHLCV bars — 5-min | Derived from ticks **or** broker historical | Every 5 min | open/high/low/close/volume; tz-aware index (Asia/Kolkata) | All |
| 3 | OHLCV bars — 15-min | Derived / broker historical | Every 15 min | as above | Regime, higher-TF confirmation |
| 4 | OHLCV bars — 1-min | Derived / broker historical | Every 1 min | as above | Bar aggregation, expiry scalps (S10/S11) |
| 5 | Bar volume | Within (2)–(4) | per bar | int | S1, S2, S5 (breakout/volume filters) |
| 6 | India VIX | Broker / index feed | 1-min | float | Regime classifier (S15), volatility gating |
| 7 | Option chain (per index, per expiry) | Broker REST | 1–5 s on demand | strike, CE/PE LTP, bid, ask, IV, OI; greeks (delta) | ITM/OTM selection, spread construction, all option-leg pricing |
| 8 | Per-strike bid/ask spread | From (7) | on demand | float; used to reject illiquid strikes and estimate slippage | All option entries, spreads |
| 9 | Option delta (greeks) | Broker chain **or** Black-Scholes calc | on demand | float; for slightly-ITM (~0.55) and deep-ITM (~0.8) selection | S4, S12–S14, deep-ITM variants |
| 10 | Open Interest (per strike) | From (7) | 1–5 s | int; **optional** filter (available since only news feed is excluded) | Optional confirmation filters |
| 11 | Instrument / symbol master | Broker REST (daily download) | Daily (pre-open) | Maps index+expiry+strike+right → tradable symbol; **format differs per broker** (Section 7) | `resolve_option`, every order |
| 12 | Expiry calendar | Derived + broker master + holiday list | Daily | Nifty weekly Tuesdays; monthly last Tuesday; holiday-shifted | Expiry detection (`is_expiry_day`), S10–S14 |
| 13 | Previous-day OHLC (index) | Broker historical | Daily (pre-open) | float | CPR, pivots, PDH/PDL (S6, S7) |
| 14 | Prior-day ATR (index) | Derived from daily bars | Daily (pre-open) | float; for CPR-width classification | S6, regime classifier |
| 15 | Lot size per instrument | Config / instrument master | Daily verify | int (Nifty 65, BankNifty 30) | Sizing, spreads |
| 16 | Charges schedule | Static config (operator-supplied) | On change | STT, exchange txn %, SEBI fee, GST %, stamp duty, brokerage/order | Cost model (Section 11), backtester |
| 17 | Account margin / funds | Broker REST | Pre-trade + on fill | float | Sizing sanity, sweep calc |
| 18 | Current working capital | Derived from funds + realized PnL | Continuous | float | Profit-sweep routine |
| 19 | Open positions | Broker REST/WS | Continuous | qty, avg, ltp, pnl | Concurrency cap, square-off, reconciliation |
| 20 | Order status / fills | Broker WS (postback) + REST | Real-time | order_id, status, fill price | Order management, SL/target placement, idempotency |

**Historical depth for backtesting:** at minimum 18–24 months of 1-min index data + reconstructable option premiums (or a synthetic Black-Scholes premium model calibrated to India VIX) + India VIX history + the charges schedule.

---

## 6. System architecture

### 6.1 Module map

```
config.py                 # settings, credentials, instrument specs, risk config
brokers/
  base.py                 # normalized enums, dataclasses, BrokerAdapter ABC  (CANONICAL)
  kite_adapter.py         # Zerodha Kite Connect implementation
  fyers_adapter.py        # Fyers API v3 implementation (native multileg for spreads)
core/
  strategy.py             # Signal, BarContext, Strategy ABC                  (CANONICAL)
  indicators.py           # vwap, atr, ema (extend as needed)
  risk.py                 # RiskManager: sizing, daily kill switch, sweep
strategies/
  orb.py                  # S1 reference (single-leg)
  debit_spread.py         # S12 reference (spread)
  ...                     # S2–S11, S13–S15 implement Strategy here
engine.py                 # orchestration loop, paper/live, square-off
main.py                   # wiring / entrypoint

# TO BE BUILT by implementing agent (Section 15):
data_feed.py              # WS/historical → BarContext on each closed bar
instrument_resolver.py    # resolve_option() against broker master
regime_classifier.py      # S15 day-type router
cost_model.py             # charges → per-round-trip cost (used by backtester + live PnL)
backtester.py             # vectorized per-strategy validation
state_store.py            # persistence: trades, daily risk state, sweep ledger
```

### 6.2 Data flow

```
broker WS/REST ──► data_feed ──► BarContext(5m,15m,spot,vix,is_expiry)
                                      │
                                      ▼
                          regime_classifier  ──►  active strategy set
                                      │
                                      ▼
                 Strategy.on_bar(ctx) ──► Signal | None
                                      │
                                      ▼
                 RiskManager.can_trade()/position_size()
                                      │
                                      ▼
        BrokerAdapter.place_order()/place_spread()  ──►  Kite | Fyers
                                      │
                                      ▼
        order postbacks ──► state_store ──► daily PnL ──► sweep routine
```

### 6.3 Modes
- `paper_trading=True`: signals and sizing computed and logged; no orders sent. Use for the full validation period.
- `paper_trading=False`: live order routing through the active broker.

---

## 7. Broker integration details

### 7.1 Normalized mapping

| Concept | Kite Connect | Fyers API v3 |
|---|---|---|
| Package | `kiteconnect` | `fyers-apiv3` (`from fyers_apiv3 import fyersModel`) |
| Client | `KiteConnect(api_key)` + `set_access_token` | `fyersModel.FyersModel(client_id, token, is_async=False, log_path)` |
| Buy/Sell | `TRANSACTION_TYPE_BUY/SELL` | `side`: `1` (buy) / `-1` (sell) |
| Order type | `ORDER_TYPE_MARKET/LIMIT/SL/SLM` | `type`: `1` Limit, `2` Market, `3` SL, `4` SL-M |
| Intraday product | `PRODUCT_MIS` | `productType="INTRADAY"` |
| Carry product | `PRODUCT_NRML` | `productType="MARGIN"` |
| Single order | `place_order(variety, exchange, tradingsymbol, ...)` | `place_order(dict)` |
| Multileg / spread | **None** — place legs individually | `place_multileg_order(dict)` with `orderType="2L"/"3L"` and `legs.leg1/leg2/leg3` |
| Symbol format | `NIFTY{yy}{m}{dd}{strike}{CE/PE}` e.g. `NIFTY2560324800CE` (verify against master) | `NSE:NIFTY{...}{CE/PE}` e.g. `NSE:NIFTY25...CE` (verify against `public.fyers.in` sym master) |
| LTP | `ltp(["NFO:SYMBOL"])` | `quotes({"symbols": "NSE:SYMBOL"})` |
| Square off | iterate positions, reverse | `exit_positions({})` |

> **Do not hard-code symbol formats.** Always resolve via the daily instrument master (data point #11). The formats above are illustrative and must be confirmed at runtime.

### 7.2 Reference code — `config.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BrokerName(str, Enum):
    KITE = "kite"
    FYERS = "fyers"


@dataclass(frozen=True)
class KiteCredentials:
    api_key: str
    api_secret: str
    access_token: str


@dataclass(frozen=True)
class FyersCredentials:
    client_id: str
    access_token: str
    log_path: str = ""


@dataclass(frozen=True)
class RiskConfig:
    base_capital: float = 100_000.0
    risk_per_trade_pct: float = 0.0075
    max_daily_loss_pct: float = 0.025
    max_trades_per_day: int = 5
    max_concurrent_positions: int = 2
    sweep_above: float = 100_000.0


@dataclass(frozen=True)
class InstrumentSpec:
    name: str
    lot_size: int
    tick_size: float = 0.05


INSTRUMENTS: dict[str, InstrumentSpec] = {
    "NIFTY": InstrumentSpec(name="NIFTY", lot_size=65),
    "BANKNIFTY": InstrumentSpec(name="BANKNIFTY", lot_size=30),
}


@dataclass
class AppConfig:
    active_broker: BrokerName = BrokerName.KITE
    paper_trading: bool = True
    risk: RiskConfig = field(default_factory=RiskConfig)
    session_square_off: str = "15:10"
```

### 7.3 Reference code — `brokers/base.py` (CANONICAL CONTRACT)

```python
from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SLM = "SLM"


class Product(str, Enum):
    INTRADAY = "INTRADAY"
    CARRY = "CARRY"


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: Side
    quantity: int
    order_type: OrderType = OrderType.MARKET
    product: Product = Product.INTRADAY
    limit_price: float = 0.0
    trigger_price: float = 0.0
    tag: str = ""


@dataclass(frozen=True)
class SpreadLeg:
    symbol: str
    side: Side
    quantity: int
    limit_price: float = 0.0


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    accepted: bool
    raw: dict


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    average_price: float
    last_price: float
    pnl: float


class BrokerAdapter(abc.ABC):
    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def ltp(self, symbol: str) -> float: ...

    @abc.abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult: ...

    @abc.abstractmethod
    def place_spread(self, legs: list[SpreadLeg], product: Product, tag: str = "") -> OrderResult: ...

    @abc.abstractmethod
    def modify_order(self, order_id: str, limit_price: float = 0.0, trigger_price: float = 0.0) -> OrderResult: ...

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> OrderResult: ...

    @abc.abstractmethod
    def positions(self) -> list[Position]: ...

    @abc.abstractmethod
    def square_off_all(self) -> None: ...
```

### 7.4 Reference code — `brokers/kite_adapter.py`

```python
from __future__ import annotations

from kiteconnect import KiteConnect

from brokers.base import (
    BrokerAdapter,
    OrderRequest,
    OrderResult,
    OrderType,
    Position,
    Product,
    Side,
    SpreadLeg,
)
from config import KiteCredentials


class KiteAdapter(BrokerAdapter):
    def __init__(self, credentials: KiteCredentials) -> None:
        self._creds = credentials
        self._kite = KiteConnect(api_key=credentials.api_key)

    def connect(self) -> None:
        self._kite.set_access_token(self._creds.access_token)

    def ltp(self, symbol: str) -> float:
        key = f"NFO:{symbol}"
        data = self._kite.ltp([key])
        return float(data[key]["last_price"])

    def _order_type(self, order_type: OrderType) -> str:
        return {
            OrderType.MARKET: self._kite.ORDER_TYPE_MARKET,
            OrderType.LIMIT: self._kite.ORDER_TYPE_LIMIT,
            OrderType.SL: self._kite.ORDER_TYPE_SL,
            OrderType.SLM: self._kite.ORDER_TYPE_SLM,
        }[order_type]

    def _product(self, product: Product) -> str:
        return self._kite.PRODUCT_MIS if product is Product.INTRADAY else self._kite.PRODUCT_NRML

    def _transaction(self, side: Side) -> str:
        return self._kite.TRANSACTION_TYPE_BUY if side is Side.BUY else self._kite.TRANSACTION_TYPE_SELL

    def place_order(self, request: OrderRequest) -> OrderResult:
        order_id = self._kite.place_order(
            variety=self._kite.VARIETY_REGULAR,
            exchange=self._kite.EXCHANGE_NFO,
            tradingsymbol=request.symbol,
            transaction_type=self._transaction(request.side),
            quantity=request.quantity,
            product=self._product(request.product),
            order_type=self._order_type(request.order_type),
            price=request.limit_price or None,
            trigger_price=request.trigger_price or None,
            tag=request.tag or None,
        )
        return OrderResult(order_id=str(order_id), accepted=True, raw={"order_id": order_id})

    def place_spread(self, legs: list[SpreadLeg], product: Product, tag: str = "") -> OrderResult:
        order_ids: list[str] = []
        for leg in legs:
            result = self.place_order(
                OrderRequest(
                    symbol=leg.symbol,
                    side=leg.side,
                    quantity=leg.quantity,
                    order_type=OrderType.LIMIT if leg.limit_price else OrderType.MARKET,
                    product=product,
                    limit_price=leg.limit_price,
                    tag=tag,
                )
            )
            order_ids.append(result.order_id)
        return OrderResult(order_id=",".join(order_ids), accepted=True, raw={"legs": order_ids})

    def modify_order(self, order_id: str, limit_price: float = 0.0, trigger_price: float = 0.0) -> OrderResult:
        self._kite.modify_order(
            variety=self._kite.VARIETY_REGULAR,
            order_id=order_id,
            price=limit_price or None,
            trigger_price=trigger_price or None,
        )
        return OrderResult(order_id=order_id, accepted=True, raw={})

    def cancel_order(self, order_id: str) -> OrderResult:
        self._kite.cancel_order(variety=self._kite.VARIETY_REGULAR, order_id=order_id)
        return OrderResult(order_id=order_id, accepted=True, raw={})

    def positions(self) -> list[Position]:
        net = self._kite.positions().get("net", [])
        return [
            Position(
                symbol=p["tradingsymbol"],
                quantity=p["quantity"],
                average_price=float(p["average_price"]),
                last_price=float(p["last_price"]),
                pnl=float(p["pnl"]),
            )
            for p in net
            if p["quantity"] != 0
        ]

    def square_off_all(self) -> None:
        for position in self.positions():
            side = Side.SELL if position.quantity > 0 else Side.BUY
            self.place_order(
                OrderRequest(
                    symbol=position.symbol,
                    side=side,
                    quantity=abs(position.quantity),
                    order_type=OrderType.MARKET,
                    product=Product.INTRADAY,
                    tag="square_off",
                )
            )
```

### 7.5 Reference code — `brokers/fyers_adapter.py`

```python
from __future__ import annotations

from fyers_apiv3 import fyersModel

from brokers.base import (
    BrokerAdapter,
    OrderRequest,
    OrderResult,
    OrderType,
    Position,
    Product,
    Side,
    SpreadLeg,
)
from config import FyersCredentials

_TYPE_CODE = {OrderType.LIMIT: 1, OrderType.MARKET: 2, OrderType.SL: 3, OrderType.SLM: 4}
_SIDE_CODE = {Side.BUY: 1, Side.SELL: -1}


class FyersAdapter(BrokerAdapter):
    def __init__(self, credentials: FyersCredentials) -> None:
        self._creds = credentials
        self._fyers = fyersModel.FyersModel(
            client_id=credentials.client_id,
            token=credentials.access_token,
            is_async=False,
            log_path=credentials.log_path,
        )

    def connect(self) -> None:
        self._fyers.get_profile()

    def _product(self, product: Product) -> str:
        return "INTRADAY" if product is Product.INTRADAY else "MARGIN"

    def ltp(self, symbol: str) -> float:
        response = self._fyers.quotes({"symbols": symbol})
        return float(response["d"][0]["v"]["lp"])

    def place_order(self, request: OrderRequest) -> OrderResult:
        data = {
            "symbol": request.symbol,
            "qty": request.quantity,
            "type": _TYPE_CODE[request.order_type],
            "side": _SIDE_CODE[request.side],
            "productType": self._product(request.product),
            "limitPrice": request.limit_price,
            "stopPrice": request.trigger_price,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag": request.tag or "algo",
        }
        response = self._fyers.place_order(data)
        accepted = response.get("s") == "ok"
        return OrderResult(order_id=str(response.get("id", "")), accepted=accepted, raw=response)

    def place_spread(self, legs: list[SpreadLeg], product: Product, tag: str = "") -> OrderResult:
        leg_payload = {
            f"leg{i}": {
                "symbol": leg.symbol,
                "qty": leg.quantity,
                "side": _SIDE_CODE[leg.side],
                "type": 1 if leg.limit_price else 2,
                "limitPrice": leg.limit_price,
            }
            for i, leg in enumerate(legs, start=1)
        }
        data = {
            "orderTag": tag or "spread",
            "productType": self._product(product),
            "offlineOrder": False,
            "orderType": f"{len(legs)}L",
            "validity": "DAY",
            "legs": leg_payload,
        }
        response = self._fyers.place_multileg_order(data)
        accepted = response.get("s") == "ok"
        return OrderResult(order_id=str(response.get("id", "")), accepted=accepted, raw=response)

    def modify_order(self, order_id: str, limit_price: float = 0.0, trigger_price: float = 0.0) -> OrderResult:
        response = self._fyers.modify_order(
            {"id": order_id, "limitPrice": limit_price, "stopPrice": trigger_price}
        )
        return OrderResult(order_id=order_id, accepted=response.get("s") == "ok", raw=response)

    def cancel_order(self, order_id: str) -> OrderResult:
        response = self._fyers.cancel_order({"id": order_id})
        return OrderResult(order_id=order_id, accepted=response.get("s") == "ok", raw=response)

    def positions(self) -> list[Position]:
        response = self._fyers.positions()
        result: list[Position] = []
        for p in response.get("netPositions", []):
            if p.get("netQty", 0) == 0:
                continue
            result.append(
                Position(
                    symbol=p["symbol"],
                    quantity=int(p["netQty"]),
                    average_price=float(p.get("netAvg", 0.0)),
                    last_price=float(p.get("ltp", 0.0)),
                    pnl=float(p.get("pl", 0.0)),
                )
            )
        return result

    def square_off_all(self) -> None:
        self._fyers.exit_positions({})
```

### 7.6 Reference code — `core/strategy.py` (CANONICAL CONTRACT)

```python
from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum

import pandas as pd

from brokers.base import Side, SpreadLeg


class SignalKind(str, Enum):
    SINGLE = "SINGLE"
    SPREAD = "SPREAD"


@dataclass(frozen=True)
class Signal:
    kind: SignalKind
    side: Side
    option_symbol: str
    stop_premium: float
    target_premium: float
    spread_legs: tuple[SpreadLeg, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class BarContext:
    underlying: str
    bars_5m: pd.DataFrame
    bars_15m: pd.DataFrame
    spot: float
    india_vix: float
    is_expiry_day: bool


class Strategy(abc.ABC):
    name: str

    @abc.abstractmethod
    def on_bar(self, ctx: BarContext) -> Signal | None: ...
```

### 7.7 Reference code — `core/indicators.py`

```python
from __future__ import annotations

import pandas as pd


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cumulative_pv = (typical * df["volume"]).cumsum()
    cumulative_vol = df["volume"].cumsum().replace(0, pd.NA)
    return cumulative_pv / cumulative_vol


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()
```

### 7.8 Reference code — `core/risk.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from config import RiskConfig


@dataclass
class RiskState:
    day: date
    realized_pnl: float = 0.0
    trades_taken: int = 0
    open_positions: int = 0


@dataclass
class RiskManager:
    config: RiskConfig
    state: RiskState = field(init=False)

    def __post_init__(self) -> None:
        self.state = RiskState(day=date.today())

    def _roll_day(self) -> None:
        today = date.today()
        if today != self.state.day:
            self.state = RiskState(day=today)

    def can_trade(self) -> bool:
        self._roll_day()
        if self.state.trades_taken >= self.config.max_trades_per_day:
            return False
        if self.state.open_positions >= self.config.max_concurrent_positions:
            return False
        max_loss = -self.config.base_capital * self.config.max_daily_loss_pct
        return self.state.realized_pnl > max_loss

    def position_size(self, premium_stop_distance: float, lot_size: int) -> int:
        if premium_stop_distance <= 0:
            return 0
        risk_rupees = self.config.base_capital * self.config.risk_per_trade_pct
        per_lot_risk = premium_stop_distance * lot_size
        if per_lot_risk <= 0:
            return 0
        lots = int(risk_rupees // per_lot_risk)
        return max(lots, 0) * lot_size

    def register_entry(self) -> None:
        self.state.trades_taken += 1
        self.state.open_positions += 1

    def register_exit(self, pnl: float) -> None:
        self.state.realized_pnl += pnl
        self.state.open_positions = max(self.state.open_positions - 1, 0)

    def sweep_amount(self, current_capital: float) -> float:
        excess = current_capital - self.config.sweep_above
        return max(excess, 0.0)
```

### 7.9 Reference code — `strategies/orb.py` (S1, single-leg)

```python
from __future__ import annotations

from datetime import time

import pandas as pd

from brokers.base import Side
from core.strategy import BarContext, Signal, SignalKind, Strategy


class OpeningRangeBreakout(Strategy):
    name = "orb_15"

    def __init__(self, option_resolver, target_rr: float = 2.0, volume_mult: float = 1.5) -> None:
        self._resolve_option = option_resolver
        self._target_rr = target_rr
        self._volume_mult = volume_mult
        self._range_high: float | None = None
        self._range_low: float | None = None

    def _compute_range(self, bars: pd.DataFrame) -> None:
        opening = bars.between_time("09:15", "09:30")
        if not opening.empty:
            self._range_high = float(opening["high"].max())
            self._range_low = float(opening["low"].min())

    def on_bar(self, ctx: BarContext) -> Signal | None:
        bars = ctx.bars_5m
        if bars.empty:
            return None

        last_ts = bars.index[-1].time()
        if last_ts <= time(9, 30):
            self._compute_range(bars)
            return None
        if self._range_high is None or self._range_low is None:
            self._compute_range(bars)
            if self._range_high is None:
                return None

        last = bars.iloc[-1]
        avg_volume = bars["volume"].rolling(20).mean().iloc[-1]
        if pd.isna(avg_volume) or last["volume"] < self._volume_mult * avg_volume:
            return None

        if last["close"] > self._range_high:
            side, ref = Side.BUY, "CE"
        elif last["close"] < self._range_low:
            side, ref = Side.BUY, "PE"
        else:
            return None

        symbol = self._resolve_option(ctx.underlying, ctx.spot, ref, moneyness="ATM")
        entry_premium = bars["close"].iloc[-1]
        stop = self._range_low if ref == "CE" else self._range_high
        stop_distance_spot = abs(ctx.spot - stop)
        stop_premium = max(entry_premium - stop_distance_spot * 0.5, 0.05)
        target_premium = entry_premium + (entry_premium - stop_premium) * self._target_rr

        return Signal(
            kind=SignalKind.SINGLE,
            side=side,
            option_symbol=symbol,
            stop_premium=round(stop_premium, 2),
            target_premium=round(target_premium, 2),
            reason=f"ORB breakout {ref}",
        )
```

### 7.10 Reference code — `strategies/debit_spread.py` (S12, spread)

```python
from __future__ import annotations

import pandas as pd

from brokers.base import Side, SpreadLeg
from core.indicators import ema
from core.strategy import BarContext, Signal, SignalKind, Strategy


class DirectionalDebitSpread(Strategy):
    name = "directional_debit_spread"

    def __init__(self, option_resolver, lot_size: int, width_strikes: int = 2) -> None:
        self._resolve_option = option_resolver
        self._lot_size = lot_size
        self._width = width_strikes

    def on_bar(self, ctx: BarContext) -> Signal | None:
        bars = ctx.bars_5m
        if len(bars) < 25:
            return None

        ema9 = ema(bars["close"], 9)
        ema20 = ema(bars["close"], 20)
        bullish = ema9.iloc[-1] > ema20.iloc[-1] and bars["close"].iloc[-1] > ema20.iloc[-1]
        bearish = ema9.iloc[-1] < ema20.iloc[-1] and bars["close"].iloc[-1] < ema20.iloc[-1]
        if not (bullish or bearish):
            return None

        ref = "CE" if bullish else "PE"
        long_symbol = self._resolve_option(ctx.underlying, ctx.spot, ref, moneyness="ATM")
        short_symbol = self._resolve_option(
            ctx.underlying, ctx.spot, ref, moneyness="OTM", strikes_away=self._width
        )

        legs = (
            SpreadLeg(symbol=long_symbol, side=Side.BUY, quantity=self._lot_size),
            SpreadLeg(symbol=short_symbol, side=Side.SELL, quantity=self._lot_size),
        )
        return Signal(
            kind=SignalKind.SPREAD,
            side=Side.BUY,
            option_symbol=long_symbol,
            stop_premium=0.0,
            target_premium=0.0,
            spread_legs=legs,
            reason=f"{'bull-call' if bullish else 'bear-put'} debit spread",
        )
```

### 7.11 Reference code — `engine.py`

```python
from __future__ import annotations

import logging
from datetime import datetime, time

from brokers.base import BrokerAdapter, OrderRequest, OrderType, Product, Side
from config import AppConfig, INSTRUMENTS
from core.risk import RiskManager
from core.strategy import BarContext, Signal, SignalKind, Strategy

logger = logging.getLogger("engine")


class TradingEngine:
    def __init__(self, broker: BrokerAdapter, risk: RiskManager, config: AppConfig) -> None:
        self._broker = broker
        self._risk = risk
        self._config = config
        self._strategies: list[Strategy] = []

    def register(self, strategy: Strategy) -> None:
        self._strategies.append(strategy)

    def _square_off_time(self) -> time:
        hour, minute = map(int, self._config.session_square_off.split(":"))
        return time(hour, minute)

    def _execute(self, signal: Signal, underlying: str) -> None:
        if not self._risk.can_trade():
            logger.info("risk gate blocked signal: %s", signal.reason)
            return

        lot_size = INSTRUMENTS[underlying].lot_size

        if self._config.paper_trading:
            logger.info("[PAPER] %s | %s | stop=%.2f target=%.2f",
                        signal.reason, signal.option_symbol, signal.stop_premium, signal.target_premium)
            self._risk.register_entry()
            return

        if signal.kind is SignalKind.SPREAD:
            self._broker.place_spread(list(signal.spread_legs), Product.INTRADAY, tag=signal.reason)
            self._risk.register_entry()
            return

        entry_premium = self._broker.ltp(signal.option_symbol)
        stop_distance = max(entry_premium - signal.stop_premium, 0.05)
        quantity = self._risk.position_size(stop_distance, lot_size)
        if quantity == 0:
            logger.info("size resolved to zero, skipping")
            return

        self._broker.place_order(
            OrderRequest(
                symbol=signal.option_symbol,
                side=Side.BUY,
                quantity=quantity,
                order_type=OrderType.MARKET,
                product=Product.INTRADAY,
                tag=signal.reason,
            )
        )
        self._broker.place_order(
            OrderRequest(
                symbol=signal.option_symbol,
                side=Side.SELL,
                quantity=quantity,
                order_type=OrderType.SLM,
                product=Product.INTRADAY,
                trigger_price=signal.stop_premium,
                tag="protective_stop",
            )
        )
        self._risk.register_entry()

    def on_bar(self, ctx: BarContext) -> None:
        if datetime.now().time() >= self._square_off_time():
            self._broker.square_off_all()
            return
        for strategy in self._strategies:
            signal = strategy.on_bar(ctx)
            if signal is not None:
                self._execute(signal, ctx.underlying)
```

### 7.12 Reference code — `main.py`

```python
from __future__ import annotations

import logging

from brokers.fyers_adapter import FyersAdapter
from brokers.kite_adapter import KiteAdapter
from config import (
    AppConfig,
    BrokerName,
    FyersCredentials,
    INSTRUMENTS,
    KiteCredentials,
)
from core.risk import RiskManager
from engine import TradingEngine
from strategies.debit_spread import DirectionalDebitSpread
from strategies.orb import OpeningRangeBreakout


def build_broker(config: AppConfig):
    if config.active_broker is BrokerName.KITE:
        return KiteAdapter(KiteCredentials(api_key="", api_secret="", access_token=""))
    return FyersAdapter(FyersCredentials(client_id="", access_token="", log_path="./logs"))


def resolve_option(underlying: str, spot: float, right: str, moneyness: str = "ATM", strikes_away: int = 0) -> str:
    raise NotImplementedError("Wire to broker instrument master / option chain")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = AppConfig()
    broker = build_broker(config)
    broker.connect()

    risk = RiskManager(config=config.risk)
    engine = TradingEngine(broker=broker, risk=risk, config=config)

    engine.register(OpeningRangeBreakout(option_resolver=resolve_option))
    engine.register(
        DirectionalDebitSpread(option_resolver=resolve_option, lot_size=INSTRUMENTS["NIFTY"].lot_size)
    )

    logging.getLogger("main").info("engine ready | paper=%s | broker=%s",
                                   config.paper_trading, config.active_broker.value)


if __name__ == "__main__":
    main()
```

---

## 8. The 15 strategies

Confidence ratings are honest (see Section 2). Win-rate figures are **estimate ranges to validate by backtest**. `HW` = High-Win profile, `HR` = High-RR profile.

### S1 — Opening Range Breakout (ORB-15)
- **Family / Vehicle / TF:** Trend-momentum / Nifty weekly ATM / 5-min
- **Best regime:** Trend day
- **Confidence:** NORMAL — clean and robust, but win rate is not high and false breakouts on range days hurt.
- **Profiles:** HW ~55% @ 1:1.3 | HR ~44% @ 1:2 | Est. CI 42–58%
- **Entry:** Mark 09:15–09:30 high/low. Long CE on a 5-min close above the range high with volume > `volume_mult`×20-bar average; PE mirror on a close below the range low.
- **Exit / target:** HR — fixed 1:2. HW — scale 50% at 1:1, trail remainder on the 20-EMA. Exit all by 15:10.
- **Stop:** Opposite end of the opening range (translated to a premium stop).
- **Theta/cost note:** Intraday, exit same day → low theta. Reject if the chosen strike's bid/ask spread is wide.
- **Data:** 5-min OHLCV, spot, option chain (ATM), instrument master.
- **Tunable:** opening-range window, `volume_mult`, `target_rr`, trail anchor.
- **Impl:** See `strategies/orb.py` (reference). Add a trend-day gate from the regime classifier.

### S2 — VWAP Reclaim Momentum
- **Family / Vehicle / TF:** Trend-momentum / Nifty weekly ATM / 5-min
- **Best regime:** Trend day
- **Confidence:** NORMAL
- **Profiles:** HW ~55% @ 1:1.3 | HR ~45% @ 1:2 | Est. CI 43–58%
- **Entry:** In an established trend, after price pulls back to VWAP, enter when a 5-min candle closes reclaiming VWAP in the trend direction with rising volume.
- **Exit / target:** As HW/HR. **Stop:** swing pivot just beyond VWAP.
- **Data:** 5-min OHLCV (VWAP needs intraday volume), spot, chain.
- **Tunable:** reclaim confirmation (close vs wick), volume filter, RR.

### S3 — 9/20 EMA Pullback
- **Family / Vehicle / TF:** Trend-momentum / Nifty weekly ATM / 5-min
- **Best regime:** Trend day
- **Confidence:** NORMAL
- **Profiles:** HW ~54% @ 1:1.4 | HR ~44% @ 1:2 | Est. CI 42–57%
- **Entry:** Price above a rising 20-EMA with 9-EMA > 20-EMA → buy CE on a pullback to the 9-EMA that holds (bullish rejection candle); mirror for PE. **Stop:** below the pullback low.
- **Tunable:** EMA periods, rejection-candle definition.

### S4 — Supertrend + ADX Momentum
- **Family / Vehicle / TF:** Trend-momentum / Nifty or BankNifty **slightly-ITM (delta ≈ 0.55)** / 5-min
- **Best regime:** Trend day
- **Confidence:** NORMAL — ADX filter reduces chop but does not eliminate whipsaws.
- **Profiles:** HW ~56% @ 1:1.3 | HR ~46% @ 1:2 | Est. CI 44–59%
- **Entry:** Supertrend(10,3) flips up with ADX > 20 and +DI > −DI → buy slightly-ITM CE; mirror. **Stop:** Supertrend flip back.
- **Theta/cost note:** Slightly-ITM lowers theta-as-% vs ATM; acceptable on BankNifty monthly.
- **Data:** 5-min OHLCV, option delta/greeks (for ~0.55 selection), chain.
- **Tunable:** Supertrend params, ADX threshold, target delta.

### S5 — Bollinger Squeeze Breakout
- **Family / Vehicle / TF:** Volatility breakout / Nifty weekly ATM / 5-min
- **Best regime:** Post-consolidation expansion
- **Confidence:** NORMAL
- **Profiles:** HW ~52% @ 1:1.3 | HR ~40% @ 1:2.5 | Est. CI 38–55%
- **Entry:** Bandwidth contracts to a 50-bar low (squeeze); enter on the expansion candle that closes beyond a band. **Stop:** opposite band.
- **Tunable:** BB length/stddev, squeeze lookback, expansion confirmation.

### S6 — CPR Trend-Day Play
- **Family / Vehicle / TF:** Volatility/structure / Nifty weekly ATM / 5-min + daily
- **Best regime:** Trend day (narrow CPR)
- **Confidence:** **HIGH** — CPR width is a genuinely useful day-type classifier; pairs with the regime router.
- **Profiles:** HW ~55% @ 1:1.4 | HR ~47% @ 1:2 | Est. CI 45–60%
- **Entry:** Compute daily CPR. If CPR width < ~0.3×(prior-day ATR) → trend day → trade the previous-day-high/low or opening-range breakout in the CPR-implied direction. Wide CPR → route to S8. **Stop:** CPR top/bottom or opposite OR end.
- **Data:** previous-day OHLC, prior-day ATR, 5-min OHLCV, spot, chain.
- **Tunable:** CPR-width threshold, direction trigger.

### S7 — Camarilla Pivots
- **Family / Vehicle / TF:** Structure / Nifty weekly ATM / 5-min + daily
- **Best regime:** Both (breakout or reversal selected by level)
- **Confidence:** NORMAL
- **Profiles:** HW ~54% @ 1:1.3 | HR ~44% @ 1:2 | Est. CI 42–57%
- **Entry:** Breakout beyond H4/L4 → momentum buy in breakout direction; rejection at H3/L3 → reversal buy the other way. **Stop:** next pivot level.
- **Data:** previous-day OHLC (Camarilla pivots), 5-min OHLCV, chain.
- **Tunable:** which pivot set, confirmation candle.

### S8 — Support/Resistance Range Fade
- **Family / Vehicle / TF:** Mean-reversion / Nifty weekly ATM / 5-min
- **Best regime:** **Range day only**
- **Confidence:** NORMAL — *high win rate but real tail risk*; downgraded from the win rate alone because a range break gives back many wins.
- **Profiles:** HW ~62% @ 1:1 | HR ~50% @ 1:1.7 | Est. CI 48–65%
- **Entry:** On a confirmed range day, buy PE on rejection at the range high, CE on rejection at the range low; target the mid-range. **Stop (mandatory):** a hard stop beyond the range — non-negotiable.
- **Data:** intraday range detection, 5-min OHLCV, chain; regime label = range.
- **Tunable:** range-confirmation logic, rejection definition, hard-stop buffer.

### S9 — RSI(2) Mean-Reversion Bounce
- **Family / Vehicle / TF:** Mean-reversion / Nifty weekly ATM / 5-min
- **Best regime:** Range / pullback within trend
- **Confidence:** NORMAL
- **Profiles:** HW ~60% @ 1:1 | HR ~48% @ 1:1.6 | Est. CI 46–63%
- **Entry:** Uptrend + RSI(2) < 10 at support → buy CE; downtrend + RSI(2) > 90 at resistance → buy PE. Quick reversion scalp. **Stop:** beyond the tested swing.
- **Tunable:** RSI period/thresholds, support/resistance source.

### S10 — Expiry-Day Gamma Scalp (Nifty, Tuesday)
- **Family / Vehicle / TF:** Expiry / Nifty weekly ATM / 1–5-min
- **Best regime:** Expiry Tuesday, trending hours
- **Confidence:** **LOW** — highest variance; theta is brutal if a position stalls.
- **Profiles:** HW ~50% @ 1:1.2 | HR ~40% @ 1:2.5 | Est. CI 36–52%
- **Entry:** Tuesday only. On a momentum thrust (range expansion + correct VWAP side), buy near-ATM weekly. **Hard time-stop (8–12 min) plus a price stop.** Avoid 12:00–13:30 chop.
- **Data:** 1-min + 5-min OHLCV, spot, chain, `is_expiry_day=True`, India VIX.
- **Tunable:** time-stop, thrust definition, blackout window. **Size small.**

### S11 — Expiry-Day OTM Momentum Lottery (Nifty, Tuesday)
- **Family / Vehicle / TF:** Expiry / Nifty weekly **OTM** / 1–5-min
- **Best regime:** Strongly directional expiry only
- **Confidence:** **LOW** — lottery payoff; low hit rate by design.
- **Profiles:** HR ~33% @ 1:4 (no HW variant) | Est. CI 28–40%
- **Entry:** Only on a strongly directional expiry, buy cheap ~1–2% OTM on a breakout for asymmetric payoff. **Tiny size.** **Stop:** fixed % of premium / time-stop.
- **Tunable:** OTM distance, max premium per attempt, daily attempt cap.

### S12 — Directional Debit Spread
- **Family / Vehicle / TF:** Spread / Nifty or BankNifty vertical (bull-call / bear-put) / 5-min
- **Best regime:** Trend day
- **Confidence:** **HIGH** — best risk-adjusted buying structure; the sold leg hedges theta and IV; risk = net debit.
- **Profiles:** HW ~58% @ 1:1.3 | HR ~50% @ 1:1.8 | Est. CI 48–60%
- **Entry:** On a confirmed directional signal (reuse S2/S4/S6 trigger), buy ATM + sell 1–2 strikes OTM. **Exit:** target a % of max spread value; **stop** at a % of net debit.
- **Execution:** Fyers `place_multileg_order` (2L); Kite leg-by-leg via `place_spread`.
- **Data:** chain (both legs, bid/ask, delta), 5-min OHLCV, lot size.
- **Tunable:** strike width, signal source, target/stop % of debit. See `strategies/debit_spread.py` (reference).

### S13 — ORB Debit Spread
- **Family / Vehicle / TF:** Spread / Nifty vertical / 5-min
- **Best regime:** Trend day
- **Confidence:** **HIGH** — ORB trigger expressed as a vertical to cut theta/slippage vs a naked long.
- **Profiles:** HW ~56% @ 1:1.4 | HR ~47% @ 1:1.8 | Est. CI 46–58%
- **Entry:** Same ORB trigger as S1, executed as a bull-call/bear-put vertical. **Exit/stop:** as S12.
- **Impl:** Compose the S1 breakout detector with the S12 spread builder.

### S14 — Mild Diagonal (Nifty only)
- **Family / Vehicle / TF:** Spread/calendar / Nifty weekly + next-weekly / 5-min + multi-day
- **Best regime:** Mild trend, low-to-moderate volatility
- **Confidence:** NORMAL — more moving parts and execution risk; Nifty-only because BankNifty has no weekly.
- **Profiles:** HW ~55% @ 1:1.2 (no clean HR variant) | Est. CI 50–58%
- **Entry:** Buy a slightly-ITM next-weekly + sell a near-OTM current-weekly to harvest near-term theta while staying net-long-directional. **Exit:** manage as theta accrues; defined max loss = net debit.
- **Data:** two expiries in the chain, delta, India VIX.
- **Tunable:** ITM depth of long leg, OTM distance of short leg, roll logic.

### S15 — Regime-Switch Composite (overlay / router)
- **Family / Vehicle / TF:** Meta-overlay / routes to other strategies / decision at ~09:35
- **Best regime:** All — this *is* the regime selector
- **Confidence:** **HIGH** — regime gating is the single biggest durable edge for a retail buyer.
- **Profiles:** Blended ~53% @ ~1:1.5 | Est. CI 50–56%
- **Logic:** At ~09:35 classify the day from CPR width, India VIX band, opening-range size, and gap %, then activate the matching book:
  - **Trend** → S1/S2/S6 + spreads S12/S13
  - **Range** → S8/S9
  - **Expiry (Tue)** → S10 (small) + S12
- **Output:** the active strategy set for the day; the engine only runs those. **Do not run all 15 blindly.**
- **Impl:** `regime_classifier.py` (Section 15); the engine consults it before dispatching `on_bar`.

### 8.1 Recommended live deployment book
Run the HIGH-confidence core daily, **regime-gated by S15**: S6 + spreads S12/S13 on trend days; S8/S9 on range days. Treat S1–S5/S7 as situational add-ons and the expiry lotteries S10/S11 as tiny optional satellites. This yields a HIGH-confidence-*weighted* live book even though only 4 of 15 individual strategies are rated HIGH.

---

## 9. Regime classifier (S15) — specification

**Inputs (computed by ~09:35):** CPR width vs prior-day ATR; India VIX value/band; opening-range (09:15–09:30) size vs prior-day ATR; gap % (today open vs prior close); `is_expiry_day`.

**Output:** one of `{TREND, RANGE, EXPIRY}` plus a list of active strategy names.

**Pseudocode:**
```
if is_expiry_day:                      regime = EXPIRY
elif cpr_width < 0.30 * prior_atr
     and opening_range > 0.45 * prior_atr: regime = TREND
elif cpr_width > 0.60 * prior_atr
     or india_vix in calm_band:        regime = RANGE
else:                                  regime = TREND   # default to trend book with spreads (defined risk)
```
Thresholds are starting points; calibrate per Section 13. Prefer defaulting ambiguous days to the **spread** book (defined risk) rather than naked longs.

---

## 10. Risk & capital management

- **Risk per trade:** 0.5–1.0% of ₹1,00,000 (config `risk_per_trade_pct=0.0075`).
- **Position sizing:** `lots = floor(risk_rupees / (premium_stop_distance × lot_size))`, then ×lot_size. Realistic granularity is 1 lot.
- **Daily loss limit:** 2.5% (₹2,500) → `can_trade()` returns False for the rest of the day (kill switch).
- **Max trades/day:** 5. **Max concurrent positions:** 2.
- **Profit sweep:** `sweep_amount(current_capital) = max(current_capital − 100_000, 0)`. Run end-of-day or weekly; persist a sweep ledger; reset working base to ₹1,00,000.
- **Square-off:** all positions flat by 15:10 (engine-enforced).

---

## 11. Cost model (must be in the backtester and live PnL)

Components per round-trip (operator supplies exact current rates in config data point #16):
- Securities Transaction Tax (STT) — on the sell side of option premium
- Exchange transaction charges (NFO options) — % of premium turnover
- SEBI turnover fee
- GST — 18% on (brokerage + exchange txn charges)
- Stamp duty — on buy side
- Brokerage — per executed order (both brokers ≈ ₹20/order or as configured)

`net_pnl = gross_pnl − Σ(all charges, both legs/all spread legs)`. A naked-long round-trip can cost ₹40–80+ all-in; on a 1:2 scalp this is a material slice of edge and is a primary reason to favor ATM strikes, spreads (fewer adverse-theta exits), and the regime router (fewer junk trades). **Expectancy must be computed net of this model.**

---

## 12. Execution & order management

- **Entry:** market (or marketable-limit to cap slippage). For spreads, use the broker's atomic multileg where available (Fyers) and reconcile leg fills where not (Kite).
- **Protective stop:** SL-M attached immediately after entry at `stop_premium`.
- **Target:** limit at `target_premium` (HR) or partial-scale + trail (HW).
- **Time-stop:** mandatory for S10/S11 (expiry); recommended cap for all intraday holds.
- **Reconciliation:** consume order postbacks; persist order/fill state; make entries idempotent so a restart does not double-fire. On startup, reconcile broker positions against `state_store` before accepting new signals.
- **Throttle:** respect broker orders-per-second; queue and retry on rate-limit errors with backoff.

---

## 13. Backtesting & validation gates (blocking before live)

Build `backtester.py` (vectorized, pandas/numpy) producing **per strategy and per regime**:
- realized win rate, realized average RR, **net expectancy (R, after the Section 11 cost model)**, profit factor, max drawdown, trade count, and a returns series.
- slippage assumption applied to every option fill (e.g. half the modeled bid/ask, plus a fixed tick buffer).
- walk-forward windows; regime-segmented results; sensitivity to the key tunables.

**Acceptance gates (all must hold on out-of-sample data before that strategy goes live):**
1. Net expectancy > 0 after costs and slippage over a full regime cycle.
2. Max drawdown within the operator's tolerance for a ₹1,00,000 base.
3. Realized win rate within the strategy's stated CI band (else recalibrate or shelve).
4. Performance not dependent on a single regime that may not recur.

**Then** run `paper_trading=True` live for a meaningful period (multiple weeks across regimes) and confirm paper results track the backtest before risking capital.

---

## 14. Implementation checklist (build order)

1. Drop in `config.py`, `brokers/base.py`, `core/strategy.py`, `core/indicators.py`, `core/risk.py`, `engine.py` (Sections 7.2–7.11). Confirm imports resolve in the host project layout.
2. Implement `instrument_resolver.py::resolve_option` for **both** brokers against their daily instrument masters (Section 15).
3. Implement `data_feed.py`: subscribe to WS / load history, aggregate to 5-/15-/1-min, build `BarContext` on each **closed** bar, and call `engine.on_bar`.
4. Implement `cost_model.py` from data point #16.
5. Implement `regime_classifier.py` (Section 9) and have the engine consult it to filter active strategies.
6. Verify the two reference strategies (S1, S12) run end-to-end in `paper_trading=True`.
7. Implement S2–S11, S13–S15 against `Strategy` (Section 8). One module per strategy, comment-free, PEP 8.
8. Implement `state_store.py` (trades, daily risk state, sweep ledger) and order reconciliation (Section 12).
9. Build `backtester.py`; run the validation gates (Section 13).
10. **Compliance:** complete broker/exchange algo registration + order tagging (Section 4) — blocking.
11. Paper-trade across a full regime cycle; compare to backtest; only then enable live with the smallest viable size.

---

## 15. Open seams to fill (host-codebase specifics)

- **`resolve_option(underlying, spot, right, moneyness, strikes_away)`** → tradable symbol. Round `spot` to the strike step (Nifty 50, BankNifty 100), apply moneyness/`strikes_away`, select the correct expiry (Nifty weekly Tuesday; BankNifty monthly last Tuesday), and look the contract up in the **broker-specific** instrument master (Kite `instruments("NFO")`; Fyers `public.fyers.in/sym_details/NSE_FO.csv`). Reject illiquid strikes by bid/ask width.
- **Bar feed → `engine.on_bar(ctx)`** — WebSocket (Kite `KiteTicker`; Fyers `FyersWebsocket.data_ws`) or a historical replay loop in backtest. Emit `BarContext` only on **closed** bars.
- **`state_store`** — persistence for trades, daily `RiskState`, and the profit-sweep ledger (so the working base correctly resets to ₹1,00,000).
- **Alerting/monitoring** — heartbeat, error alerts, daily PnL + sweep report to the operator.

---

## 16. Disclaimers

This system trades leveraged derivatives. Independent of any backtest, capital is at risk and losses can be total for any given position. The win-rate, RR, and confidence figures herein are **estimates to be validated on the operator's own data and costs**, not guarantees or solicitations. Roughly 91% of individual derivatives traders lose money (SEBI FY25); this system's controls are designed to fight that base rate, not to defeat it with certainty. This document is engineering material, **not investment advice**, and its author is not a SEBI-registered advisor. Confirm and comply with all current SEBI / exchange / broker requirements for automated retail trading (algo registration, order tagging, static IP) before deploying live capital.

---

## Appendix A — Master strategy table

| # | Strategy | Vehicle | Win% (HW) | Win% (HR) | RR (HW → HR) | Best regime | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | ORB-15 | Nifty wk ATM | ~55% | ~44% | 1:1.3 → 1:2 | Trend | NORMAL |
| 2 | VWAP reclaim | Nifty wk ATM | ~55% | ~45% | 1:1.3 → 1:2 | Trend | NORMAL |
| 3 | 9/20 EMA pullback | Nifty wk ATM | ~54% | ~44% | 1:1.4 → 1:2 | Trend | NORMAL |
| 4 | Supertrend + ADX | Nifty/BN ITM | ~56% | ~46% | 1:1.3 → 1:2 | Trend | NORMAL |
| 5 | BB squeeze | Nifty wk ATM | ~52% | ~40% | 1:1.3 → 1:2.5 | Coil→expansion | NORMAL |
| 6 | CPR trend-day | Nifty wk ATM | ~55% | ~47% | 1:1.4 → 1:2 | Trend (narrow CPR) | HIGH |
| 7 | Camarilla | Nifty wk ATM | ~54% | ~44% | 1:1.3 → 1:2 | Both | NORMAL |
| 8 | S/R range fade | Nifty wk ATM | ~62% | ~50% | 1:1 → 1:1.7 | Range only | NORMAL |
| 9 | RSI(2) bounce | Nifty wk ATM | ~60% | ~48% | 1:1 → 1:1.6 | Range / pullback | NORMAL |
| 10 | Expiry gamma scalp | Nifty wk ATM | ~50% | ~40% | 1:1.2 → 1:2.5 | Expiry Tue | LOW |
| 11 | Expiry OTM lottery | Nifty wk OTM | — | ~33% | — → 1:4 | Expiry, trending | LOW |
| 12 | Directional debit spread | Nifty/BN vertical | ~58% | ~50% | 1:1.3 → 1:1.8 | Trend | HIGH |
| 13 | ORB debit spread | Nifty vertical | ~56% | ~47% | 1:1.4 → 1:1.8 | Trend | HIGH |
| 14 | Mild diagonal | Nifty wk/next-wk | ~55% | — | 1:1.2 | Mild trend, low vol | NORMAL |
| 15 | Regime-switch composite | Overlay/router | ~53% | — | ~1:1.5 | All (router) | HIGH |

Distribution: **0 VERY HIGH · 4 HIGH · 9 NORMAL · 2 LOW.**

## Appendix B — Symbol-format quick reference

- **Kite (NFO):** `tradingsymbol` like `NIFTY{yy}{mon}{strike}{CE|PE}` / weekly variants; query `kite.instruments("NFO")` daily and match on name + expiry + strike + instrument_type.
- **Fyers:** `NSE:{NAME}{...}{CE|PE}` from `public.fyers.in/sym_details/NSE_FO.csv`; LTP via `quotes({"symbols": "NSE:..."})`.
- Always resolve at runtime from the master; never hard-code.

## Appendix C — Glossary

ATM/ITM/OTM (moneyness); CPR (Central Pivot Range); RR (reward:risk); HW/HR (High-Win / High-RR profile); theta (time decay); IV (implied volatility); XIRR (annualized return); MIS/INTRADAY (intraday product); SL-M (stop-loss market); CI (estimate range, not a measured statistical interval).
