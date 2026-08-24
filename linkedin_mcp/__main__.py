"""Entry point: `python -m linkedin_mcp` (stdio server) or `--live-probe`.

The probe is a manual diagnostic. It refuses to run unless
``LINKEDIN_MCP_LIVE_PROBE=1`` is set, and it is never invoked by tests or CI.
"""

from __future__ import annotations

import argparse
import sys

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_REFUSED = 2
EXIT_AUTH_ERROR = 3  # bad/expired token — distinct from a spec drift or unknown failure


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-mcp",
        description="MCP server for propose-then-approve LinkedIn profile edits.",
    )
    parser.add_argument(
        "--live-probe",
        action="store_true",
        help=(
            "Send ONE real request to the documented profile-write endpoint and report "
            "whether the endpoint shape is right (requires LINKEDIN_MCP_LIVE_PROBE=1)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.live_probe:
        from . import probe

        try:
            result = probe.run_live_probe()
        except probe.ProbeRefused as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_REFUSED
        except probe.ProbeError as exc:
            print(f"live probe could not run: {exc}", file=sys.stderr)
            return EXIT_FAILURE
        print(probe.format_report(result))
        if not result["is_failure"]:
            return EXIT_OK
        if result["outcome"] == probe.AUTH_ERROR:
            return EXIT_AUTH_ERROR
        return EXIT_FAILURE

    from . import server

    server.run()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
