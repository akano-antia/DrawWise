# DrawWise 4.1 — Source Parser & Updater Reliability

## Fixed
- National Lottery updater no longer assumes a successful `/csv` response is actually a comma-delimited CSV table.
- Detects HTML, XML and delimited text before parsing.
- Tries the official XML history endpoint and then the CSV history endpoint.
- CSV parser accepts comma, semicolon, tab and pipe delimiters plus UTF-8 BOM / Windows text encoding fallback.
- Broader date and number-column aliases are recognised.
- National Lottery XML parser supports nested result records and metadata/ball-set sibling layouts.
- Two-round Lotto XML keeps `Round` and `DrawNumber` identity and ignores the non-selected bonus ball.
- HotPicks are treated as derived views of Lotto / EuroMillions rather than separate physical result feeds.
- Results Updater now reports 5 result sources / 7 supported games.
- Source failures can preserve the raw response for **Save diagnostic**.
- Source checks retry transient failures but do not retry/bypass HTTP 403 access controls.
- Powerball histories under 100 compatible draws show a stronger stability warning.

## Safety unchanged
`CHECK -> VALIDATE -> PREVIEW -> YOU APPROVE -> BACKUP -> MERGE`

A source check never changes local history by itself.

## Verification
50 automated tests pass, including XML parsing, two-round Lotto parsing, delimiter detection, HTML-to-alternate-feed fallback, diagnostics, Powerball date normalisation and all previous analytics/strategy tests.
