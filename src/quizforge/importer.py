"""导入编排：import_bank() = 管道 + 落库 + JSON 快照（技术方案 2.2/2.3）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .config import get_settings
from .llm.client import LLMClient
from .models import ImportReport, QuarantineItem, Question
from .parsing.pipeline import run_pipeline
from .storage import db


def import_bank(
    pdf_path: str,
    name: str | None = None,
    use_llm: bool = True,
    db_path: str | None = None,
) -> tuple[ImportReport, list[Question], list[QuarantineItem]]:
    settings = get_settings()
    db_path = db_path or settings.db_path
    db.init_db(db_path)

    source = Path(pdf_path)
    bank_id = "b_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    bank_name = name or source.stem

    llm_client = None
    if use_llm and settings.llm_api_key:
        llm_client = LLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
        )

    questions, report, quarantine = run_pipeline(pdf_path, bank_id, llm_client)

    with db.connect(db_path) as conn:
        db.insert_bank(conn, bank_id, bank_name, str(source), len(questions))
        db.insert_questions(conn, questions)
        db.insert_quarantine(conn, quarantine)

    snapshot_dir = settings.data_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "bank_id": bank_id,
        "name": bank_name,
        "source_file": str(source),
        "imported_at": datetime.now(UTC).isoformat(),
        "question_count": len(questions),
        "questions": [q.model_dump(mode="json") for q in questions],
    }
    snapshot_path = snapshot_dir / f"{bank_id}.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report.bank_id = bank_id
    return report, questions, quarantine
