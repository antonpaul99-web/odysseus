from types import SimpleNamespace

import numpy as np

from services.faces.face_service import FaceService


def _fake_face(embedding, bbox=(0, 0, 10, 10)):
    return SimpleNamespace(
        normed_embedding=np.asarray(embedding, dtype=np.float32),
        bbox=np.asarray(bbox, dtype=np.float32),
    )


def test_enroll_and_recognize_round_trip(tmp_path, monkeypatch):
    service = FaceService(data_dir=str(tmp_path))

    alice_embedding = [1.0, 0.0, 0.0]
    monkeypatch.setattr(service, "_decode_faces", lambda image_bytes: [_fake_face(alice_embedding)])

    result = service.enroll("alice", b"fake-jpeg-bytes")
    assert result == {"name": "alice", "samples": 1}
    assert service.list_known() == [
        {"name": "alice", "samples": 1, "has_thumbnail": True, "updated": service._load_known()["alice"]["updated"]}
    ]

    # A near-identical embedding should match; an orthogonal one should not.
    monkeypatch.setattr(service, "_decode_faces", lambda image_bytes: [_fake_face([0.99, 0.01, 0.0])])
    matches = service.recognize(b"fake-jpeg-bytes-2")
    assert matches[0]["name"] == "alice"
    assert matches[0]["score"] > 0.9

    monkeypatch.setattr(service, "_decode_faces", lambda image_bytes: [_fake_face([0.0, 1.0, 0.0])])
    matches = service.recognize(b"fake-jpeg-bytes-3")
    assert matches[0]["name"] is None


def test_enroll_requires_name(tmp_path, monkeypatch):
    service = FaceService(data_dir=str(tmp_path))
    monkeypatch.setattr(service, "_decode_faces", lambda image_bytes: [_fake_face([1.0, 0.0, 0.0])])

    try:
        service.enroll("  ", b"fake-jpeg-bytes")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_enroll_requires_detected_face(tmp_path, monkeypatch):
    service = FaceService(data_dir=str(tmp_path))
    monkeypatch.setattr(service, "_decode_faces", lambda image_bytes: [])

    try:
        service.enroll("alice", b"fake-jpeg-bytes")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_delete_known_removes_entry_and_thumbnail(tmp_path, monkeypatch):
    service = FaceService(data_dir=str(tmp_path))
    monkeypatch.setattr(service, "_decode_faces", lambda image_bytes: [_fake_face([1.0, 0.0, 0.0])])
    service.enroll("alice", b"fake-jpeg-bytes")

    thumb_name = service._load_known()["alice"]["thumbnails"][0]
    assert (service.thumb_dir / thumb_name).exists()

    assert service.delete_known("alice") is True
    assert service.list_known() == []
    assert not (service.thumb_dir / thumb_name).exists()
    assert service.delete_known("alice") is False


def test_get_thumbnail_returns_bytes_for_known_name(tmp_path, monkeypatch):
    service = FaceService(data_dir=str(tmp_path))
    monkeypatch.setattr(service, "_decode_faces", lambda image_bytes: [_fake_face([1.0, 0.0, 0.0])])
    service.enroll("alice", b"fake-jpeg-bytes")

    assert service.get_thumbnail("alice") == b"fake-jpeg-bytes"
    assert service.get_thumbnail("bob") is None
