from __future__ import annotations

import argparse
import sys


def main():
    # The installed DrawWise.exe must ALWAYS open Maximum Intelligence.
    # Advanced Tools is entered from the in-app button.  Command-line --advanced
    # is retained only for source/developer runs such as RUN_ADVANCED.bat.
    if getattr(sys, "frozen", False):
        from smart_pick_app import main as smart_main
        smart_main(None)
        return

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--advanced", action="store_true")
    parser.add_argument("--game", default=None)
    args, _unknown = parser.parse_known_args()

    if args.advanced:
        from drawwise_app import main as advanced_main
        advanced_main(args.game)
    else:
        from smart_pick_app import main as smart_main
        smart_main(args.game)


if __name__ == "__main__":
    main()
