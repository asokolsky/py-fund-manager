# Import Files

This document is the authoritative contract for files accepted by import
commands. Imported files are validated application inputs, distinct from the
canonical manifests and ledgers described in the [storage
contract](portfolio-storage-validation.md).

## Supported files

| File | Command | Purpose |
| --- | --- | --- |
| Opening snapshot CSV | `portfolio create ID --balance=@FILE` | Establish opening cash and positions at one account boundary. |
| Activity CSV | `portfolio import ID FILE` | Append independently timestamped broker events. |
| Execution JSON | `portfolio import ID FILE` | Append confirmed fills emitted by a broker command. |
| IBKR monthly Activity Statement CSV | `portfolio import ID FILE` | Validate and preserve a native IBKR statement when every activity row can satisfy the ledger contract. |

Portfolio creation also accepts inline opening facts through
`--balance=ASSET:VALUE,...`. This path writes the canonical transaction ledger
without importing or preserving a source file; see the
[portfolio CLI guide](cli-portfolio.md#create-a-portfolio).

Broker-native exports require an adapter that produces canonical transactions.
The IBKR adapter recognizes monthly Activity Statement CSV files, validates their
statement and account identity, and fails before writing when a broker row cannot
supply the canonical timestamp or stable identity.

## Common behavior

- The source file must exist and be a regular file.
- CSV files may include a UTF-8 byte-order mark.
- Portfolio imports use a `.csv` or `.json` filename extension.
- Blank optional fields are treated as absent.
- Validation happens before the transaction ledger or preserved source is written.
- A successful import copies the source into the portfolio's `imports/`
  directory under its original basename for audit purposes.
- An existing preserved source is never replaced.
- Validation errors identify the source row when applicable.

Real statements and account data belong in a private data root, not this public
repo. See [Sample and Personal Data](data.md).

## Opening snapshot CSV

An opening snapshot establishes the known account state at a single point in
time. The command supplies that time with `--as-of`; when omitted, the import time
is used.

```shell
mise run py-fund-manager -- \
  portfolio create brokerage \
  --broker historical \
  --account-id brokerage-123 \
  --as-of 2020-01-02T08:00:00-08:00 \
  --balance=@/path/to/private/opening.csv
```

The timestamp must be ISO 8601 with a timezone offset. Every generated ledger
row uses the same timestamp.

### Complete `opening.csv` example

The following file initializes a USD 100,000 balance plus two security
positions:

```csv
asset,quantity,amount,cost_basis
USD,,100000.00,
AAPL,12,,2100.00
MSFT,5,,
```

### Columns

| Column | Required | Meaning |
| --- | --- | --- |
| `asset` | Yes | Currency code for cash or security ticker for a position; normalized to uppercase. |
| `quantity` | Position rows | Positive security quantity. |
| `amount` | Cash row | Nonnegative opening balance. |
| `cost_basis` | No | Nonnegative total cost basis for a position. |

The populated value column determines the row type:

| Populated column | `asset` value | Generated transaction |
| --- | --- | --- |
| `amount` | Three-letter currency code such as `USD`, `CAD`, or `AUD` | Opening cash |
| `quantity` | Security ticker such as `AAPL` or `MSFT` | Opening position |

The import file therefore does not need a separate `currency` column. A position
uses the portfolio base currency, while a cash row names that currency directly
in `asset`. Every row must populate exactly one of `quantity` or `amount`.

The CSV must contain at least one row. A cash-only snapshot is valid. It may
contain at most one cash row and at most one position row per ticker. A cash asset
must equal the portfolio base currency; position rows are valued in that base
currency.

Cash rows cannot contain quantity or cost basis. Position rows cannot contain
amount.

### Output

The import writes a canonical `transactions.csv` ledger containing one
cash or position transaction per input row. Generated IDs are sequential within
the import. The source CSV is also preserved in `imports/`.

See [Transaction schema](schemas.md#transaction) for the resulting ledger
contract.

## Activity CSV

An activity CSV updates an existing portfolio after its opening boundary:

```shell
mise run py-fund-manager -- \
  portfolio import brokerage \
  /path/to/private/activity-2020-03.csv
```

Each row is one confirmed broker event. A dividend reinvestment is represented by
two rows because the cash credit and security purchase are separate facts.

Opening events and splits are not accepted in activity files.

### Complete `activity-2020-03.csv` example

```csv
occurred_at,event,asset,quantity,amount,price,fees,external_id
2020-03-13T09:00:00-07:00,dividend,USD,,24.60,,,etrade-dividend-84721
2020-03-13T09:01:00-07:00,buy,AAPL,0.09,,273.33,0.00,etrade-trade-84722
```

### Columns

| Column | Required | Meaning |
| --- | --- | --- |
| `occurred_at` | Yes | ISO 8601 event time with a timezone offset. |
| `event` | Yes | Supported broker event name. |
| `asset` | Yes | Currency for cash events or ticker for security events. |
| `quantity` | Security events | Security quantity. |
| `amount` | Cash events; optional for trades with `price` | Exact cash amount. |
| `price` | Trades without `amount` | Per-unit execution price. |
| `cost_basis` | No | Nonnegative total cost basis when applicable. |
| `fees` | No | Nonnegative broker fees; defaults to zero. |
| `external_id` | Yes | Stable event or execution identity assigned by the source system. |

Supported cash events are:

- `deposit`,
- `withdrawal`,
- `dividend`,
- `interest`, and
- `fee`.

Cash-event assets must equal the portfolio base currency.

Supported security events are:

- `buy`,
- `sell`,
- `transfer_in`,
- `transfer_out`, and
- `position_adjustment`.

Security events use the portfolio base currency.

Activity imports are append-only and use `external_id` for idempotency. Repeating
the same file, or importing an overlapping file containing identical known events,
skips those events. Reusing an archived filename with different content fails.
CSV rows must be chronological, and new events that predate the latest ledger
event also fail; import broker activity in chronological batches. Execution JSON
differs because broker-order arrays are sorted by execution time during import.

Buys and sells require either an exact `amount` or a `price`;
when only a price is supplied, cash movement is derived as price times
quantity. Fees are applied separately.

### Identity and repeat imports

`external_id` identifies the immutable event in the broker or source system. It
allows overlapping exports to be imported safely:

- An unseen identity is appended to the ledger.
- An existing identity with identical content is skipped.
- An existing identity with different content is rejected as a conflict.
- Repeated identities within one source file are rejected.

New events must follow the existing ledger chronologically. The complete updated
ledger is validated and replaced atomically only after every new event passes.
The source file is retained in `imports/`; give overlapping exports distinct
basenames so each can be preserved.

## Execution JSON

Execution JSON is the canonical array emitted by commands such as
`broker historical`. Import it directly without converting it to CSV:

```shell
mise run py-fund-manager -- \
  portfolio import brokerage executions-2026-08-26.json
```

Each array item must match the strict `Execution` schema:

```json
[
  {
    "id": "brokerage-order-1-fill-0001",
    "order_id": "brokerage-order-1",
    "ticker": "AAPL",
    "side": "buy",
    "quantity": "2.000",
    "price": "220.00",
    "fees": "0.00",
    "currency": "USD",
    "executed_at": "2026-08-26T14:00:00-07:00"
  }
]
```

The importer maps `buy` and `sell` executions to ledger transactions. Execution
`id` becomes the stable `external_id`, enabling the same idempotency and conflict
checks as activity CSV. The execution currency must equal the portfolio base
currency, and the array must not be empty. Array items may follow broker order;
the importer writes their ledger transactions chronologically by execution time.
Quantities and prices are imported exactly as confirmed by the broker adapter.
For `broker historical`, this normally means three-decimal share quantities,
with exact remaining quantities preserved for full liquidations. Prices use
cents at or above `1.00` and four decimal places below `1.00`.

## IBKR monthly Activity Statement CSV

An IBKR portfolio uses the statement account identifier as `account_id`:

```shell
mise run py-fund-manager -- \
  portfolio create brokerage \
  --broker ibkr \
  --account-id U1234567 \
  --balance=USD:100

mise run py-fund-manager -- \
  portfolio import brokerage \
  /path/to/private/ibkr-monthly-activity.csv
```

The adapter accepts the UTF-8 byte-order mark used by IBKR and parses its
section-local `Header` and `Data` rows. Sections may be absent in months with no
corresponding activity. Summary totals, code dictionaries, and legal notes are
not ledger facts. Any other statement section is rejected until its activity
mapping is explicitly supported; the adapter never silently skips unknown
transactional content.

Before any source or ledger write, the adapter requires:

- broker name `Interactive Brokers LLC`;
- statement title `Activity Statement`;
- a valid inclusive statement period;
- an account ID equal to the selected Portfolio `account_id`; and
- a base currency equal to the Portfolio base currency.

The observed `Deposits & Withdrawals` rows contain a settlement date but no
timezone-aware event timestamp or stable broker transaction ID. Those rows are
therefore rejected rather than assigned synthetic values. A statement with no
activity can be validated and preserved with zero imported events. Import of
trades, income, fees, transfers, and corporate actions remains unsupported until
representative exports establish their exact timestamp and identity fields.

Real IBKR statements contain private account data. Keep them in a private data
root; tests use inline sanitized statements that retain only the structural
contract.
