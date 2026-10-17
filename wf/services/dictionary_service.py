# wf/services/dictionary_service.py
# coding: utf-8
# AI 2026-10-15
"""
Słowniki (pola `AsyncSelectWidget`/`TreeSelectWidget`) dla formularzy
obiegów migrowanych z Webcona do RJSF.

Kontekst: migracja opisana w `rjsf.md`/`x-webcon.md` (katalog
`spiffworkflow-core`) zamieniła macierz atrybutów Webcona na
`x-permissions`, a pola słownikowe (`x-webcon.type` = "Pole wyboru"/"Osoba
lub grupa"/"Spółka"/"Drzewo wyboru") bez zaszytego `oneOf` dostały w
`*-uischema.json` widget `AsyncSelectWidget`/`TreeSelectWidget` z
`ui:options.endpoint` wskazującym na backend (`/v1/wf/dictionaries/<slug>`
albo `/v1/wf/users`). Lista tych słowników i ich źródeł jest spisana w
`spiffworkflow-core/zaleznosci.md` (ten plik jest "wykonaniem" tamtej
analizy po stronie huba).

Rejestr w tym module odwzorowuje każdy `slug` na jedno z trzech źródeł
danych:

  1. **Obieg wewnętrzny** (`InternalDictionary`) - słownik to zwykły,
     opublikowany `ProcessModel` w tym hubie (proces "Krok słownikowy" albo
     inny samodzielny obieg, np. "Lista terminów", "Kontrahent"). Jeden
     rekord słownika = jedna `ProcessInstance`; jako (value, label) używamy
     uniwersalnie `ProcessInstance.id`/`ProcessInstance.title` - te dwie
     kolumny istnieją dla KAŻDEGO procesu w hubie, więc nie trzeba znać
     (niespójnych między obiegami) nazw pól formularza danego słownika -
     zob. przegląd pól w `spiffworkflow-core/zaleznosci.md` ("Nazwa" /
     "Nazwa firmy" / "Firma lub skrót nazwy" / "Krótki opis sprawy" -
     każdy obieg nazywał to inaczej, `title` jest wspólnym mianownikiem).
  2. **Frappe Education** (`FrappeDictionary`) - słownik odpowiada Doctype
     w systemie dziekanatowym (connector/frappe_education), np. "Studenci".
  3. **Niepodłączone** (`NOT_CONNECTED`) - `source-name` wskazuje na system,
     dla którego w tym repozytorium nie ma jeszcze konektora (ERP Optima,
     EZD, "Spółki" uczelni) - zwracamy jawny błąd 501 z wyjaśnieniem,
     zamiast fałszywie pustej listy (żeby nie wyglądało to na "słownik jest
     pusty", gdy w rzeczywistości nikt go jeszcze nie podłączył).

`process_model_key` jest tu umowny - taki klucz powinien dostać
`ProcessModel` w chwili zaimportowania danego obiegu-słownika do huba
(`POST /process-models` + `.../publish`, zob.
wf/routers/process_model_router.py, albo skrypt wzorowany na
scripts/seed_edr_process.py). Dopóki dany słownik nie zostanie
zaimportowany, endpoint zwraca 503 - ten sam wzorzec co już istniejące
konektory (connector/frappe_education, connector/signius) dla "sekcja w
config.ini nieskonfigurowana": "jeszcze nie podłączone", nie "zepsute".
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import ConnectorConfigError, ConnectorError
from connector.frappe_education.connector import get_connector as get_frappe_connector
from wf.models import ProcessInstance, ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import OptionRead


class DictionaryError(Exception):
    """Błąd domenowy słownika - router tłumaczy go na kod HTTP."""


class DictionaryNotFoundError(DictionaryError):
    """Nieznany `slug` (nie ma go w żadnym rejestrze poniżej)."""


class DictionaryNotSeededError(DictionaryError):
    """Słownik wewnętrzny jeszcze nie zaimportowany do huba (brak
    opublikowanego `ProcessModel` o umówionym kluczu), albo zewnętrzny
    konektor (Frappe Education) nieskonfigurowany."""


class DictionaryNotConnectedError(DictionaryError):
    """Słownik, dla którego w tym repozytorium nie ma (jeszcze) żadnego
    konektora - zob. `NOT_CONNECTED` poniżej."""


@dataclass(frozen=True)
class InternalDictionary:
    """Słownik = opublikowany `ProcessModel` w tym hubie."""

    process_model_key: str
    source_name: str  # x-webcon.source-name oryginalnego pola (dokumentacyjnie)


@dataclass(frozen=True)
class FrappeDictionary:
    """Słownik = Doctype w systemie dziekanatowym Frappe Education."""

    doctype: str
    label_field: str
    source_name: str


# ---------------------------------------------------------------------
# 1. Słowniki wewnętrzne - obiegi skopiowane do spiffworkflow-core (zob.
#    zaleznosci.md). "instytuty-uczelni" to alias tego samego obiegu co
#    "instytuty" (x-webcon source-name różnił się między polami, choć
#    wskazywał na ten sam proces "Instytuty").
# ---------------------------------------------------------------------
INTERNAL_DICTIONARIES: dict[str, InternalDictionary] = {
    "adres-e-doreczen": InternalDictionary("adres-e-doreczen", "Adres e-Doręczeń"),
    "dostawcy-uslug-podpisu-elektronicznego": InternalDictionary(
        "dostawcy-uslug-podpisu-elektronicznego", "Dostawcy usług podpisu elektronicznego"),
    "dzialy": InternalDictionary("dzialy", "Działy"),
    "instytuty": InternalDictionary("instytuty", "instytuty"),
    "instytuty-uczelni": InternalDictionary("instytuty", "Instytuty Uczelni"),
    "kategorie-spraw": InternalDictionary("kategorie-spraw", "Kategorie spraw"),
    "kierunki": InternalDictionary("kierunki", "Kierunki"),
    "podpisy-elektroniczne": InternalDictionary("podpisy-elektroniczne", "Podpisy Elektroniczne"),
    "slownik-tematow": InternalDictionary("slownik-tematow", "Słownik tematów"),
    "trm-terminarze": InternalDictionary("trm-terminarze", "[TRM] Terminarze"),
    "umowy-karta-kontrahenta": InternalDictionary("umowy-karta-kontrahenta", "[Umowy] Karta kontrahenta"),
    "teczki-spraw": InternalDictionary("teczki-spraw", "Teczki Spraw"),
}

# ---------------------------------------------------------------------
# 2. Słowniki z systemu dziekanatowego (connector/frappe_education).
#    `label_field` to standardowe pole Doctype Frappe Education - jeśli w
#    konkretnej instalacji go nie ma, i tak spada na `name` (docname).
# ---------------------------------------------------------------------
FRAPPE_DICTIONARIES: dict[str, FrappeDictionary] = {
    "studenci": FrappeDictionary("Student", "student_name", "Studenci"),
    "rok-akademicki": FrappeDictionary("Academic Term", "term_name", "rok akademicki"),
}

# ---------------------------------------------------------------------
# 3. Źródła zewnętrzne bez konektora w tym repozytorium - zob.
#    spiffworkflow-core/zaleznosci.md, sekcja "odwołania zewnętrzne".
# ---------------------------------------------------------------------
NOT_CONNECTED: dict[str, str] = {
    "companies": (
        "Pole 'Wybór spółki' (x-webcon.type='Spółka') nie wskazuje na żaden "
        "zidentyfikowany obieg-słownik w spiffworkflow-all - prawdopodobnie "
        "lista spółek uczelni żyje w systemie ERP/finansowym. W tym "
        "repozytorium nie ma jeszcze konektora do tego źródła - dodaj go w "
        "connector/, gdy system źródłowy będzie znany."
    ),
    "k-kontrahenci": (
        "source-name='[K] Kontrahenci', source-type='REST Web Service' "
        "(pole 'z systemu ERP (Optima)' w kontrahent-k-uischema.json). W "
        "repozytorium nie ma jeszcze konektora Optima (connector/optima/ "
        "jest tylko zapowiedziane sekcją [optima] w config.ini.example)."
    ),
    "konta-kontrahentow": (
        "source-name='Konta kontrahentów' (pole 'Konto w Optimie' w "
        "kontrahent-k-uischema.json) - jak wyżej, brak konektora Optima."
    ),
    "ezd-jrwa": (
        "source-name='[EZD] JRWA', source-type='Baza MSSQL' - rejestr "
        "Jednolitego Rzeczowego Wykazu Akt zewnętrznego systemu EZD. Nie "
        "znaleziono odpowiadającego obiegu w spiffworkflow-all ani "
        "konektora w connector/ - do potwierdzenia u właściciela integracji "
        "EZD."
    ),
    "ezd-kategoria-archiwizacji": (
        "source-name='[EZD] Kategoria archiwizacji' - jak wyżej (system "
        "EZD, brak konektora)."
    ),
}


def _internal_options(db: Session, dictionary: InternalDictionary, q: str | None,
                       limit: int) -> list[OptionRead]:
    model = db.execute(
        select(ProcessModel).where(ProcessModel.key == dictionary.process_model_key)
    ).scalars().first()
    if model is None:
        raise DictionaryNotSeededError(
            f"Słownik '{dictionary.source_name}' (process_model.key="
            f"'{dictionary.process_model_key}') nie został jeszcze "
            f"zaimportowany do huba - brak ProcessModel o tym kluczu. "
            f"Zaimportuj obieg z spiffworkflow-core (POST /process-models + "
            f".../publish, albo skrypt wzorowany na "
            f"scripts/seed_edr_process.py) z process_model.key="
            f"'{dictionary.process_model_key}'."
        )
    stmt = (
        select(ProcessInstance)
        .join(ProcessModelVersion,
              ProcessInstance.process_model_version_id == ProcessModelVersion.id)
        .where(ProcessModelVersion.process_model_id == model.id)
    )
    if q:
        stmt = stmt.where(ProcessInstance.title.ilike(f"%{q}%"))
    stmt = stmt.order_by(ProcessInstance.title).limit(limit)
    instances = db.execute(stmt).scalars().all()
    return [OptionRead(value=str(instance.id), label=instance.title) for instance in instances]


def _frappe_options(dictionary: FrappeDictionary, q: str | None, limit: int) -> list[OptionRead]:
    connector = get_frappe_connector()
    filters = [[dictionary.label_field, "like", f"%{q}%"]] if q else None
    try:
        rows = connector.get_list(
            dictionary.doctype,
            filters=filters,
            fields=["name", dictionary.label_field],
            limit_page_length=limit,
        )
    except ConnectorConfigError as exc:
        raise DictionaryNotSeededError(
            f"Frappe Education nie skonfigurowane ([frappe_education] w "
            f"config/config.ini) - słownik '{dictionary.source_name}': {exc}"
        ) from exc
    except ConnectorError as exc:
        raise DictionaryError(f"Frappe Education ({dictionary.doctype}): {exc}") from exc
    return [
        OptionRead(value=row["name"], label=row.get(dictionary.label_field) or row["name"])
        for row in rows
    ]


def list_options(db: Session, slug: str, q: str | None = None, limit: int = 50) -> list[OptionRead]:
    """Opcje {value, label} dla jednego pola `AsyncSelectWidget`/
    `TreeSelectWidget` (`ui:options.endpoint` = `/v1/wf/dictionaries/{slug}`
    w `*-uischema.json`)."""
    if slug in INTERNAL_DICTIONARIES:
        return _internal_options(db, INTERNAL_DICTIONARIES[slug], q, limit)
    if slug in FRAPPE_DICTIONARIES:
        return _frappe_options(FRAPPE_DICTIONARIES[slug], q, limit)
    if slug in NOT_CONNECTED:
        raise DictionaryNotConnectedError(NOT_CONNECTED[slug])
    raise DictionaryNotFoundError(f"Nieznany słownik '{slug}'")
