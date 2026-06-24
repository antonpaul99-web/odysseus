"""Face enrollment + recognition service (on-demand only — no camera worker)."""

from .face_service import FaceService, get_face_service

__all__ = ["FaceService", "get_face_service"]
