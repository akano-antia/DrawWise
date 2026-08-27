from __future__ import annotations

import argparse


def main():
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
