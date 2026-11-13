"""Frozen desktop backend. Uses the same CLI/stdio entry point as feiyu."""
from feiyu.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
