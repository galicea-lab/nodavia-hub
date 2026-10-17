# AI 2026-10-04
"""
Agregacja routerów aplikacji wf (obieg dokumentów). Zob. TODO.md pkt 2.

Nie zawiera routerów z wf/routers/bpmn/ - to odrębny, niekompletny prototyp
(importuje nieistniejące moduły api.schemas/db.models i inną async bazę
deklaratywną niż reszta huba). Zob. 2026-10-04.md, sekcja "Dwa silniki
workflow w wf/".
"""

from .comment_router import router as router_comments
from .dictionary_api import router as router_dictionaries
from .edr_paperless_api import router as router_edr_paperless
from .human_task_router import router as router_tasks
from .notification_router import router as router_notifications
from .process_instance_router import router as router_process_instances
from .process_model_router import router as router_process_models
from .signius_api import router as router_signius
from .user_directory_api import router as router_users

__all__ = [
    "router_process_models",
    "router_process_instances",
    "router_tasks",
    "router_comments",
    "router_notifications",
    "router_signius",
    "router_edr_paperless",
    "router_dictionaries",
    "router_users",
]
