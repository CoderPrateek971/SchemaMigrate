"""Person B (Prateek): Seed demo database with exactly 10,000 customer rows."""
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.create_demo_db import main

if __name__ == "__main__":
    if "--rows" not in sys.argv:
        sys.argv.extend(["--rows", "10000"])
    main()
