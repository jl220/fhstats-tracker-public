from pathlib import Path

from forza_tracker.parser.ocr import _available_languages, _resolve_tesseract_cmd


def test_bundled_tesseract_takes_priority(tmp_path: Path, monkeypatch) -> None:
    application_root = tmp_path / "app"
    bundled = application_root / "ocr" / "tesseract" / "tesseract.exe"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    configured = tmp_path / "system" / "tesseract.exe"
    configured.parent.mkdir(parents=True)
    configured.touch()
    monkeypatch.setattr("forza_tracker.parser.ocr.application_root", lambda: application_root)

    assert _resolve_tesseract_cmd(configured) == bundled


def test_ocr_uses_packaged_english_and_spanish_models(tmp_path: Path) -> None:
    executable = tmp_path / "tesseract.exe"
    executable.touch()
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "eng.traineddata").touch()
    (tessdata / "spa.traineddata").touch()

    assert _available_languages(executable) == "eng+spa"
