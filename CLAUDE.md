<div align = "center">

# CLAUDE.md

</div>

<div align = "justify">

Guidance for Claude Code (claude.ai/code) when working in this repository. Read this file before touching any code, and
treat the rules in it as binding rather than advisory.

## Purpose and Non Goals

`algotraders` is a broker agnostic integration library. It is consumed **in process** by a separate FastAPI + SQLAlchemy
+ asyncpg backend that serves a Next.js frontend. The library fetches, normalizes and returns data; the host application
decides what to do with it.

Three rules define the boundary. Breaking any of them is a design defect, not a preference:

  1. **The library never touches a database.** No SQLAlchemy, no DSN, no `to_sql`. It returns Pydantic models and the
     host's ORM persists them. If a change makes the library aware of a table, the change is wrong.
  2. **The library never blocks on stdin.** No `input()`, no `getpass`, no interactive prompt anywhere. A web backend
     cannot answer a console prompt. OAuth codes arrive as function arguments supplied by the host's callback route.
  3. **The library never opens a connection the caller did not ask for.** No implicit refresh on import, no background
     poller started by a constructor, no telemetry.

Non goals: strategy logic, backtesting, portfolio accounting and order scheduling all belong to the host application or
to a package layered on top. `algotraders/strategies/` stays an empty documented extension point.

## Commands

```shell
$ pip install -e .[dev]         # library, test tooling and the fastapi extra
$ pip install -e .[all]         # everything including both broker SDKs
$ flake8 .                      # must report zero findings, CI gates on this
$ pytest                        # full suite, "live" tests deselected by default
$ pytest -m live                # opt in, needs real credentials, never in CI
$ pytest --cov=algotraders --cov-report=term-missing
$ python -m build               # sdist and wheel
```

`flake8 .` gates in CI as of the microservice refactor. It previously ran with `--exit-zero`, so `.flake8` was
documentation rather than a check, and the repository accumulated findings behind it.

## Architecture

### Target Layout

```text
algotraders/
    __init__.py             # version, initialize, Broker, DTO re-exports
    api.py                  # public surface shim
    config.py               # BrokerCredentials, RuntimeConfig, os.getenv reads
    logger.py               # getLogger, RedactingFilter, maskSecret
    errors/                 # AlgoTradersError tree
    models/                 # pure pydantic DTOs, the host facing contract
    core/                   # clock, ratelimit, retry, chunking, executor, resample
    auth/                   # BaseTokenStore, FileTokenStore, BrokerSession, initialize
    instruments/            # BaseInstrumentResolver, InstrumentCache
    brokers/                # base ABCs, registry, fyers/, kite/
    streaming/              # BaseBrokerStream, sinks, ThreadLoopBridge
    strategies/             # empty extension point
    transport/              # optional fastapi APIRouter
```

### Layering Rules

Imports flow one way only. A test enforces this, so a violation fails CI rather than review:

| Package | May Import |
| :---: | --- |
| `models` | `errors` only |
| `core` | `models`, `errors` |
| `auth`, `instruments`, `streaming` | `core` and below |
| `brokers/*` | everything above |
| `transport` | everything |

**No module may import a broker SDK at package import time.** Broker implementation modules under `brokers/fyers/` and
`brokers/kite/` are the sole exception, because the registry only reaches them after the extra has been resolved.
`tests/unit/test_imports.py` reimports the package with `fyers_apiv3` and `kiteconnect` hidden behind a `sys.meta_path`
blocker and asserts the import still succeeds.

### Broker Registry

`BrokerRegistry` is a module level dict of frozen `BrokerRegistryEntryPoint` dataclasses, populated at import time by
`registerBroker()` in `algotraders/brokers/__init__.py`. Entry points store **strings**, not callables; the module is
resolved lazily through `importlib.import_module` inside `getBrokerInterface(brokerName)`. That is what lets a Kite only
deployment skip the Fyers SDK entirely: a missing dependency raises `BrokerConfigurationError` when that broker is
requested, never at `import algotraders`.

`Broker` is a `str` enum covering the built in brokers only. It exists for ergonomics and autocomplete. The dict remains
the runtime registry so third party brokers can register themselves, and every public entry point accepts
`Union[str, Broker]`.

## Public API Surface

Everything not listed here is internal and may change without notice:

  * `algotraders.initialize(...) -> BrokerSession` and the `initializeSync` wrapper.
  * `algotraders.BrokerSession` and its `auth`, `api`, `resolver`, `stream()` members.
  * `algotraders.Broker`, `availableBrokers()`, `getBrokerInterface()`.
  * Everything exported from `algotraders.models`.
  * `algotraders.transport.fastapi.createRouter` under the `server` extra.

`initialize` is a coroutine because the refresh and login-completion paths perform network I/O. Do not add a callable
module object or an `initialize = True` flag; modules are not callable without replacing `sys.modules`, which breaks
static analysis, Sphinx autodoc and pickling.

## Adding a New Broker

  1. Create `algotraders/brokers/<name>/` with `limits.py`, `mapper.py`, `auth.py`, `api.py`, `symbols.py`, `stream.py`.
  2. `limits.py` holds the rate limit table, the per timeframe chunk spans, the symbol master URLs and the token policy.
     Everything that the broker can change unilaterally lives in exactly this one file.
  3. `mapper.py` translates vendor payloads to and from the DTOs. It must reject any response that is not an explicit
     success, because both SDKs return an error dict rather than raising.
  4. Register the broker in `algotraders/brokers/__init__.py` with its capabilities and the name of its pip extra.
  5. Add the extra to `pyproject.toml`.
  6. Add a licence clause to `README.md` if the broker SDK carries its own terms.
  7. Write tests against recorded payloads under `tests/data/`. No new broker ships without a mapper test built from a
     real captured response.

## Code Style

Read `~/.claude/skills/python-code-format/SKILL.md` in full before writing Python. The rules that differ from stock
PEP 8 and that reviewers get wrong most often:

  * **`camelCase` for functions, methods, parameters and locals.** The package is camelCase throughout and the skill's
    preserve-existing-convention rule applies. `PascalCase` for classes. `SCREAMING_SNAKE_CASE` for constants. Vendor
    payload keys and database column names stay `snake_case` because they are not ours to rename.
  * **A space before the colon in annotations**: `symbol : str`, and spaces around `=` in defaults and keyword
    arguments: `maxConcurrency : int = 5`. Annotate every parameter and every return, including `-> None`.
  * **Two blank lines between methods inside a class**, one blank line between the import block and the first class.
    Both differ from PEP 8, which is why `.flake8` suppresses E302 and E303.
  * reST docstrings with `:type  arg:` (two spaces), `:param arg:`, `:rtype:`, `:returns:`, `:raises X:`, bold section
    headers and `.. code-block:: python` samples.
  * Comment markers: `# !` for something the reader must not miss, `# ?` for a decision or a reference, and
    `# ..versionchanged:: yyyy-mm-dd Title Case Summary` for behavioural changes.
  * 88 column limit. `.flake8` also suppresses E203, E221, E251 and E261.
  * **Never use an em dash.** Use a hyphen. This applies to code, comments, docstrings and Markdown alike.

## Async Rules

The original implementation wrapped a blocking `FyersModel.history()` call in `async with self.semaphore`, which made
the semaphore decorative and the concurrency imaginary. The replacement rules:

  * **Never block the event loop.** Every vendor SDK call goes through `BlockingCaller.call(...)`, which composes retry,
    rate limiting, a semaphore and an explicit `ThreadPoolExecutor`.
  * **Pass an explicit executor.** `asyncio.to_thread` uses the default executor, capped at `min(32, cpu + 4)` threads.
    Without an executor sized to `maxConcurrency`, the semaphore admits work that then queues invisibly on 32 threads.
  * **Every call declares an `EndpointClass`** so it is charged against the right rate limit bucket.
  * **Order endpoints never auto retry.** A `placeOrder` that times out may already have reached the exchange; retrying
    it risks a double fill. `EndpointClass.ORDER` uses `RetryPolicy(maxAttempts = 1)` and a documented reconcile path.
  * **Rate limit buckets are shared, not per endpoint.** Fyers publishes one app wide quota; building a separate bucket
    per endpoint class enforces it several times over and is what produced 170,132 HTTP 429 responses in the captured
    log.

## Secret Handling

A broker access token is a bearer credential that can place live orders. Treat it accordingly:

  * Credentials are `SecretStr`. Read configuration through `os.getenv` in `config.py`, never inline.
  * Log through `algotraders.logger.getLogger`, never `logging.getLogger`. The redacting filter must be attached to
    every child logger, because a filter on a parent does not apply to records propagating up from a child.
  * The library attaches a `NullHandler` only and never calls `logging.basicConfig`.
  * No token in a `__repr__`, a log record, an exception message or a URL query string. OAuth codes travel in a POST
    body precisely because query strings land in proxy and access logs.
  * Always pass `log_path` to the Fyers SDK. Left unset it writes `fyersApi.log` into the process working directory,
    which is how a 570,646 line log file ended up committed to `examples/fyers/`.
  * `FileTokenStore` is the development default only. It is split brain under multi worker uvicorn. Production hosts
    implement `BaseTokenStore` over their own Postgres; it is three synchronous methods.

Environment variables, all optional at import time and validated on use:

```text
ALGOTRADERS_FYERS_CLIENT_ID     ALGOTRADERS_KITE_API_KEY
ALGOTRADERS_FYERS_SECRET_KEY    ALGOTRADERS_KITE_API_SECRET
ALGOTRADERS_FYERS_REDIRECT_URL  ALGOTRADERS_KITE_REDIRECT_URL
ALGOTRADERS_FYERS_PIN
ALGOTRADERS_TOKEN_DIR    ALGOTRADERS_CACHE_DIR    ALGOTRADERS_LOG_DIR
```

## Testing Rules

  * Fakes only. A unit test that needs a broker is a design smell; inject a fake session, a recorded payload or both.
  * `tests/conftest.py` ships two autouse fixtures. `noNetwork` replaces `socket.socket.connect` with a raising stub,
    and `dummyEnvironment` scrubs every `ALGOTRADERS_*` variable from the developer's shell before seeding throwaway
    values under `tmp_path`. The scrub matters more than the seed: without it a local run could read, or overwrite, a
    real token file.
  * Anything time dependent uses `FakeClock`. Rate limit and retry tests assert an exact sleep sequence, not a
    tolerance, and the suite stays in the millisecond range.
  * Recorded vendor payloads live in `tests/data/` rather than hand written dicts, so a mapper test fails when the
    vendor response shape drifts.
  * One test file per module under test. Coverage gate starts at 80 percent; `models/`, `core/` and every `mapper.py`
    should sit above 95 percent because they are pure functions.

## Canonical Contracts

### Timeframe Mapping

`Timeframe` values match the host's existing `private.timeframe_value` Postgres enum exactly, and a test asserts that
equality. Broker mapping lives in `brokers/<name>/mapper.py`, never on the enum itself, or `models` would have to
import broker code.

| Canonical | Fyers Resolution | Kite Interval |
| :---: | :---: | :---: |
| `1m` | `1` | `minute` |
| `3m` | `3` | `3minute` |
| `5m` | `5` | `5minute` |
| `15m` | `15` | `15minute` |
| `1h` | `60` | `60minute` |
| `1D` | `D` | `day` |
| `1M` | not supported | not supported |
| `1Y` | not supported | not supported |

`1M` and `1Y` raise `UnsupportedTimeframe`. Passing `allowAggregation = True` folds them from `1D` through
`core/resample.py`. The default is a hard error because fabricating a candle the broker never sent is worse than
failing. `1h` buckets are not comparable across brokers because bucket anchoring differs, which is why `source` is a
mandatory field on `Candle`.

### Historical Chunk Spans

A request exceeding these spans is rejected by the broker, so `fetchCandles` splits the requested range before
dispatching. Kite caps per interval; Fyers caps by resolution family.

| Timeframe | Fyers | Kite |
| :---: | ---: | ---: |
| `1m` | 100 days | 60 days |
| `3m`, `5m` | 100 days | 100 days |
| `15m` | 100 days | 200 days |
| `1h` | 100 days | 400 days |
| `1D` | 366 days | 2000 days |

### Rate Limits

| Broker | Limits | Scope |
| :---: | --- | --- |
| Fyers | 10/second, 200/minute, 100,000/day | One shared quota across all endpoints |
| Kite | 1/second quote, 3/second historical, 10/second order, 10/second other | Per API key, aggregated across users |

## Broker Fact Sheet

Verified 2026-08-05 against the vendor documentation. **These values move.** Re-verify before relying on them, and
update the date when you do.

### Fyers API v3

SDK `fyers-apiv3`. Auth is an authorization code flow: `SessionModel(...)` then `generate_authcode()`, the user
authenticates in a browser, the code returns to the registered redirect URI, then `set_token(code)` and
`generate_token()` yield an access token and a refresh token. Access tokens last one day per SEBI regulation; refresh
tokens last 15 days and require a PIN. Symbols use `<exchange>:<symbol>-<class>`, for example `NSE:SBIN-EQ`. Symbol
masters are public CSVs under `https://public.fyers.in/sym_details/`. Websockets: `data_ws.FyersDataSocket` with
`SymbolUpdate` and `DepthUpdate` types and roughly 200 symbols per connection, plus `order_ws.FyersOrderSocket`.

Never compute `appIdHash` by hand. It is `sha256(f"{clientId}:{secretKey}")`, colon separated, and getting it wrong
produces a 401 that looks like bad credentials. Use the SDK's own token generation.

### Zerodha Kite Connect v3

SDK `kiteconnect`. Auth redirects to `https://kite.zerodha.com/connect/login?v=3&api_key=...`, returns a
`request_token`, and `generate_session(requestToken, apiSecret)` exchanges it. Access tokens expire at 06:00 IST the
next day. There is no usable refresh flow, so a daily interactive login is unavoidable.

Two facts drive the design. **Historical data is keyed on an integer `instrument_token`, not a symbol string**, which
is the entire reason the instrument resolution layer exists; and **historical data is a paid add on**, so an account
without it fails every call with a permission error that maps to `DataSubscriptionRequired` rather than a retry.
Futures and options `instrument_token` values are flushed at every expiry, which is why the instrument cache is
invalidated per trading day rather than on a rolling TTL. Websocket `wss://ws.kite.trade` carries 3000 instruments per
connection, 3 connections per API key, in `ltp`, `quote` and `full` binary modes.

## Relationship to the Options Buying Strategy Specification

`docs/Options Buying Strategy Implementation Specification.md` predates this refactor and prescribes a competing broker
layer: `snake_case` naming, a synchronous `BrokerAdapter`, `place_spread`, `OrderResult` and credentials living in a
`config.py` constant. **This file is the source of truth.** Map that document's names onto the current ones as follows:

| Specification | This Library |
| :---: | :---: |
| `BrokerAdapter` | `BaseBrokerAPI` |
| `OrderResult` | `OrderResponse` |
| `place_spread` | reserved `placeBasket` seam, not implemented |
| `core/strategy.py` | host application, not this package |

Multi leg orders are deliberately out of scope. Fyers exposes `place_multileg_order` and Kite does not, so a single
`placeOrder` signature cannot express a spread honestly. The seam is reserved behind a capability flag.

## Build Status

The microservice refactor lands phase by phase on `refactor/microservice`. Each phase is one commit that is lint clean
and test green on its own.

  - [x] Phase 0 - repository hygiene, dependency policy, gating CI, pytest harness, this document
  - [x] Phase 1 - error tree, redacting logger, configuration, clock
  - [x] Phase 2 - normalized Pydantic DTOs
  - [x] Phase 3 - rate limiter, retry, chunking, blocking executor
  - [x] Phase 4 - token store, `BrokerSession`, `initialize`
  - [x] Phase 5 - instrument resolution and cache
  - [x] Phase 6 - broker ABCs, registry hardening, Fyers data
  - [x] Phase 7 - layering enforcement and Fyers trading
  - [x] Phase 8 - streaming layer and Fyers stream
  - [x] Phase 9 - Zerodha Kite
  - [x] Phase 10 - Kite streaming
  - [x] Phase 11 - FastAPI transport
  - [x] Phase 12 - examples, documentation, version bump

## Things Discovered During the Refactor

Recorded because each cost real debugging time and none is obvious from reading the code afterwards.

  * **The layering rule needs a test, not a paragraph.** `models/market.py` imported `core.clock` for a timezone
    helper, which inverted the documented direction. Nothing broke until `core/__init__.py` grew one more export, at
    which point the cycle surfaced as an `ImportError` in an unrelated test module and blamed the wrong file. The
    timezone primitives now live in `models/timezone.py` at the bottom of the graph, and
    `tests/unit/test_imports.py::test_layeringRulesAreRespected` walks the source of each package to enforce it.
  * **Response validation must run inside the retried operation.** Validating after `caller.call` returned put the
    error classification outside the retry boundary, so a throttled response was never retried at all, which defeats
    the entire reason 429 is mapped to a retryable type. Both broker surfaces now validate within the callable they
    hand to the caller.
  * **Token bucket arithmetic needs an epsilon.** Sleeping for exactly `timeToAvailable()` credits back exactly the
    deficit only in exact arithmetic. In floating point the balance lands at `0.9999999999999998`, which reads as "not
    yet" and sends `acquire` round again for a meaningless sub-nanosecond sleep. That produced 26 sleeps for 5
    acquisitions and would have polluted every downstream timing assertion.
  * **A broker package cannot eagerly import its own submodules.** The registry reads a broker's capabilities from its
    `limits.py` during registration, and `limits.py` deliberately has no vendor import, but importing it executes the
    package `__init__` which would drag the SDK in. Both broker packages resolve their concrete classes through a
    PEP 562 module level `__getattr__`.
  * **Blocking the loopback interface breaks asyncio itself.** The `noNetwork` fixture originally blocked every
    outbound connect, including the socketpair `ProactorEventLoop` uses for its self-pipe, so every coroutine test
    errored on Windows before it ran.

</div>
