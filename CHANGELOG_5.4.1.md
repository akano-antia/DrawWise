# DrawWise 5.4.1 — Maximum Intelligence + AI Strategy Copilot

## Primary release fixes
- Installed `DrawWise.exe` is now hard-wired to open the Maximum Intelligence Smart Pick interface.
- Advanced Tools remains available inside the app and through `RUN_ADVANCED.bat` for source/developer runs.
- Increased primary window width to support the new side-by-side copilot without clipping ticket cards.

## AI Strategy Copilot
- Added a permanent side-by-side Strategy Copilot panel.
- Every generated portfolio receives an immediate deterministic local audit of coverage, overlap, crowd risk, randomness-gate weight and Monte Carlo challenge result.
- Optional OpenAI review uses the Responses API after the mathematical engine has finished.
- Cloud AI never generates or replaces ticket numbers; it critiques the portfolio and can recommend keep/regenerate/objective changes.
- API keys are read from `OPENAI_API_KEY` or pasted for the current session only; DrawWise does not save pasted keys to disk.
- Default optional cloud model is `gpt-5.6-luna`, overridable with `DRAWWISE_AI_MODEL`.
- Only generated tickets and portfolio metrics are sent to the AI reviewer.

## Mathematical authority
- Maximum Intelligence remains the selection authority.
- AI cannot change fair-draw jackpot odds and is explicitly prompted not to invent predictive claims.
