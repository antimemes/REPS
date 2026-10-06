"""Build-time schema export: python -m reps_events.export module:union."""

import importlib
import json
import sys

from .render import export_schema


def main() -> None:
    module, attr = sys.argv[1].split(":")
    print(json.dumps(export_schema(getattr(importlib.import_module(module), attr)), indent=2))


if __name__ == "__main__":
    main()
