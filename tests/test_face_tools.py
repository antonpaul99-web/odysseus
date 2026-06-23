import json
from types import SimpleNamespace

import pytest

from src import tool_implementations as ti


@pytest.mark.asyncio
async def test_recognize_face_requires_image_id():
    result = await ti.do_recognize_face(json.dumps({}), owner="alice")
    assert result["exit_code"] == 1
    assert "image_id" in result["error"]


@pytest.mark.asyncio
async def test_enroll_face_requires_image_id_and_name():
    result = await ti.do_enroll_face(json.dumps({"image_id": "img1"}), owner="alice")
    assert result["exit_code"] == 1
    assert "image_id and name" in result["error"]


@pytest.mark.asyncio
async def test_recognize_face_image_not_found(monkeypatch):
    monkeypatch.setattr(ti, "_read_gallery_image_bytes", lambda image_id, owner: None)
    result = await ti.do_recognize_face(json.dumps({"image_id": "missing"}), owner="alice")
    assert result["exit_code"] == 1
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_recognize_face_unavailable_service(monkeypatch):
    monkeypatch.setattr(ti, "_read_gallery_image_bytes", lambda image_id, owner: b"fake-bytes")
    stub_service = SimpleNamespace(available=False, init_error="insightface not installed")
    monkeypatch.setattr("services.faces.get_face_service", lambda: stub_service)

    result = await ti.do_recognize_face(json.dumps({"image_id": "img1"}), owner="alice")
    assert result["exit_code"] == 1
    assert "insightface not installed" in result["error"]


@pytest.mark.asyncio
async def test_recognize_face_formats_matches_and_unknowns(monkeypatch):
    monkeypatch.setattr(ti, "_read_gallery_image_bytes", lambda image_id, owner: b"fake-bytes")
    stub_service = SimpleNamespace(
        available=True,
        recognize=lambda image_bytes: [
            {"bbox": [0, 0, 10, 10], "name": "alice", "score": 0.91},
            {"bbox": [20, 20, 30, 30], "name": None, "score": 0.2},
        ],
    )
    monkeypatch.setattr("services.faces.get_face_service", lambda: stub_service)

    result = await ti.do_recognize_face(json.dumps({"image_id": "img1"}), owner="alice")
    assert result["exit_code"] == 0
    assert "alice" in result["output"]
    assert "unrecognized face" in result["output"]
    assert len(result["faces"]) == 2


@pytest.mark.asyncio
async def test_recognize_face_no_faces_detected(monkeypatch):
    monkeypatch.setattr(ti, "_read_gallery_image_bytes", lambda image_id, owner: b"fake-bytes")
    stub_service = SimpleNamespace(available=True, recognize=lambda image_bytes: [])
    monkeypatch.setattr("services.faces.get_face_service", lambda: stub_service)

    result = await ti.do_recognize_face(json.dumps({"image_id": "img1"}), owner="alice")
    assert result["exit_code"] == 0
    assert result["faces"] == []


@pytest.mark.asyncio
async def test_enroll_face_success(monkeypatch):
    monkeypatch.setattr(ti, "_read_gallery_image_bytes", lambda image_id, owner: b"fake-bytes")
    stub_service = SimpleNamespace(
        available=True,
        enroll=lambda name, image_bytes: {"name": name, "samples": 2},
    )
    monkeypatch.setattr("services.faces.get_face_service", lambda: stub_service)

    result = await ti.do_enroll_face(json.dumps({"image_id": "img1", "name": "alice"}), owner="alice")
    assert result["exit_code"] == 0
    assert "alice" in result["output"]
    assert "2 sample" in result["output"]


@pytest.mark.asyncio
async def test_enroll_face_no_face_detected(monkeypatch):
    monkeypatch.setattr(ti, "_read_gallery_image_bytes", lambda image_id, owner: b"fake-bytes")

    def _raise(name, image_bytes):
        raise ValueError("no face detected in image")

    stub_service = SimpleNamespace(available=True, enroll=_raise)
    monkeypatch.setattr("services.faces.get_face_service", lambda: stub_service)

    result = await ti.do_enroll_face(json.dumps({"image_id": "img1", "name": "alice"}), owner="alice")
    assert result["exit_code"] == 1
    assert "no face detected" in result["error"]
