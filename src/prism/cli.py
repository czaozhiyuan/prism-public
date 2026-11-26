"""Explicit retirement notice for the pre-v4 CLI module."""
import sys


def main(argv=None):
    print("旧 automation v1 / CLI 已停用；请使用 python -m prism_agent.cli。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
