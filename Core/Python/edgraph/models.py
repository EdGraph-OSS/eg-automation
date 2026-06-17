from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from .exceptions import ApplicationEndpointNotFoundError

EdFiOdsBackupCode = Literal["Empty", "GrandBend"]
EdFiTier = Literal["General Purpose", "Business Critical"]
EdFiDatabaseEngine = Literal["MsSql", "PgSql"]
EdFiVersion = Literal[
    "Suite 3 v6.1",
    "Suite 3 v6.2",
    "Suite 3 v7.2 (Data Standard 4.0)",
    "Suite 3 v7.2 (Data Standard 5.0)",
    "Suite 3 v7.3 (Data Standard 4.0)",
    "Suite 3 v7.3 (Data Standard 5.0)",
]
EdFiExtension = Literal["Tx", "Wi", "Idoe", "Ne", "Core"]
EdFiInstanceDatabaseStatus = Literal[
    "Creating",
    "Create failed",
    "Created",
    "Deleting",
    "Delete failed",
    "Deleted",
    "Reset failed",
    "Resetting (deleting phase)",
    "Resetting (creating phase)",
    "Restoring from clone",
]
EdFiApplicationEndpointType = Literal["Composite", "Discovery", "Resource"]
EdFiApplicationEndpointAccessType = Literal["Primary (Read-Write)", "Replica (Read-only)"]
EdFiInstanceConnectionStatus = Literal[
    "Unknown",
    "None",
    "Connected",
    "Not Connected",
    "Success",
    "Failure",
    "success",
    "failure",
    "Successful",
    "Unsuccessful",
]
EdFiInstanceConnectionResultCode = Literal[
    "Unknown", "BadCredentials", "Unreachable", "MissingClaims", "Success", "BadRequest"
]


class EdGraphModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PaginatedResponse[T](EdGraphModel):
    page_index: int
    page_size: int
    count: int
    data: list[T]

    def has_elements(self) -> bool:
        return len(self.data) > 0


class OdsApiEndpoint(EdGraphModel):
    access_type_id: str
    composites_url: str
    resources_url: str
    discovery_url: str


class OdsApiConnection(EdGraphModel):
    client_id: str
    token_url: str
    endpoints: list[OdsApiEndpoint]


class EdFiAdminConnectionTier(EdGraphModel):
    tier_id: str
    tier_name: str | None = None
    ods_api_connection: OdsApiConnection


class EdFiAdminConnection(EdGraphModel):
    id: str
    connection_name: str
    database_engine: str
    ed_fi_version: str
    ed_fi_extension: str
    hosting_provider: str
    allowed_tenant_ids: list[str]
    tiers: list[EdFiAdminConnectionTier]
    created_by: str
    created_date_time: str
    connection_type: str
    instance_type: str
    is_deleted: bool
    metadata_json: str

    def get_tier(self, tier_name: EdFiTier) -> EdFiAdminConnectionTier:
        matching: list[EdFiAdminConnectionTier] = [t for t in self.tiers if t.tier_name == tier_name]
        if not matching:
            raise ValueError(f"No tier named '{tier_name}' found on connection '{self.connection_name}'.")
        return matching[0]


class OdsBackupCode(EdGraphModel):
    code: EdFiOdsBackupCode | str
    description: str
    tenant_id: str


class InstanceDatabase(EdGraphModel):
    status: EdFiInstanceDatabaseStatus
    selected_tier_id: str
    jobs: Any | None = None


class InstanceDatabases(EdGraphModel):
    admin: InstanceDatabase
    security: InstanceDatabase
    ods: list[InstanceDatabase]


class EdFiAdminInstance(EdGraphModel):
    id: str
    instance_name: str
    connection_name: str
    selected_connection_id: str
    databases: InstanceDatabases
    tenant_id: str
    is_deleted: bool
    applications: list[Any]

    @property
    def is_provisioned(self) -> bool:
        admin_ready: bool = self.databases.admin.status == "Created"
        security_ready: bool = self.databases.security.status == "Created"
        ods_ready: bool = all(db.status == "Created" for db in self.databases.ods)
        return admin_ready and security_ready and ods_ready


class CreateEdFiAdminInstanceSchoolYearsRequest(EdGraphModel):
    year: int
    selected_tier_id: str
    selected_tier_name: str
    ods_backup_code: str
    ods_backup_description: str


class CreateEdFiAdminInstanceRequest(EdGraphModel):
    instance_name: str
    database_engine: str
    selected_connection_id: str
    selected_connection_name: str
    school_years: list[CreateEdFiAdminInstanceSchoolYearsRequest]


class EdFiAdminInstanceCreatedResponse(EdGraphModel):
    instance_id: str
    tenant_id: str


class EdFiAdminInstanceClaimSet(EdGraphModel):
    applications_count: int
    claim_set_id: int
    claim_set_name: str
    is_system_reserved: bool


class EdFiAdminClaimSetCreatedResponse(EdGraphModel):
    tenant_id: str
    instance_id: str
    claim_set_id: int


class EdFiAdminPlaceholderLeaCreatedResponse(EdGraphModel):
    education_organization_id: int
    id: str


class CreateEdFiAdminVendorRequest(EdGraphModel):
    vendor_name: str
    namespace_prefixes: list[str] | None = None


class EdFiAdminVendorCreatedResponse(EdGraphModel):
    tenant_id: str
    instance_id: str
    vendor_id: int
    applications: list[Any]
    namespace_prefixes: list[str] | None = None


class VendorRequest(EdGraphModel):
    namespace_prefixes: list[str]


class EducationOrganizationRequest(EdGraphModel):
    id: str
    education_organization_id: int
    addresses: list[Any]


class CreateEdFiAdminApplicationRequest(EdGraphModel):
    application_name: str
    claim_set_name: str
    operational_context_uri: str
    vendor_id: int
    vendor: VendorRequest
    education_organizations: list[EducationOrganizationRequest]


class EdFiAdminApplicationCreatedResponse(EdGraphModel):
    tenant_id: str
    instance_id: str
    application_id: int
    vendor_id: int


class EdFiAdminInstanceApplication(EdGraphModel):
    application_id: int
    api_client_id: int
    key: str
    secret: str
    name: str
    secret_is_hashed: bool

    def set_secret(self, new_secret: str) -> None:
        self.secret: str = new_secret


class EdFiAdminInstanceApplicationSecretRegeneratedResponse(EdGraphModel):
    tenant_id: str
    instance_id: str
    api_client_id: int
    new_secret: str


class EndpointUrl(EdGraphModel):
    access_type: EdFiApplicationEndpointAccessType | str
    url: str


class EdFiAdminInstanceApplicationEndpoints(EdGraphModel):
    auth_url: str
    composites_urls: list[EndpointUrl]
    discovery_urls: list[EndpointUrl]
    resources_urls: list[EndpointUrl]

    def get_url(
        self,
        url_type: EdFiApplicationEndpointType,
        access_type: EdFiApplicationEndpointAccessType,
    ) -> str:
        urls_map: dict[str, list[EndpointUrl]] = {
            "Composite": self.composites_urls,
            "Discovery": self.discovery_urls,
            "Resource": self.resources_urls,
        }
        urls: list[EndpointUrl] = urls_map.get(url_type, [])

        if not urls:
            raise ValueError(f"No URLs available for type '{url_type}'.")

        for endpoint in urls:
            if endpoint.access_type == access_type:
                return endpoint.url

        raise ApplicationEndpointNotFoundError(f"No '{access_type}' endpoint found for URL type '{url_type}'.")


class ConnectionMetadataField(EdGraphModel):
    code: str
    value: str
    is_secret: bool


class CreateEdFiAdminConnectionRequest(EdGraphModel):
    tenant_id: str
    name: str
    provider_id: str
    connection_type_id: str
    connection_metadata: list[ConnectionMetadataField]


class EdFiAdminConnectionCreatedResponse(EdGraphModel):
    connection_id: str


class EdFiAdminTestConnectionRequest(EdGraphModel):
    connection_id: str | None
    provider_id: str
    connection_type_id: str
    connection_metadata: list[ConnectionMetadataField]


class EdFiAdminConnectionTestedResponse(EdGraphModel):
    status: EdFiInstanceConnectionStatus
    details: str | None = None
    connection_result_code: EdFiInstanceConnectionResultCode | None = None

    @property
    def is_successful(self) -> bool:
        return self.status in ("success", "Success", "Successful", "Connected")


class DataSyncConnection(EdGraphModel):
    tenant_id: str
    connection_id: str
    provider_id: str
    provider_name: str
    connection_type_id: str
    connection_type_name: str
    name: str
    created_by: str | None = None
    created_date_time: str | None = None
    last_modified_by: str | None = None
    last_modified_date_time: str | None = None


class DataSyncCreateJobScheduleRequest(EdGraphModel):
    enabled: bool = False
    begin_date: str
    end_date: str | None = None
    cron: str
    time_zone: str


class DataSyncCreateJobMetadataRequest(EdGraphModel):
    code: str
    value: str


class DataSyncCreateJobRequest(EdGraphModel):
    name: str
    job_type_id: str
    source_connection_id: str
    destination_connection_id: str
    profile_id: str
    job_complete_callback_url: str = ""
    max_api_retry: int = 3
    max_api_failure: int = 3
    notification_emails: list[str] = []
    schedule: DataSyncCreateJobScheduleRequest
    job_metadata: list[DataSyncCreateJobMetadataRequest] = []


class DataSyncJob(EdGraphModel):
    job_id: str
    tenant_id: str
    profile_id: str
    job_type_id: str
    source_connection_id: str
    destination_connection_id: str
    name: str
    job_status: str
    job_execution_status: str
    created_by: str
    created_date_time: str
    last_modified_by: str
    last_modified_date_time: str
