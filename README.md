# DrawWise 5.3.3 — Smart Pick Layout Fix

This release removes the recurring LINE 02 clipping issue rather than adding another canvas offset workaround.

## What changed

- The Recommended line remains pinned at the top.
- Short portfolios (3 or 5 lines) no longer use a scrolling Tk canvas at all.
- LINE 02 through LINE 05 render in a normal fixed frame, so there is no inherited y-scroll position that can crop LINE 02 on Windows.
- 10- and 20-line portfolios still use the scrolling alternatives view because scrolling is genuinely required there.
- Alternative cards are slightly more compact so all five UK Lotto lines fit comfortably at the normal desktop size.
- Smart Ensemble scoring and lottery-selection logic are unchanged.

## Run

Double-click `RUN_DRAW_WISE.bat`.

Recommended check: UK Lotto -> 5 lines -> Generate My Numbers. The screen should show, in order, RECOMMENDED, LINE 02, LINE 03, LINE 04 and LINE 05 with LINE 02 fully visible.

`BUILD_DESKTOP_APP.bat` creates a portable Windows build. If Inno Setup is installed, the build scripts can also create `DrawWise-Setup-5.3.3.exe`.
