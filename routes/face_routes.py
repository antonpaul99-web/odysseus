# routes/face_routes.py
"""Face enrollment + recognition API routes (on-demand only, no camera/worker)."""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form, Response
import logging

from src.auth_helpers import require_privilege

logger = logging.getLogger(__name__)


def setup_face_routes(face_service):
    """Set up face recognition routes with the provided face service."""
    router = APIRouter(prefix="/api/faces", tags=["faces"])

    @router.get("/stats")
    async def get_face_stats(request: Request):
        require_privilege(request, "can_manage_faces")
        return face_service.get_stats()

    @router.get("")
    async def list_known_faces(request: Request):
        require_privilege(request, "can_manage_faces")
        return {"known": face_service.list_known()}

    @router.post("/enroll")
    async def enroll_face(request: Request, name: str = Form(...), file: UploadFile = File(...)):
        require_privilege(request, "can_manage_faces")
        if not face_service.available:
            raise HTTPException(503, face_service.init_error or "Face service not available")

        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(400, "Empty image file")

        try:
            return face_service.enroll(name, image_bytes)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Face enroll failed: {e}", exc_info=True)
            raise HTTPException(500, "Enrollment failed")

    @router.post("/recognize")
    async def recognize_faces(request: Request, file: UploadFile = File(...)):
        require_privilege(request, "can_manage_faces")
        if not face_service.available:
            raise HTTPException(503, face_service.init_error or "Face service not available")

        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(400, "Empty image file")

        try:
            results = face_service.recognize(image_bytes)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Face recognize failed: {e}", exc_info=True)
            raise HTTPException(500, "Recognition failed")
        return {"faces": results}

    @router.get("/{name}/thumbnail")
    async def get_face_thumbnail(request: Request, name: str):
        require_privilege(request, "can_manage_faces")
        data = face_service.get_thumbnail(name)
        if data is None:
            raise HTTPException(404, "No thumbnail")
        return Response(content=data, media_type="image/jpeg")

    @router.delete("/{name}")
    async def delete_known_face(request: Request, name: str):
        require_privilege(request, "can_manage_faces")
        if not face_service.delete_known(name):
            raise HTTPException(404, "Unknown name")
        return {"ok": True}

    return router
