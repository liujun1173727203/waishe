from __future__ import annotations

import sys

from demo_pickup_test import main


if __name__ == "__main__":
    if not any(arg == "--test-device-input-type" or arg.startswith("--test-device-input-type=") for arg in sys.argv):
        sys.argv.extend(["--test-device-input-type", "LineIn"])
    sys.exit(main())
