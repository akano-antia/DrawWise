# DrawWise 5.4.0 — Maximum Intelligence

## New engine

- Added `Maximum Intelligence` as the normal Smart Pick strategy.
- Added whole-portfolio optimisation with greedy construction plus simulated-annealing/local refinement.
- Added convex pairwise-overlap penalty inspired by balanced-overlap / majorization portfolio design.
- Added multiple optimisation objectives: best overall, no-win proxy, 3+ coverage, and prize-sharing reduction.

## Probability and validation

- Added exact hypergeometric match probabilities.
- Added a Randomness Gate based on normalized entropy and marginal deviation; historical influence is capped at 20% and is normally much lower.
- Added Monte Carlo portfolio challenge against random portfolios on identical synthetic draws.
- Added a portfolio rating based on tuple-efficiency, overlap, crowd-risk and random challenge performance.
- Jackpot odds remain calculated separately from the optimisation score.

## UI

- Added optimisation-objective selector to the simple Smart Pick sidebar.
- Added History Gate, Portfolio Rating and Random Challenge summary cards.
- Added candidate/refinement/simulation counts to the result subtitle.
- Pick Again now re-evaluates the modified portfolio.

## Regression

- Existing 55 tests retained.
- Added 4 V5.4 mathematical/portfolio tests.
- Total: 59 tests.
