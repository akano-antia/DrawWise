# DrawWise 5.4.0 — Maximum Intelligence

DrawWise 5.4.0 upgrades the personal Smart Pick workflow from individual-line ranking to whole-portfolio mathematical optimisation.

## What is new

- **Maximum Intelligence** is now the default Smart Pick engine.
- **Whole-portfolio optimisation**: lines are selected together rather than independently.
- **Balanced overlap control**: convex penalties strongly discourage near-duplicate tickets while allowing useful low overlap.
- **Randomness Gate**: historical frequency is automatically shrunk to a small secondary influence when stored results look close to uniform.
- **Exact hypergeometric probability engine** for main and special-ball match probabilities.
- **Monte Carlo challenge**: each generated portfolio is tested on synthetic fair draws against random portfolios using the same simulated draws.
- **Evolutionary/local refinement** after greedy construction.
- **Four objectives**: Best overall portfolio, Minimise no-win proxy, Maximise 3+ coverage, and Minimise prize sharing.
- **Game-specific special-ball diversification** remains active, including broad Life Ball / Thunderball / Lucky Star coverage across multi-line portfolios.
- Smart Ensemble remains available as a baseline strategy in Advanced Tools.

## Important mathematical boundary

For a fair lottery, every complete valid line has the same jackpot probability. DrawWise does not claim to predict the next draw or magically change that per-line probability. Maximum Intelligence optimises how a fixed budget of distinct lines is arranged for coverage, overlap, match-threshold performance and crowd-sharing risk.

## Run

Double-click `RUN_DRAW_WISE.bat`.

## Test

Double-click `RUN_TESTS.bat`. The V5.4 regression suite contains 59 tests.

## Windows build

`BUILD_DESKTOP_APP.bat` creates a portable Windows build. If an installer toolchain is present, the target installer name is `DrawWise-Setup-5.4.0.exe`.

## 5.4.2 — AI Strategy Copilot

DrawWise 5.4.2 opens directly into **Maximum Intelligence**.  The mathematical
portfolio engine remains authoritative for ticket selection.  A side-by-side
Strategy Copilot then audits the generated portfolio.

### Without any AI account
The right-hand panel always provides a deterministic local strategy review of:
- pair/triple coverage efficiency
- portfolio overlap
- crowd-pattern / prize-sharing risk
- randomness-gate historical weight
- Monte Carlo random-portfolio challenge

### Optional OpenAI review
Click **Connect / change AI** and paste an OpenAI API key.  The key is kept only
in memory for the current DrawWise session and is not written to disk.  If the
`OPENAI_API_KEY` environment variable already exists, DrawWise uses it
automatically.  `DRAWWISE_AI_MODEL` can override the default `gpt-5.6-luna`.

The cloud reviewer receives only the game, generated tickets and portfolio
metrics.  It critiques the math result; it does not generate replacement
numbers and it is instructed not to claim that AI can predict a fair draw.
