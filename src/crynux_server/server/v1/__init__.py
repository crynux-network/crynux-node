from fastapi import APIRouter

from .account import router as account_router
from .delegator import router as delegator_router
from .dual_mining import router as dual_mining_router
from .node import router as node_router
from .settings import router as settings_router
from .system import router as system_router
from .task import router as task_router
from .worker import router as worker_router

router = APIRouter(prefix="/v1")
router.include_router(account_router)
router.include_router(node_router)
router.include_router(system_router)
router.include_router(task_router)
router.include_router(worker_router)
router.include_router(settings_router)
router.include_router(delegator_router)
router.include_router(dual_mining_router)
