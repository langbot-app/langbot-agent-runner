"""Pytest configuration for Dify tests."""

import pathlib
import sys

# Add project root to path for dify_agent imports
project_root = pathlib.Path(__file__).parent.parent.parent
if project_root.exists():
    sys.path.insert(0, str(project_root))

# Add _shared to path for imports
shared_path = project_root / "_shared"
if shared_path.exists():
    sys.path.insert(0, str(shared_path))
