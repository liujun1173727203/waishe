from __future__ import annotations

import sys

from demo_speaker_test import main


if __name__ == "__main__":
    if not any(arg == "--test-device-output-type" or arg.startswith("--test-device-output-type=") for arg in sys.argv):
        sys.argv.extend(["--test-device-output-type", "LineOut"])
    sys.exit(main())
