# DrawWise 5.1 — Smart Ensemble

- Added **Smart Ensemble** as the new personal default selection engine.
- Combined long-history, recent, combined-score, pair, balance and crowd-pattern signals instead of relying on one historical ranking.
- Added automatic small-sample shrinkage so shallow histories influence candidate scoring less strongly.
- Added candidate generation from ranked, recent, hot/cold, historically tilted and uniform pools.
- Added automatic **Best single pick** mode for one line and **Smart portfolio** mode for multiple lines.
- Preserved the top-ranked candidate as Line 1 before portfolio-diversification trade-offs are applied to later lines.
- Added **Pick again** on the recommended card; it excludes currently displayed main-number lines and returns another strong candidate.
- Improved special-ball selection so the recommended special selection is ranked first and later lines diversify the special universe.
- Enlarged the recommended card and number tiles.
- Simplified summary wording and made Advanced tools less prominent.
- Added mouse-wheel scrolling to the generated-ticket area.
- Updated Windows installer versioning to 5.1.
- Added V5.1 regression tests for all games, recommendation ordering and Pick Again exclusions.
