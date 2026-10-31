"""Entry point for the packaged app: double-click to open MeshRight."""

import sys

if __name__ == "__main__":
    # Double-clicked with no options: say something before the slow imports,
    # so the window is not blank while the app loads.
    if len(sys.argv) == 1:
        print(
            "Starting MeshRight. The first start can take a minute.\n"
            "Your browser will open when it is ready. Keep this window open.",
            file=sys.stderr,
            flush=True,
        )
    from meshright.cli import main

    sys.exit(main())
