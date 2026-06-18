import sys
from pathlib import Path

# Put this demo's folder on sys.path so `from agent import ...` resolves to
# demos/<this-slug>/agent.py. The slug is this conftest's parent directory name.
_slug = Path(__file__).parent.name
sys.path.insert(0, str(Path(__file__).parents[2] / "demos" / _slug))
