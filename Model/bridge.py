#!/usr/bin/env python3
"""Punto de entrada del puente PHP ⇄ IA.

Uso:
    echo '{"action":"info"}' | .venv/bin/python bridge.py
    .venv/bin/python bridge.py --verbose < payload.json
"""

from solar_ai.bridge import main

if __name__ == "__main__":
    raise SystemExit(main())
