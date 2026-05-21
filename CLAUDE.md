# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install (development mode):**
```
pip install -e .
```

**Lint:**
```
flake8 .
```

**Build distribution:**
```
python -m build
```

There is no test suite yet (v0.0.1.dev0).

## Architecture

The package provides a unified broker-agnostic API for algorithmic trading. The key design constraints are:

- **Async-first**: `BaseBrokerAPI.fetchData()` and `executeOrder()` are async. Concurrency is rate-limited using `asyncio.Semaphore(maxConcurrency)` — always acquire it with `async with self.semaphore:` inside concrete `fetchData()` implementations.
- **Zero base dependencies**: `dependencies = []` in `pyproject.toml` is intentional. Broker-specific libraries (e.g., `fyers_apiv3`) are optional and validated at runtime by the registry.
- **Separation of auth from API**: `BaseBrokerAuthentication` handles login/logout (synchronous, one-time per session). `BaseBrokerAPI` handles data fetching and order execution (async, session-scoped). These are two separate concrete classes per broker.

### Broker Registry (`algotraders/brokers/registry.py`)

`BrokerRegistry` is a module-level dict of `BrokerRegistryEntryPoint` frozen dataclasses. Brokers are registered at import time in `algotraders/brokers/__init__.py` via `registerBroker()`. The registry uses lazy imports (`importlib.import_module`) so missing broker libraries only raise `BrokerConfigurationError` when that specific broker is requested via `getBrokerInterface(brokerName)`, not at package import time.

### Adding a New Broker

1. Create `algotraders/brokers/<brokername>.py` implementing `BaseBrokerAuthentication` and `BaseBrokerAPI`.
2. Register in `algotraders/brokers/__init__.py`:
   ```python
   registerBroker(
       "brokername",
       API="BrokernameAPI",
       AUTH="BrokernameAuthentication",
       PATH="algotraders.brokers.brokername",
       REQUIRES=["external_lib_name"],
   )
   ```
3. The `REQUIRES` list is used in error messages when the dependency is missing; the actual import check is done by catching `ImportError` during `importlib.import_module`.

### Fyers Implementation (`algotraders/brokers/fyers.py`)

Symbol format: `<exchange>:<symbol>-<class>` (e.g., `NSE:HDFCBANK-EQ`). Date inputs to `fetchData()` accept either epoch integers or `yyyy-mm-dd` strings; the implementation converts to epoch internally. Per SEBI regulations, access tokens expire daily.

## Code Style

Flake8 enforces standard PEP 8 rules. Max line length is 88 characters. `__init__.py` files are exempt from F401/F403/F405. The `examples/` directory is excluded from lint checks.

Suppressed codes (personal preference): E203 (whitespace before symbols), E221 (multiple spaces before operator), E251 (spaces around keyword equals), E261 (two spaces before inline comment).
