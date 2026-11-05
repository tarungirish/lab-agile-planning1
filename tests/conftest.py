import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
# Most tests exercise admin routes directly; the fail-closed behaviour has its own tests.
os.environ.setdefault("MICROMARKET_ALLOW_OPEN_ADMIN", "1")
