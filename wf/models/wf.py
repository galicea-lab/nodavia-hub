"""
Modele SQLAlchemy 2.0 (PostgreSQL) dla systemu obiegu dokumentów.
Odpowiadają schema.sql + migracji 002_paperless_integration.sql.

Konwencje:
  * AI 2026-10-05: klucze obce do użytkowników wskazują teraz jedną tabelę
    `users` (models/user.py) - patrz TODO.md pkt 2/4.
  * Kolumny FK mają w Pythonie przyrostek _id (np. started_by_id), ale w bazie
    zachowują nazwy ze schematu (started_by). Nazwa bez przyrostka to relacja –
    dzięki temu modele *Read z schemas.py (np. started_by: UserSummary)
    budują się wprost przez model_validate(obj).
  * Statusy: VARCHAR + CHECK (Enum z native_enum=False), wartości z tych samych
    StrEnum co w schemas.py. Najlepiej przenieś enumy do wspólnego modułu.
  * process_instance.lock_version to version_id_col – SQLAlchemy sam go
    inkrementuje i rzuca StaleDataError przy konflikcie zapisu.

Przy AsyncSession relacje ładuj jawnie (selectinload/joinedload) – leniwe
ładowanie w kontekście async rzuci MissingGreenlet.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from db.base import Base
from models.user import User  # AI 2026-10-05: jedna tabela użytkowników
# AI 2026-10-04: błędny import - enumy nie są w pakiecie `schemas` (top-level,
# dot. usera/auth), tylko w `wf.schemas.wf_schemas`. Bez tej poprawki pakiet
# wf.models nie dał się zaimportować (zob. 2026-10-04.md).
from wf.schemas.wf_schemas import (  # TODO: przenieść enumy do np. core/enums.py
    DocumentLinkRole,
    DocumentStatus,
    HumanTaskStatus,
    IngestStatus,
    JobStatus,
    JobType,
    PrincipalType,
    ProcessFileType,
    ProcessInstanceStatus,
    ProcessModelVersionStatus,
)


# =====================================================================
# Typy pomocnicze
# =====================================================================
# AI 2026-10-05 (TODO.md pkt 1): modele były pisane pod PostgreSQL (UUID,
# JSONB, gen_random_uuid(), GIN, Identity(always=True)) i nie dało się ich
# utworzyć na SQLite - a config.ini domyślnie wskazywał bazę plikową.
# Typy są teraz przenośne: SQLAlchemy dobiera wariant per dialekt, więc te same
# modele działają na SQLite (dev/test) i PostgreSQL (produkcja).

def str_enum(enum_cls: type[StrEnum], constraint_name: str) -> SAEnum:
    """VARCHAR + CHECK z wartościami StrEnum (bez typu ENUM w Postgresie)."""
    return SAEnum(
        enum_cls,
        name=constraint_name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
        length=32,
    )


def json_type() -> JSON:
    """JSON dla każdego dialektu; na PostgreSQL podmieniany na JSONB
    (indeksowalny, więc można dokładać GIN w PostgreSQL-specific DDL)."""
    return JSON().with_variant(JSONB, "postgresql")


def int_pk() -> BigInteger:
    """Liczbowy PK 64-bitowy; SQLite nie ma BIGINT autoinkrementu (dostaje
    INTEGER PRIMARY KEY), więc wariant zapisujemy jawnie."""
    return BigInteger().with_variant(Integer, "sqlite")


def uuid_pk() -> Mapped[uuid.UUID]:
    # AI 2026-10-05: `default=uuid.uuid4` po stronie Pythona zastępuje
    # `server_default=func.gen_random_uuid()`, które jest PostgreSQL-only
    # (w SQLite takie wyrażenie generowało błąd przy CREATE TABLE).
    return mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


def created_at_col() -> Mapped[datetime]:
    # AI 2026-10-05: `func.now()` działa w obu dialektach, więc server_default
    # zostaje (gwarantuje znacznik czasu także dla insertów spoza ORM).
    return mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


def user_fk(column_name: str | None = None, *, nullable: bool = True,
            ondelete: str | None = None) -> Mapped[Any]:
    """FK do jedynej tabeli użytkowników: `users` (models.user.User, models/user.py).

    AI 2026-10-05 (TODO.md pkt 2/4): wcześniej klucze obce wskazywały na
    `app_user` (UUID w wf/models/wf.py) - powstała druga tabela użytkowników,
    rozłączna z tabelą `users`, z której korzysta cała reszta huba i
    api/security.get_current_user. Teraz obie ścieżki widzą to samo konto.
    """
    args = (column_name,) if column_name else ()
    return mapped_column(
        *args,
        Integer,
        ForeignKey("users.id", ondelete=ondelete),
        nullable=nullable,
    )


def json_col(default: str = "{}") -> Mapped[dict[str, Any]]:
    # AI 2026-10-05: bez `server_default=text("'{}'::jsonb")` - cast do jsonb
    # jest składnią PostgreSQL, której SQLite nie zna. Wartość domyślną
    # zapewnia `default=dict` po stronie Pythona.
    return mapped_column(json_type(), nullable=False, default=dict)


TZ = TIMESTAMP(timezone=True)
ACTIVE_TASK = text("status IN ('open', 'claimed')")


def partial_index(name: str, *columns: str, where: Any = None) -> Index:
    """Indeks częściowy działający w PostgreSQL i SQLite.

    AI 2026-10-05: samo `postgresql_where` oznaczało, że na SQLite powstawał
    zwykły pełny indeks (działa, ale nie poprawnie) - albo indeks był pomijany
    przez Alembic. Podajemy warunek dla obu dialektów.
    """
    kwargs: dict[str, Any] = {}
    if where is not None:
        kwargs["postgresql_where"] = where
        kwargs["sqlite_where"] = where
    return Index(name, *columns, **kwargs)


# =====================================================================
# 1. Użytkownicy i organizacja
# =====================================================================

class ProcessInstanceState(Base):
    __tablename__ = "process_instance_state"

    process_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_instance.id", ondelete="CASCADE"),
        primary_key=True)
    serializer_version: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(json_type(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now(), onupdate=func.now())

    process_instance: Mapped[ProcessInstance] = relationship(back_populates="state")


class HumanTask(Base):
    __tablename__ = "human_task"
    __table_args__ = (
        Index("ix_ht_instance", "process_instance_id"),
        partial_index("ix_ht_active", "status", "claimed_by", where=ACTIVE_TASK),
        partial_index("ix_ht_due", "due_at", where=ACTIVE_TASK),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)  # = task.id Spiffa
    process_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_instance.id", ondelete="CASCADE"),
        nullable=False)
    bpmn_task_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    lane: Mapped[str | None] = mapped_column(Text)
    form_file: Mapped[str | None] = mapped_column(Text)
    ui_schema_file: Mapped[str | None] = mapped_column(Text)
    status: Mapped[HumanTaskStatus] = mapped_column(
        str_enum(HumanTaskStatus, "human_task_status_check"),
        nullable=False, default=HumanTaskStatus.OPEN,
        server_default=HumanTaskStatus.OPEN.value)
    claimed_by_id: Mapped[int | None] = user_fk("claimed_by")
    claimed_at: Mapped[datetime | None] = mapped_column(TZ)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0,
                                          server_default=text("0"))
    due_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[datetime | None] = mapped_column(TZ)
    completed_by_id: Mapped[int | None] = user_fk("completed_by")
    completed_on_behalf_of_id: Mapped[int | None] = user_fk("completed_on_behalf_of")
    submitted_data: Mapped[dict[str, Any] | None] = mapped_column(json_type())

    process_instance: Mapped[ProcessInstance] = relationship(back_populates="tasks")
    claimed_by: Mapped[User | None] = relationship(foreign_keys=[claimed_by_id])
    completed_by: Mapped[User | None] = relationship(foreign_keys=[completed_by_id])
    completed_on_behalf_of: Mapped[User | None] = relationship(
        foreign_keys=[completed_on_behalf_of_id])
    candidates: Mapped[list["HumanTaskCandidate"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True)


# =====================================================================
# 4. Zadania użytkowników
# =====================================================================

class HumanTaskCandidate(Base):
    __tablename__ = "human_task_candidate"
    __table_args__ = (
        Index("ix_htc_principal", "principal_type", "principal_value"),
    )

    human_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("human_task.id", ondelete="CASCADE"),
        primary_key=True)
    principal_type: Mapped[PrincipalType] = mapped_column(
        str_enum(PrincipalType, "human_task_candidate_principal_type_check"),
        primary_key=True)
    principal_value: Mapped[str] = mapped_column(Text, primary_key=True)

    task: Mapped[HumanTask] = relationship(back_populates="candidates")


class DocumentNumberSequence(Base):
    __tablename__ = "document_number_sequence"

    doc_type: Mapped[str] = mapped_column(Text, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default=text("0"))


# =====================================================================
# 5. Dokumenty (pliki w Paperless-ngx)
# =====================================================================

class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        Index("ix_document_type", "doc_type", text("created_at DESC")),
        # AI 2026-10-05: GIN/jsonb_path_ops to indeks PostgreSQL dla kolumny
        # JSON - na innych dialektach taki DDL się nie wygeneruje. Indeks
        # tworzony jest w migracji dla PostgreSQL (patrz 2026-10-05.md).
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    registry_number: Mapped[str | None] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus, "document_status_check"),
        nullable=False, default=DocumentStatus.DRAFT,
        server_default=DocumentStatus.DRAFT.value)
    # "metadata" jest zarezerwowane w klasach deklaratywnych – atrybut nazywa się
    # doc_metadata, kolumna w bazie pozostaje "metadata". DocumentRead czyta go
    # przez validation_alias.
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        # AI 2026-10-05: bez castu `::jsonb` (składnia PostgreSQL) -
        # wartość domyślną zapewnia default=dict po stronie Pythona.
        "metadata", json_type(), nullable=False, default=dict)
    paperless_document_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    current_version_no: Mapped[int | None] = mapped_column(Integer)
    created_by_id: Mapped[int] = user_fk("created_by", nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True,
        order_by="DocumentVersion.version_no")
    current_version: Mapped["DocumentVersion | None"] = relationship(
        primaryjoin=lambda: and_(
            Document.id == foreign(DocumentVersion.document_id),
            Document.current_version_no == foreign(DocumentVersion.version_no),
        ),
        viewonly=True, uselist=False)
    process_links: Mapped[list["ProcessInstanceDocument"]] = relationship(
        back_populates="document")

    @property
    def process_instance_ids(self) -> list[uuid.UUID]:
        return [link.process_instance_id for link in self.process_links]


class DocumentVersion(Base):
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no"),
        CheckConstraint(
            "(ingest_status IN ('staged', 'submitted') AND staging_path IS NOT NULL) "
            "OR ingest_status IN ('done', 'failed', 'duplicate')",
            name="ck_dv_file_location"),
        partial_index("ix_dv_ingest_pending", "ingest_status",
                      where=text("ingest_status IN ('staged', 'submitted')")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    # --- integracja z Paperless-ngx ---
    ingest_status: Mapped[IngestStatus] = mapped_column(
        str_enum(IngestStatus, "document_version_ingest_status_check"),
        nullable=False, default=IngestStatus.STAGED,
        server_default=IngestStatus.STAGED.value)
    staging_path: Mapped[str | None] = mapped_column(Text)
    paperless_task_id: Mapped[str | None] = mapped_column(Text)
    paperless_version_id: Mapped[int | None] = mapped_column(Integer)
    ingest_error: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime | None] = mapped_column(TZ)
    # ----------------------------------
    uploaded_by_id: Mapped[int] = user_fk("uploaded_by", nullable=False)
    uploaded_at: Mapped[datetime] = created_at_col()
    human_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("human_task.id"))

    document: Mapped[Document] = relationship(back_populates="versions")
    uploaded_by: Mapped[User] = relationship(foreign_keys=[uploaded_by_id])


class ProcessInstanceDocument(Base):
    __tablename__ = "process_instance_document"
    __table_args__ = (Index("ix_pid_document", "document_id"),)

    process_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_instance.id", ondelete="CASCADE"),
        primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document.id"), primary_key=True)
    role: Mapped[DocumentLinkRole] = mapped_column(
        str_enum(DocumentLinkRole, "process_instance_document_role_check"),
        nullable=False, default=DocumentLinkRole.MAIN,
        server_default=DocumentLinkRole.MAIN.value)
    added_at: Mapped[datetime] = created_at_col()

    process_instance: Mapped[ProcessInstance] = relationship(back_populates="document_links")
    document: Mapped[Document] = relationship(back_populates="process_links")


class Comment(Base):
    __tablename__ = "comment"
    __table_args__ = (Index("ix_comment_instance", "process_instance_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    process_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_instance.id", ondelete="CASCADE"),
        nullable=False)
    human_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("human_task.id"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document.id"))
    author_id: Mapped[int] = user_fk(nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    author: Mapped[User] = relationship(foreign_keys=[author_id])


# =====================================================================
# 6. Komentarze
# =====================================================================

class ProcessEvent(Base):
    __tablename__ = "process_event"
    __table_args__ = (
        Index("ix_pe_instance", "process_instance_id", "created_at"),
        Index("ix_pe_actor", "actor_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(int_pk(), primary_key=True, autoincrement=True)
    process_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_instance.id"), nullable=False)
    human_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)  # EventType – bez CHECK, lista rośnie
    actor_id: Mapped[int | None] = user_fk()
    on_behalf_of_id: Mapped[int | None] = user_fk()
    payload: Mapped[dict[str, Any]] = json_col()
    created_at: Mapped[datetime] = created_at_col()

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_id])
    on_behalf_of: Mapped[User | None] = relationship(foreign_keys=[on_behalf_of_id])


# =====================================================================
# 7. Audyt (append-only)
# =====================================================================

class EngineJob(Base):
    __tablename__ = "engine_job"
    __table_args__ = (
        partial_index("ix_job_pending", "run_at", where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(int_pk(), primary_key=True, autoincrement=True)
    process_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_instance.id", ondelete="CASCADE"))
    job_type: Mapped[JobType] = mapped_column(
        str_enum(JobType, "engine_job_job_type_check"), nullable=False)
    payload: Mapped[dict[str, Any]] = json_col()
    run_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=func.now())
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "engine_job_status_check"),
        nullable=False, default=JobStatus.PENDING,
        server_default=JobStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                          server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5,
                                              server_default=text("5"))
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = created_at_col()


# =====================================================================
# 8. Zadania w tle i powiadomienia
# =====================================================================

class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (
        Index("ix_notification_unread", "user_id", text("created_at DESC"),
              postgresql_where=text("read_at IS NULL"),
              sqlite_where=text("read_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(int_pk(), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = user_fk(nullable=False, ondelete="CASCADE")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = json_col()
    created_at: Mapped[datetime] = created_at_col()
    read_at: Mapped[datetime | None] = mapped_column(TZ)


# =====================================================================

# AI 2026-10-05 (TODO.md pkt 2/4): tabela `app_user` została USUNIĘTA.
# Była drugą, niezależną tabelą użytkowników (UUID) obok tabeli `users`
# (models/user.py, Integer id), z której korzysta cały hub i
# api/security.get_current_user - przez to ścieżka wf nie mogła znaleźć konta
# zalogowanego przez Keycloak. Jedynym źródłem tożsamości jest teraz
# `users` + claim `sub` (users.keycloak_sub).


class OrgUnit(Base):
    __tablename__ = "org_unit"

    id: Mapped[uuid.UUID] = uuid_pk()
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("org_unit.id"))
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    keycloak_group: Mapped[str | None] = mapped_column(Text, unique=True)

    parent: Mapped["OrgUnit | None"] = relationship(
        remote_side=[id], back_populates="children")
    children: Mapped[list["OrgUnit"]] = relationship(back_populates="parent")
    memberships: Mapped[list["OrgMembership"]] = relationship(
        back_populates="org_unit", cascade="all, delete-orphan", passive_deletes=True)


class OrgMembership(Base):
    __tablename__ = "org_membership"
    __table_args__ = (
        partial_index("ix_org_membership_unit", "org_unit_id", where=text("is_manager")),
    )

    # AI 2026-10-05: FK do `users` (models/user.py), nie do nieistniejącej
    # już tabeli `app_user`.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("org_unit.id", ondelete="CASCADE"), primary_key=True)
    is_manager: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                             server_default=text("false"))

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    org_unit: Mapped[OrgUnit] = relationship(back_populates="memberships")


# =====================================================================
# 2. Definicje procesów
# =====================================================================

class ProcessModel(Base):
    __tablename__ = "process_model"

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                            server_default=text("true"))
    created_at: Mapped[datetime] = created_at_col()

    versions: Mapped[list["ProcessModelVersion"]] = relationship(
        back_populates="process_model", order_by="ProcessModelVersion.version")


class ProcessModelVersion(Base):
    __tablename__ = "process_model_version"
    __table_args__ = (
        UniqueConstraint("process_model_id", "version"),
        # AI 2026-10-05: unikalność tylko wśród opublikowanych wersji -
        # warunek częściowy także dla SQLite, inaczej unikalność obejmowałaby
        # wszystkie statusy (tylko jedna wersja modelu w ogóle).
        Index("uq_pmv_one_published", "process_model_id", unique=True,
              postgresql_where=text("status = 'published'"),
              sqlite_where=text("status = 'published'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    process_model_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_model.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ProcessModelVersionStatus] = mapped_column(
        str_enum(ProcessModelVersionStatus, "process_model_version_status_check"),
        nullable=False, default=ProcessModelVersionStatus.DRAFT,
        server_default=ProcessModelVersionStatus.DRAFT.value)
    bpmn_process_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    spec_json: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    published_at: Mapped[datetime | None] = mapped_column(TZ)
    published_by: Mapped[int | None] = user_fk()   # users.id (publikujący)
    created_at: Mapped[datetime] = created_at_col()

    process_model: Mapped[ProcessModel] = relationship(back_populates="versions")
    files: Mapped[list["ProcessModelFile"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", passive_deletes=True,
        order_by="ProcessModelFile.file_name")


class ProcessModelFile(Base):
    __tablename__ = "process_model_file"
    __table_args__ = (UniqueConstraint("version_id", "file_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_model_version.id", ondelete="CASCADE"),
        nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[ProcessFileType] = mapped_column(
        str_enum(ProcessFileType, "process_model_file_file_type_check"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)

    version: Mapped[ProcessModelVersion] = relationship(back_populates="files")


class UserSubstitution(Base):
    __tablename__ = "user_substitution"
    __table_args__ = (
        CheckConstraint("valid_to > valid_from", name="ck_substitution_period"),
        CheckConstraint("user_id <> substitute_id", name="ck_substitution_self"),
        Index("ix_substitution_substitute", "substitute_id", "valid_from", "valid_to"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[int] = user_fk(nullable=False)
    substitute_id: Mapped[int] = user_fk(nullable=False)
    process_model_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_model.id"))
    valid_from: Mapped[datetime] = mapped_column(TZ, nullable=False)
    valid_to: Mapped[datetime] = mapped_column(TZ, nullable=False)
    created_by: Mapped[int] = user_fk(nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    substitute: Mapped[User] = relationship(foreign_keys=[substitute_id])


# =====================================================================
# 3. Instancje procesów
# =====================================================================

class ProcessInstance(Base):
    __tablename__ = "process_instance"
    __table_args__ = (
        Index("ix_pi_status", "status"),
        Index("ix_pi_business_key", "business_key"),
        Index("ix_pi_started_by", "started_by", text("started_at DESC")),
        Index("ix_pi_version", "process_model_version_id"),
        partial_index("ix_pi_wakeup", "next_wakeup_at",
                      where=text("next_wakeup_at IS NOT NULL")),
        # AI 2026-10-05: j.w. - indeks GIN na `summary` tylko dla PostgreSQL.
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    process_model_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("process_model_version.id"), nullable=False)
    business_key: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProcessInstanceStatus] = mapped_column(
        str_enum(ProcessInstanceStatus, "process_instance_status_check"),
        nullable=False, default=ProcessInstanceStatus.RUNNING,
        server_default=ProcessInstanceStatus.RUNNING.value)
    summary: Mapped[dict[str, Any]] = json_col()
    started_by_id: Mapped[int | None] = user_fk("started_by")
    started_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[datetime | None] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now(), onupdate=func.now())
    next_wakeup_at: Mapped[datetime | None] = mapped_column(TZ)
    error_message: Mapped[str | None] = mapped_column(Text)
    error_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False,
                                              server_default=text("0"))

    __mapper_args__ = {"version_id_col": lock_version}

    version: Mapped[ProcessModelVersion] = relationship()
    started_by: Mapped[User | None] = relationship(foreign_keys=[started_by_id])
    state: Mapped["ProcessInstanceState | None"] = relationship(
        back_populates="process_instance", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True)
    tasks: Mapped[list["HumanTask"]] = relationship(
        back_populates="process_instance", cascade="all, delete-orphan",
        passive_deletes=True, order_by="HumanTask.created_at")
    document_links: Mapped[list["ProcessInstanceDocument"]] = relationship(
        back_populates="process_instance", cascade="all, delete-orphan",
        passive_deletes=True)

    # Pola denormalizowane dla ProcessInstanceRead – wymagają załadowanego
    # version.process_model (selectinload(ProcessInstance.version)
    #                        .joinedload(ProcessModelVersion.process_model)).
    @property
    def process_model_key(self) -> str:
        return self.version.process_model.key

    @property
    def process_model_name(self) -> str:
        return self.version.process_model.name
