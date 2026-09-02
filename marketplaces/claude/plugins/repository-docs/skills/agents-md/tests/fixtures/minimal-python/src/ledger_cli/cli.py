from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger-check")
    parser.add_argument("command", choices=("validate",))
    parser.add_argument("path")
    return parser


def main() -> int:
    parser = build_parser()
    parser.parse_args()
    return 0
