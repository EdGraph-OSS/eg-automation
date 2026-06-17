from pathlib import Path
from typing import Self

from pydantic import BaseModel


class State(BaseModel):
    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class ApplicationCredentials(BaseModel):
    auth_url: str
    resources_url: str
    key: str
    secret: str


class TenantState(State):
    tenant_id: str
    district_name: str
    school_year: int
    instance_id: str | None = None
    nde_vendor_id: int | None = None
    act_vendor_id: int | None = None
    district_vendor_id: int | None = None
    sea_sync_application_id: int | None = None
    act_sync_application_id: int | None = None
    esu_sync_application_id: int | None = None
    sea_sync_credentials: ApplicationCredentials | None = None
    act_sync_credentials: ApplicationCredentials | None = None
    esu_sync_credentials: ApplicationCredentials | None = None


class EsuState(State):
    esu_tenant_id: str
    esu_name: str
    school_year: int
    instance_id: str | None = None
    vendor_id: int | None = None


class SyncState(State):
    source_connection_id: str | None = None
    destination_connection_id: str | None = None
    job_id: str | None = None


class EsuSyncState(State):
    esu_application_id: int | None = None
    esu_sync_credentials: ApplicationCredentials | None = None
    source_connection_id: str | None = None
    destination_connection_id: str | None = None
    job_id: str | None = None
