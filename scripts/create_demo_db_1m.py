"""Person B (Prateek): Seed demo database with exactly 1,000,000 customer rows (1 Million / 10 Lakh)."""
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.create_demo_db import main

if __name__ == "__main__":
    if "--rows" not in sys.argv:
        sys.argv.extend(["--rows", "1000000"])
    main()
