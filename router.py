#!/usr/bin/env python3
# coding: utf-8
# authors: Jerzy Wawro
# (C) 2026

from fastapi import FastAPI, APIRouter
from api.auth import router as router_auth

# AI 2026-10-10: adres dokumentacji API (poniżej) przeniesiony do [app] docs_url
# w config/config.ini, zamiast być wpisany na sztywno.
from config.config import settings

from pm.routers.calc import router as router_calc
from pm.routers.dashboard import router as router_dashboard
from pm.routers.forms import router as router_forms
from pm.routers.projects import router as router_projects

# AI 2026-10-04: aplikacja wf nie była dotąd montowana w ogóle (zob. TODO.md pkt 2
# i 2026-10-04.md). NIE są tu dodawane:
#  - router_edr (wf/routers/edr_api.py) - blokowany przez import nieistniejącego
#
# AI 2026-10-05: blokada importu usunięta (zob. connector/*), endpointy `/ade*`
# mają token Keycloak, a `/webcon_*` i `/evidences/webcon` dodatkowo grupę
# administracyjną, a `*Callback` (wywoływane przez e-Delivery) zostały otwarte.
# Router nadal NIE jest montowany - to decyzja wdrożeniowa: włączenie go
# wystawia wyszukiwarkę e-Delivery po PESEL/NIP i akcje wysyłki wiadomości.
# Aby włączyć: `v1router.mount("/wf", ...)` + `app_wf.include_router(router_edr)`
# i dopisanie testów autoryzacji w tests/test_edr_auth.py.
# AI 2026-10-09: router_signius (wf/routers/signius_api.py) montowany od razu -
# w przeciwieństwie do router_edr nie ma żadnego anonimowego endpointu
# (wszystko za get_current_wf_user, jak reszta modułu wf); bez skonfigurowanych
# sekcji [signius]/[frappe_education] w config.ini po prostu zwraca 503.
# AI 2026-10-10: router_edr_paperless (wf/routers/edr_paperless_api.py) -
# alternatywa dla WEBCON w e-Doręczeniach (zob. analiza_edr.md,
# connector/edoreczenia/edr_paperless.py), montowany od razu z tego samego
# powodu co router_signius; wymaga opublikowanego ProcessModel
# "edr-korespondencja" (zob. scripts/seed_edr_process.py) i konfiguracji
# [paperless]/[edr] w config.ini, inaczej zwraca 503/409.
# AI 2026-10-15: router_dictionaries (wf/routers/dictionary_api.py) i
# router_users (wf/routers/user_directory_api.py) - backend dla pól
# `AsyncSelectWidget`/`TreeSelectWidget` wygenerowanych migracją Webcon ->
# RJSF w spiffworkflow-core (zob. rjsf.md, x-webcon.md,
# spiffworkflow-core/zaleznosci.md). Montowane od razu - jak router_signius,
# wszystko za get_current_wf_user; słowniki wewnętrzne, których jeszcze nie
# zaimportowano do huba, zwracają 503, a te bez konektora w ogóle (ERP
# Optima, EZD) - 501 (zob. wf/services/dictionary_service.py).
from wf.routers import (
    router_comments as router_wf_comments,
    router_dictionaries as router_wf_dictionaries,
    router_edr_paperless as router_wf_edr_paperless,
    router_notifications as router_wf_notifications,
    router_process_instances as router_wf_process_instances,
    router_process_models as router_wf_process_models,
    router_signius as router_wf_signius,
    router_tasks as router_wf_tasks,
    router_users as router_wf_users,
)
from wf.routers.calendar_api import router_calendar as router_wf_calendar

api_description = """
API  ....
"""

api_tags_metadata = [
    {
        "name": "Dasboard",
        "description": "Operacje API",
        "externalDocs": {
         "description": "zob. /v1/nodivia/docs",
         "url": settings.app.docs_url,
       },
    },

]

v1router = FastAPI(openapi_tags=api_tags_metadata,
                       description=api_description,
                       version="1.0.0",
                       terms_of_service="#",
                       )
app_nodivia = FastAPI()
app_nodivia.include_router(router_auth)
v1router.mount("/nodivia", app_nodivia)

app_pm = FastAPI()
app_pm.include_router(router_calc)
app_pm.include_router(router_dashboard)
app_pm.include_router(router_projects)
app_pm.include_router(router_auth)
app_pm.include_router(router_forms)
v1router.mount("/pm", app_pm)

# AI 2026-10-04: montowanie aplikacji wf - zob. TODO.md pkt 2
app_wf = FastAPI()
app_wf.include_router(router_wf_process_models)
app_wf.include_router(router_wf_process_instances)
app_wf.include_router(router_wf_tasks)
app_wf.include_router(router_wf_comments)
app_wf.include_router(router_wf_notifications)
app_wf.include_router(router_wf_calendar)
app_wf.include_router(router_wf_signius)
app_wf.include_router(router_wf_edr_paperless)
app_wf.include_router(router_wf_dictionaries)
app_wf.include_router(router_wf_users)
v1router.mount("/wf", app_wf)