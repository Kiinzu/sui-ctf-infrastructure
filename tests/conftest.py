import os
import sys

# Make the orchestrator `app` package importable in tests.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "orchestrator"))

CHALLENGES_DIR = os.path.join(REPO, "challenges")
