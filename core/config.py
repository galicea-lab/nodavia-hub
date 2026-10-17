# core/config.py
# AI 2026-10-10: właściwa implementacja ustawień przeniesiona do
# config/config.py (pakiet `config`, plik config/config.ini). Ten moduł zostaje
# jako cienki reeksport, żeby istniejące `from core.config import settings`
# (main.py, api/security.py, db/session.py, wf/dependencies.py, ...) działało
# bez zmian. Nowy kod spoza katalogu connector/ powinien importować wprost
# z `config.config`; konektory czytają swoje sekcje przez `connector/base.py`.
from config.config import (  # noqa: F401
    AppSettings,
    DBSettings,
    KeycloakSettings,
    LoggingSettings,
    Settings,
    section,
    settings,
)

__all__ = [
    'AppSettings',
    'DBSettings',
    'KeycloakSettings',
    'LoggingSettings',
    'Settings',
    'section',
    'settings',
]
