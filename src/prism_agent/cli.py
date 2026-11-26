"""Non-interactive JSON CLI adapter for Agent Service v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable, Dict, Iterable, Optional, TextIO

from prism.agent_contract import cli_exit_code
from prism.agent_service import create_service


INTERRUPTED_EXIT_CODE = 130


class CliArgumentError(ValueError):
    """Raised when adapter arguments cannot be converted to a service request."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep parser failures inside the JSON output path."""

    def error(self, message: str) -> None:
        raise CliArgumentError(message)


def _parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="python -m prism_agent.cli",
        add_help=False,
        description="Call one PRISM Agent Service v1 tool and emit one JSON response.",
    )
    parser.add_argument("tool", nargs="?")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--request", help="UTF-8 JSON object")
    source.add_argument("--request-file", help="Path to a UTF-8 JSON object")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    return parser


def _failure(code: str, message: str, details: Optional[Dict[str, Any]] = None,
             commit_state: str = "not_committed", status: str = "failed") -> Dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "data": None,
        "warnings": [],
        "revision": None,
        "commit_state": commit_state,
        "records_saved": None,
        "review_checklist": None,
        "error": {
            "code": code,
            "message": message,
            "retryable": False,
            "commit_state": commit_state,
            "details": details or {},
        },
    }


def _interrupted(tool: Optional[str]) -> Dict[str, Any]:
    return _failure(
        "UNKNOWN",
        "CLI execution was interrupted; the operation outcome is unknown. Reconcile with a read-only Agent Service call and stop automatic writes if the current state cannot be confirmed.",
        {
            "tool": tool,
            "outcome": "UNKNOWN",
            "reconcile": "Use project_get or the corresponding read-only tool; do not automatically replay the request.",
        },
        commit_state="unknown",
        status="unknown",
    )


def _load_request(arguments: argparse.Namespace) -> Dict[str, Any]:
    if arguments.request_file is not None:
        raw = Path(arguments.request_file).read_text(encoding="utf-8")
    elif arguments.request is not None:
        raw = arguments.request
    else:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise CliArgumentError("request JSON must be an object")
    return value


def _write_json(stream: TextIO, value: Dict[str, Any], pretty: bool = False) -> None:
    json.dump(
        value,
        stream,
        ensure_ascii=False,
        allow_nan=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    stream.write("\n")
    stream.flush()


def run(argv: Optional[Iterable[str]] = None,
        service_factory: Callable[..., Any] = create_service,
        stdout: Optional[TextIO] = None,
        stderr: Optional[TextIO] = None) -> int:
    """Execute one CLI call; injectable streams and service support adapter tests."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = _parser()
    arguments = None
    try:
        arguments = parser.parse_args(list(argv) if argv is not None else None)
        if arguments.help:
            parser.print_help(file=stderr)
            return 0
        if not arguments.tool:
            raise CliArgumentError("tool is required")
        request = _load_request(arguments)
        if arguments.verbose:
            print("PRISM CLI invoking Agent Service tool: {}".format(arguments.tool), file=stderr)
        service = service_factory()
        response = service.call(arguments.tool, request)
        _write_json(stdout, response, arguments.pretty)
        return cli_exit_code(response)
    except (CliArgumentError, json.JSONDecodeError, OSError, UnicodeError) as error:
        response = _failure("INVALID_ARGUMENT", "CLI request could not be loaded", {
            "reason": str(error),
        })
        _write_json(stdout, response, bool(arguments and arguments.pretty))
        return cli_exit_code(response)
    except KeyboardInterrupt:
        response = _interrupted(arguments.tool if arguments else None)
        _write_json(stdout, response, bool(arguments and arguments.pretty))
        print("PRISM CLI interrupted: outcome UNKNOWN; reconcile before any replay.", file=stderr)
        return INTERRUPTED_EXIT_CODE


def main(argv: Optional[Iterable[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
