"""Operational and diagnostic scripts.

Run from backend directory with PYTHONPATH=. or inside the backend container.

Examples:
  python scripts/ops/run_full_extraction_test.py --paper-id 1
  python scripts/ops/test_parsers_real.py --pdf benchmark_pdfs/sample.pdf
  python scripts/ops/run_bulk_extraction.py --pdf-dir "E:\data\papers" --max-jobs 3

`run_bulk_extraction.py` owns the extraction worker for its database. Stop the
web backend first, or point the command at a separate database. Re-running the
same command resumes the MinerU batch checkpoint and persistent extraction jobs.
"""
