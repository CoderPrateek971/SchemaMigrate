"""Run the SchemaMigrate Real-Time Web Dashboard (nested runner)."""
import os
import sys

# Ensure parent directory is in sys.path
parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent not in sys.path:
    sys.path.insert(0, parent)

from mainWeb import main

if __name__ == "__main__":
    main()
