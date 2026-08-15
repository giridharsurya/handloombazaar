from pathlib import Path


def test_python_multipart_present_in_requirements():
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    content = requirements.read_text(encoding="utf-8")
    assert "python-multipart" in content
