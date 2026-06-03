"""
WebUI helper routes.
"""

from fastapi import APIRouter, Request

from app.api.v1.models import _list_available_models


router = APIRouter(tags=["WebUI"])


@router.get("/webui/api/models")
async def webui_models(request: Request):
    data = await _list_available_models(request)
    return {"object": "list", "data": data}


__all__ = ["router"]
