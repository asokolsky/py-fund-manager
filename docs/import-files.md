# Import Files

This document is the authoritative contract for files accepted by import
commands. Imported files are validated application inputs, distinct from the
canonical manifests and ledgers described in the [storage contract](README.md).

## Supported files

| File | Command | Purpose |
| --- | --- | --- |
| Opening snapshot CSV | `portfolio create ID --balance=@FILE` | Establish opening cash and positions at one account boundary. |
| Activity CSV | `portfolio import ID FILE` | Append independently timestamped broker events. |

Portfolio creation also accepts inline opening facts through
`--balance=ASSET:VALUE,...`. This path writes the canonical transaction ledger
without importing or preserving a source file; see the
[portfolio CLI guide](cli-portfolio.md#create-a-portfolio).

Broker-native exports are not accepted directly. They require an adapter that
produces one of the canonical formats documented here. No broker-specific import
adapter exists yet.

## Common behavior

- The source file must exist and be a regular file.
- CSV files may include a UTF-8 byte-order mark.
- Blank optional fields are treated as absent.
- Validation happens before the transaction ledger or preserved source is written.
- A successful import copies the source into the portfolio's `imports/`
  directory under its original basename for audit purposes.
- An existing preserved source is never replaced.
- Validation errors identify the source row when applicable.

Real statements and account data belong in a private data root, not this public
repo. See the [sample-data guide](../sample-data/README.md#store-real-data-in-a-private-repo).

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
New events that predate the latest ledger event also fail; import broker activity
in chronological batches.

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
