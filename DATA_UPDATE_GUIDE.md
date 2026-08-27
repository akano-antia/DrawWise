# Updating and expanding DrawWise history — Advanced tools

## Recommended workflow

1. Select the correct game.
2. Open **Data Manager**.
3. Check the **Rule-era guide**.
4. Use **Import file** for one CSV or **Import folder** for a year/archive folder.
5. Review accepted/rejected rows and the rule-era classification.
6. Confirm the merge only after the preview looks correct.
7. DrawWise backs up the existing local history before writing.
8. Re-open Data Manager and verify quality, analysis-ready depth and era counts.

## Accepted column aliases

DrawWise accepts canonical headers plus conservative aliases such as:

- `DrawDate`, `Draw Date`, `Date`
- `Ball 1`, `Ball1`, `Number1`, `Main1`
- `Lucky Star 1`, `Star1`
- `Powerball`, `Power Ball`, `PB`
- `Life Ball`, `LifeBall`
- `Thunderball`, `Thunder Ball`
- optional `Round`, `DrawNumber`, `Draw Number`

## Rule-era policy

Structurally valid legacy rows may be stored, but current generation, Number Analysis,
Backtest and Strategy Lab use only the configured current analysis universe.

This is particularly important for EuroMillions Lucky Stars and historical Powerball
matrices, where the size of the special-ball pool changed over time.

## Same-date Lotto rounds

Current UK Lotto can contain two legitimate rounds on the same draw date. When importing
multiple same-date rows, include `Round` and/or `DrawNumber`. Ambiguous same-date rows without
identity are rejected rather than guessed.
