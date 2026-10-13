from ledger_cli.cli import build_parser


def test_build_parser_accepts_validate_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["validate", "tests/fixtures/small-ledger.csv"])
    assert args.command == "validate"
