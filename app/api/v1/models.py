"""
Models API 路由
"""

from typing import List, Optional, Set

from fastapi import APIRouter, Request

from app.core.logger import logger
from app.services.grok.services.model import ModelInfo, ModelService
from app.services.token import get_token_manager


router = APIRouter(tags=["Models"])


async def _available_pools(_: Optional[Request] = None) -> Set[str]:
    token_mgr = await get_token_manager()
    await token_mgr.reload_if_stale()

    available: Set[str] = set()
    for pool_name, pool in token_mgr.pools.items():
        try:
            if any(token.is_available() for token in pool.list()):
                available.add(pool_name)
        except Exception:
            logger.warning("Failed to inspect pool availability: %s", pool_name)
    return available


def _model_available_for_pools(model: ModelInfo, available_pools: Set[str]) -> bool:
    if not available_pools:
        return False
    candidates = ModelService.pool_candidates_for_model(model.model_id)
    return any(pool_name in available_pools for pool_name in candidates)


async def _list_available_models(request: Optional[Request] = None) -> List[dict]:
    try:
        available_pools = await _available_pools(request)
        models = [
            m
            for m in ModelService.list()
            if _model_available_for_pools(m, available_pools)
        ]
    except Exception as exc:
        logger.warning("Falling back to full model list: %s", exc)
        models = ModelService.list()

    return [
        {
            "id": m.model_id,
            "object": "model",
            "created": 0,
            "owned_by": "grok2api@chenyme",
        }
        for m in models
    ]


@router.get("/models")
async def list_models(request: Request):
    """OpenAI 兼容 models 列表接口"""
    data = await _list_available_models(request)
    return {"object": "list", "data": data}


__all__ = [
    "router",
    "_available_pools",
    "_model_available_for_pools",
    "_list_available_models",
]
