# DrawWise — Advanced verified-results guide

## Safety contract

DrawWise never writes online data merely because a network request succeeded.

1. Fetch a configured official source.
2. Identify XML / CSV / HTML before parsing.
3. Validate complete draw records against the selected game rules.
4. Compare with the local history.
5. Preview local/official dates and new/corrected counts.
6. Require explicit user approval.
7. Back up the current CSV.
8. Merge the checked records.

If any stage fails, local history stays unchanged.

## Five physical feeds / seven games

The updater has five result feeds:

- Lotto -> also provides Lotto HotPicks results
- EuroMillions -> also provides EuroMillions HotPicks results
- Set For Life
- Thunderball
- Powerball / MUSL

HotPicks rows are derived from their parent draw and do not make a second network request.

## National Lottery parser behaviour

DrawWise tries the official XML history endpoint and then the official CSV history endpoint.
It can read comma, semicolon, tab and pipe delimited files, UTF-8 BOM files, and common header
aliases. It also understands a conservative XML number-set shape, including Lotto round IDs.

If a structured endpoint actually returns HTML, or if neither structured format can be mapped
safely, the selected row shows a source error and **Save diagnostic** becomes available. Save
the raw response and companion parser notes when troubleshooting.

DrawWise does not bypass HTTP 403 or other website access controls.

## Powerball dates

The canonical date is the official U.S. Powerball draw date. An older local record with the exact
same five white balls + Powerball and a date exactly one day later is shown as an official-date
normalisation rather than added as a duplicate.
