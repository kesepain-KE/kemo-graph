from typing import Annotated
from fastapi import Depends
from core.service.facade import KnowledgeBaseService
from core.jobs import MaintenanceJobManager
from update import ApplicationUpdater
from api.deps import RuntimeContext, get_context, get_job_manager, get_service, get_updater

Service = Annotated[KnowledgeBaseService, Depends(get_service)]
Jobs = Annotated[MaintenanceJobManager, Depends(get_job_manager)]
Updater = Annotated[ApplicationUpdater, Depends(get_updater)]
Context = Annotated[RuntimeContext, Depends(get_context)]
