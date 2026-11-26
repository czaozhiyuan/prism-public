"""PyInstaller launcher for PRISM."""

import sys


def main():
    if sys.argv[1:2] == ["--prism-word-worker"]:
        from prism.word_worker import main as worker_main

        try:
            return worker_main(sys.argv[2:])
        except ValueError:
            return 2
    from prism.functional_app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
