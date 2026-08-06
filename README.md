<div align = "center">

# ✨ Automatic Trading ✨

</div>

<div align = "justify">

**`AlgoTraders`** tries to provide an uniform platform that can be integrated with any *Brokers API* to do automatic trading
based on algorithims and data-driven decisions. The repository is written in Python and list of available brokes are available
in the documentation.

## 🚀 Getting Started

The library is consumed in process by a host application. It fetches, normalizes and returns; the host decides what to do
with the result. It never opens a database connection, never blocks on input, and never opens a connection the caller did
not ask for.

```shell
$ pip install algotraders[fyers]     # or [kite], or [all]
$ pip install algotraders[server]    # adds the optional FastAPI router
```

```python
import algotraders

from algotraders.models import Timeframe

session = await algotraders.initialize(broker = "fyers")

instrument = await session.resolver.resolve("NSE:SBIN:EQ")
candles = await session.api.fetchCandles(
    instrument = instrument, timeframe = Timeframe.MINUTE_1,
    dateRange = (start, end)
)

candles[0].toRecord()   # {'instrument_key': ..., 'open_price': ..., ...}
```

On the first call of a trading day there is no stored token, so `initialize` raises `AuthorizationRequired` carrying a
login URL rather than prompting. The host redirects the user, receives the code on its own callback route, and calls
`initialize(authCode = ...)`. See [examples/host/app.py](examples/host/app.py) for the full integration, including where
the SQLAlchemy work lives.

## ⚖ Project Licensing

This project is a composite work, and its licensing reflects this structure. The project as a whole is licensed under the
**GNU General Public License, version 3 (GPLv3)**, the full text of which can be found in the `LICENSE` file in the
root directory of this repository.

Certain subdirectories of this project contain distinct components that are licensed separately from the main GPLv3 license.
This is intended to allow for more permissive use of these specific components in other contexts.

### ⚖️ Legal Obligations

The use of the GPLv3 for the main project has specific implications for the entire work. While some components are available
under the more permissive MIT License, the GPLv3's copyleft provisions mean that any distributed derivative work of the project
as a whole must also be licensed under the GPLv3 [21, 26, 27].

Users and contributors should be aware of the following:

  * **Distribution:** If you distribute this project, or a modified version of it, you must do so under the terms of the GPLv3.
    [30] This includes making the complete corresponding source code available.
  * **Contributions:** Any contributions to the main body of the project will be licensed under the GPLv3.
  * **Component-Specific Licenses:** Parts of the code with different component specific licensing can be used in accordance
    with its terms when taken separately. [30] However, when these components are part of this larger GPLv3-licensed project,
    the distribution of the combined work is governed by the GPLv3. [21]

### ⚖️ Component Specific Licensing

Certain subdirectories of this project contain distinct components that are licensed separately from the main GPLv3 license.
This is intended to allow for more permissive use of these specific components in other contexts.

  * **/algotraders/brokers/fyers/** The components within this directory are licensed under the **MIT License** which is the
    default LICENSE of the optional dependency [**`fyers_apiv3`**](https://pypi.org/p/fyers-apiv3/). For the full license text
    and copyright notices applicable to these components, please see the `LICENSE-MIT` file.
  * **/algotraders/brokers/kite/** The components within this directory are licensed under the **MIT License** which is the
    default LICENSE of the optional dependency [**`kiteconnect`**](https://pypi.org/p/kiteconnect/). For the full license text
    and copyright notices applicable to these components, please see the `LICENSE-MIT` file.

For any questions regarding the licensing of this project, please open an issue in the repository.

</div>
