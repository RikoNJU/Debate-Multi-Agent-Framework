"""Load an existing paper revision and dump its DebateReviewInput JSON for a fresh re-run."""
from __future__ import annotations

import sys
from pathlib import Path

from debate_agent_framework.config import DebateWebSettings
from debate_agent_framework.persistence import Database, PaperRepository
from debate_agent_framework.services.paper_storage import PaperPersistenceService

REVISION_ID = sys.argv[1]

settings = DebateWebSettings.from_env()
data_dir = Path(settings.data_dir).resolve()
db = Database(settings.resolved_database_url())
repo = PaperRepository(db)
persistence = PaperPersistenceService(data_dir, repo)
review_input = persistence.load_review_input(REVISION_ID)
out = data_dir / ".staging" / f"{REVISION_ID}.review_input.json"
out.write_text(review_input.model_dump_json(indent=2), encoding="utf-8")
print(out)