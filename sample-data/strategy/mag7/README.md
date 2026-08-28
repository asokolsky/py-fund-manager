# Magnificent Seven Equal Weight

This fictional strategy invests equally in the seven companies commonly grouped
as the Magnificent Seven: Apple, Amazon, Alphabet, Meta, Microsoft, NVIDIA, and
Tesla. Six weights use `0.142857`; the final Tesla weight absorbs the one-millionth
rounding residual so the allocation totals exactly `1.0`. It is not investment
advice.

`revisions/` contains immutable, content-addressed snapshots referenced by
portfolio strategy history. `strategy.yaml` remains the editable current
definition.
