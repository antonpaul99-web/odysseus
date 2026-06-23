# services/faces/face_service.py
"""Face enrollment + recognition service — on-demand only.

Hand it image bytes (a webcam capture, an upload, a gallery photo) and it
either enrolls a known face under a name or matches faces in the image
against everyone enrolled so far. No camera access and no background worker
live here — that would mean an always-on camera and a host machine with one
attached, which is a separate, more invasive feature than what's built here.

Backend is InsightFace (ArcFace embeddings over onnxruntime — the same
runtime fastembed already pulls in as a core dependency, so there's no
dlib/cmake build step like `face_recognition` needs). The buffalo_l model
pack (~300MB) downloads automatically from the InsightFace CDN on first use.
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from core.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)

# Cosine similarity threshold for "same person" on ArcFace embeddings.
# Favors precision (fewer false matches) over recall; pass `threshold` to
# recognize() to tune per call.
DEFAULT_MATCH_THRESHOLD = 0.55

_INSTALL_HINT = "pip install insightface opencv-python-headless"


class FaceService:
    """Enroll known faces and recognize them in new photos."""

    def __init__(self, data_dir: str = "data/faces"):
        self.data_dir = Path(data_dir)
        self.thumb_dir = self.data_dir / "thumbnails"
        self.encodings_path = self.data_dir / "known_faces.json"

        self._app = None  # lazy insightface.app.FaceAnalysis
        self._init_error: Optional[str] = None
        self._known: Optional[Dict[str, Any]] = None  # lazy-loaded known_faces.json

    # ── Backend (InsightFace) ──

    def _get_app(self):
        if self._app is not None:
            return self._app
        if self._init_error:
            return None
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            self._init_error = f"insightface not installed. Install with: {_INSTALL_HINT}"
            logger.warning(self._init_error)
            return None
        try:
            app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=0, det_size=(640, 640))
            self._app = app
            logger.info("InsightFace buffalo_l model ready")
        except Exception as e:
            self._init_error = f"Failed to load InsightFace model: {e}"
            logger.error(self._init_error, exc_info=True)
            return None
        return self._app

    @property
    def available(self) -> bool:
        return self._get_app() is not None

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    # ── Storage ──

    def _load_known(self) -> Dict[str, Any]:
        if self._known is None:
            if self.encodings_path.exists():
                try:
                    self._known = json.loads(self.encodings_path.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.error(f"Failed to read {self.encodings_path}: {e}")
                    self._known = {}
            else:
                self._known = {}
        return self._known

    def _save_known(self):
        atomic_write_json(str(self.encodings_path), self._known, indent=2)

    # ── Detection helper ──

    def _decode_faces(self, image_bytes: bytes):
        """Return InsightFace `Face` objects detected in `image_bytes`, or None on decode failure."""
        try:
            import cv2
        except ImportError:
            raise RuntimeError(f"opencv not installed. Install with: {_INSTALL_HINT}")
        app = self._get_app()
        if app is None:
            raise RuntimeError(self._init_error or "face service unavailable")
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        return app.get(img)

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom == 0:
            return 0.0
        return float(np.dot(va, vb) / denom)

    # ── Public interface ──

    def enroll(self, name: str, image_bytes: bytes) -> Dict[str, Any]:
        """Detect the largest face in `image_bytes` and add it as a sample under `name`."""
        name = (name or "").strip()
        if not name:
            raise ValueError("name is required")

        faces = self._decode_faces(image_bytes)
        if faces is None:
            raise ValueError("could not decode image")
        if not faces:
            raise ValueError("no face detected in image")

        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        embedding = face.normed_embedding.tolist()

        known = self._load_known()
        entry = known.setdefault(name, {"embeddings": [], "thumbnails": [], "created": time.time()})
        entry["embeddings"].append(embedding)

        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_id = f"{uuid.uuid4().hex}.jpg"
        (self.thumb_dir / thumb_id).write_bytes(image_bytes)
        entry["thumbnails"].append(thumb_id)
        entry["updated"] = time.time()

        self._save_known()
        logger.info(f"Enrolled face sample for '{name}' ({len(entry['embeddings'])} samples total)")
        return {"name": name, "samples": len(entry["embeddings"])}

    def recognize(self, image_bytes: bytes, threshold: float = DEFAULT_MATCH_THRESHOLD) -> List[Dict[str, Any]]:
        """Return one result per detected face: {bbox, name|None, score}."""
        faces = self._decode_faces(image_bytes)
        if faces is None:
            raise ValueError("could not decode image")

        known = self._load_known()
        results = []
        for face in faces:
            embedding = face.normed_embedding.tolist()
            best_name, best_score = None, 0.0
            for name, entry in known.items():
                for known_emb in entry.get("embeddings", []):
                    score = self._cosine(embedding, known_emb)
                    if score > best_score:
                        best_name, best_score = name, score
            results.append({
                "bbox": [float(v) for v in face.bbox],
                "name": best_name if best_score >= threshold else None,
                "score": round(best_score, 4),
            })
        return results

    def list_known(self) -> List[Dict[str, Any]]:
        known = self._load_known()
        return [
            {
                "name": name,
                "samples": len(entry.get("embeddings", [])),
                "has_thumbnail": bool(entry.get("thumbnails")),
                "updated": entry.get("updated"),
            }
            for name, entry in sorted(known.items())
        ]

    def delete_known(self, name: str) -> bool:
        known = self._load_known()
        entry = known.pop(name, None)
        if entry is None:
            return False
        for thumb in entry.get("thumbnails", []):
            (self.thumb_dir / thumb).unlink(missing_ok=True)
        self._save_known()
        logger.info(f"Deleted enrolled face '{name}'")
        return True

    def get_thumbnail(self, name: str) -> Optional[bytes]:
        """Raw bytes of the first enrolled photo for `name`, or None."""
        entry = self._load_known().get(name)
        if not entry or not entry.get("thumbnails"):
            return None
        path = self.thumb_dir / entry["thumbnails"][0]
        return path.read_bytes() if path.exists() else None

    def get_stats(self) -> Dict[str, Any]:
        known = self._load_known()
        return {
            "available": self.available,
            "init_error": self._init_error,
            "known_count": len(known),
            "total_samples": sum(len(e.get("embeddings", [])) for e in known.values()),
        }


# Module-level singleton
_face_service = None

def get_face_service() -> FaceService:
    global _face_service
    if _face_service is None:
        _face_service = FaceService()
    return _face_service
