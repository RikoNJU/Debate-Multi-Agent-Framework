"""Keep tests isolated from local runtime credentials and data."""

from __future__ import annotations

import os
import tempfile

os.environ["DEBATE_RUNTIME"] = "demo"
os.environ["DEBATE_DATA_DIR"] = tempfile.mkdtemp(prefix="debate-test-data-")
os.environ.pop("DEBATE_DATABASE_URL", None)
