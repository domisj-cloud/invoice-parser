from __future__ import annotations

from datetime import UTC, datetime

from app.jobs import JobRepository


def test_job_repository_records_success(tmp_path) -> None:
    repo = JobRepository(tmp_path / "jobs.db")
    repo.initialize()

    started_at = datetime.now(UTC)
    repo.create(
        job_id="job-1",
        input_bucket="inv-input",
        input_object_key="invoice.pdf",
    )
    repo.complete(
        "job-1",
        status="SUCCESS",
        started_at=started_at,
        output_bucket="inv-output",
        output_object_key="invoice.xml",
        invoice_number="INV-1",
        document_type="invoice",
        line_count=2,
    )

    job = repo.get("job-1")

    assert job is not None
    assert job["status"] == "SUCCESS"
    assert job["input_object_key"] == "invoice.pdf"
    assert job["output_object_key"] == "invoice.xml"
    assert job["duration_ms"] >= 0
    assert repo.counts() == {"SUCCESS": 1}
