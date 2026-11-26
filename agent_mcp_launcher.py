"""PyInstaller launcher for the PRISM MCP stdio server."""

import sys


def main():
    if sys.argv[1:2] == ["--prism-word-worker"]:
        from prism.word_worker import main as worker_main

        return worker_main(sys.argv[2:])
    from prism_agent.mcp_server import main as mcp_main

    return mcp_main()


if __name__ == "__main__":
    raise SystemExit(main())
