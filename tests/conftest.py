import sys
from pathlib import Path

# src/*.py use bare imports (`from backend import ...`), so src/ must be on
# sys.path for `import tool_executor` to resolve its own dependencies.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
