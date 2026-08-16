from pathlib import Path

# Python places the executing helper's directory on sys.path before loading the
# helper.  This one-shot bootstrap hook prevents the documentation directory at
# repository root from shadowing the installable src/agi package during editable
# test runs.
wrapper = Path("agi/__init__.py")
wrapper.parent.mkdir(parents=True, exist_ok=True)
wrapper.write_text(
    '''"""Repository-checkout compatibility wrapper for the installable src/agi package."""\n\nfrom pathlib import Path as _Path\n\n_src_package = _Path(__file__).resolve().parents[1] / "src" / "agi"\nif _src_package.is_dir():\n    __path__.append(str(_src_package))\n\nfrom .evaluation import CRITERIA, EvaluationPolicy, evaluate_evidence, evaluate_records\nfrom .evidence import EvidenceLedger, EvidenceRecord\n\n__all__ = [\n    "CRITERIA",\n    "EvaluationPolicy",\n    "EvidenceLedger",\n    "EvidenceRecord",\n    "evaluate_evidence",\n    "evaluate_records",\n]\n''',
    encoding="utf-8",
)
