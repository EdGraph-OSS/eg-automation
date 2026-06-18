import json
import logging
from typing import Any
from urllib.parse import urlparse

from edgraph_platform_client import ApiClient, Configuration
from edgraph_platform_client.api import (
    ConnectionsApi,
    InstancesApi,
    InstancesApplicationsApi,
    InstancesClaimSetsApi,
    InstancesEducationOrganizationsLocalEducationAgenciesApi,
    InstancesVendorsApi,
    JobsApi,
)
from edgraph_platform_client.api_response import ApiResponse
from edgraph_platform_client.exceptions import ApiException
from edgraph_platform_client.models import (
    EdfiAdminApiEdfiAdminV1CreateLocalEducationAgencyRequest,
    EdfiAdminApiEdfiAdminV1SaveClaimSetRequest,
)
from tenacity import (
    after_log,
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

from .auth import EdGraphTokenRetriever
from .config import ENVIRONMENT_URLS, ApiUrls, EdGraphEnvironment
from .exceptions import (
    ApplicationNotFoundError,
    ClaimSetNotFoundError,
    InstanceNotFoundError,
    InstanceNotProvisionedError,
    LocationHeaderNotFoundError,
)
from .filters import FilterBuilder
from .models import (
    CreateEdFiAdminApplicationRequest,
    CreateEdFiAdminConnectionRequest,
    CreateEdFiAdminInstanceRequest,
    CreateEdFiAdminVendorRequest,
    DataSyncConnection,
    DataSyncCreateJobRequest,
    DataSyncJob,
    EdFiAdminApplicationCreatedResponse,
    EdFiAdminClaimSetCreatedResponse,
    EdFiAdminConnection,
    EdFiAdminConnectionCreatedResponse,
    EdFiAdminConnectionTestedResponse,
    EdFiAdminInstance,
    EdFiAdminInstanceApplication,
    EdFiAdminInstanceApplicationEndpoints,
    EdFiAdminInstanceApplicationSecretRegeneratedResponse,
    EdFiAdminInstanceClaimSet,
    EdFiAdminInstanceCreatedResponse,
    EdFiAdminPlaceholderLeaCreatedResponse,
    EdFiAdminTestConnectionRequest,
    EdFiAdminVendorCreatedResponse,
    OdsBackupCode,
    PaginatedResponse,
)

logger: logging.Logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000


class _TokenRefreshingApiClient(ApiClient):
    def __init__(self, configuration: Configuration, retriever: EdGraphTokenRetriever) -> None:
        super().__init__(configuration=configuration)
        self._retriever = retriever

    async def call_api(self, *args, **kwargs):
        await self._retriever.ensure_async()
        return await super().call_api(*args, **kwargs)

    def update_params_for_auth(
        self, headers, queries, auth_settings, resource_path, method, body, request_auth=None
    ) -> None:
        self.configuration.access_token = self._retriever.get()
        super().update_params_for_auth(headers, queries, auth_settings, resource_path, method, body, request_auth)


_OPERATIONAL_CONTEXT_URI = "uri://edgraph.com"

_RETRY = retry(
    stop=stop_after_attempt(max_attempt_number=3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(lambda e: isinstance(e, ApiException) and getattr(e, "status", 0) >= 500),
    reraise=True,
)


def _json(api_resp: ApiResponse) -> dict:
    return json.loads(api_resp.raw_data)


class EdGraphClient:
    """EdGraph Tenant API client scoped to a single tenant.

    Wraps the EdGraph Tenant API (Ed-Fi Admin + Data Sync endpoints) using the
    official edgraph-platform-client SDK for HTTP transport. SDK API calls are
    retried up to 3 times with exponential back-off on ApiException.
    """

    def __init__(
        self,
        environment: EdGraphEnvironment,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        urls: ApiUrls = ENVIRONMENT_URLS[environment]
        self._tenant_id: str = tenant_id
        self._retriever = EdGraphTokenRetriever(urls.identity, client_id, client_secret)

        config = Configuration(host=urls.tenant)
        self._api_client = _TokenRefreshingApiClient(configuration=config, retriever=self._retriever)
        self._instances = InstancesApi(self._api_client)
        self._instance_apps = InstancesApplicationsApi(self._api_client)
        self._claimsets = InstancesClaimSetsApi(self._api_client)
        self._leas = InstancesEducationOrganizationsLocalEducationAgenciesApi(self._api_client)
        self._vendors = InstancesVendorsApi(self._api_client)
        self._connections = ConnectionsApi(self._api_client)
        self._jobs = JobsApi(self._api_client)

    async def close(self) -> None:
        self._retriever.close()
        await self._api_client.close()

    async def __aenter__(self) -> EdGraphClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    @_RETRY
    async def get_edfi_connections(
        self,
        database_engine: str,
        connection_type: str = "EdGraphManagedHosted",
    ) -> PaginatedResponse[EdFiAdminConnection]:
        api_resp: ApiResponse[Any] = await self._connections.get_ed_fi_connections_async_with_http_info(
            tenant_id=self._tenant_id,
            page_size=_PAGE_SIZE,
            page_index=0,
            filter=FilterBuilder(filter_str=f'databaseEngine == "{database_engine}"')
            .and_(filter_str=f'connectionType == "{connection_type}"')
            .build(),
            order_by="connectionName desc",
        )
        data = _json(api_resp)
        return PaginatedResponse[EdFiAdminConnection](
            page_index=data["pageIndex"],
            page_size=data["pageSize"],
            count=data["count"],
            data=[EdFiAdminConnection(**item) for item in data["data"]],
        )

    @_RETRY
    async def get_ods_backup_codes(self) -> PaginatedResponse[OdsBackupCode]:
        api_resp: ApiResponse[
            Any
        ] = await self._connections.get_ed_fi_ods_backup_codes_descriptors_async_with_http_info(
            tenant_id=self._tenant_id,
        )
        data = _json(api_resp)
        return PaginatedResponse[OdsBackupCode](
            page_index=data["pageIndex"],
            page_size=data["pageSize"],
            count=data["count"],
            data=[OdsBackupCode(**item) for item in data["data"]],
        )

    @_RETRY
    async def create_edfi_instance(self, request: CreateEdFiAdminInstanceRequest) -> EdFiAdminInstanceCreatedResponse:
        logger.info("Creating Ed-Fi instance '%s'.", request.instance_name)
        api_resp = await self._instances.create_instance_async_with_http_info(
            tenant_id=self._tenant_id,
            edfi_admin_api_edfi_admin_v1_create_instance_request=request.model_dump(by_alias=True),
        )
        return EdFiAdminInstanceCreatedResponse(**_json(api_resp))

    @_RETRY
    async def search_edfi_instances(self, instance_id: str) -> list[EdFiAdminInstance]:
        api_resp = await self._instances.get_instances_async_with_http_info(
            tenant_id=self._tenant_id,
            page_size=10,
            page_index=0,
            filter=FilterBuilder(filter_str=f'id == "{instance_id}"').build(),
        )
        data = _json(api_resp)
        return [EdFiAdminInstance(**item) for item in data["data"]]

    @retry(
        stop=(stop_after_attempt(max_attempt_number=10) | stop_after_delay(2700)),
        wait=wait_exponential(multiplier=3, min=60, max=600),
        retry=retry_if_exception_type(exception_types=InstanceNotProvisionedError),
        before_sleep=before_sleep_log(logger, log_level=logging.INFO),
        after=after_log(logger, log_level=logging.INFO),
    )
    async def poll_edfi_instance_provisioned(self, instance_id: str) -> bool:
        """Polls until the instance is provisioned. Retries up to 10 times over 45 minutes."""
        instances: list[EdFiAdminInstance] = await self.search_edfi_instances(instance_id)
        if not instances:
            raise InstanceNotFoundError(f"Instance '{instance_id}' not found.")
        instance: EdFiAdminInstance | None = next((i for i in instances if i.id == instance_id), None)
        if instance is None:
            raise InstanceNotFoundError(f"Instance '{instance_id}' not found.")
        if not instance.is_provisioned:
            raise InstanceNotProvisionedError(f"Instance '{instance_id}' is not yet provisioned.")
        return True

    @_RETRY
    async def get_edfi_instance_claimsets(self, instance_id: str) -> PaginatedResponse[EdFiAdminInstanceClaimSet]:
        api_resp = await self._claimsets.get_claim_sets_async_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            page_size=2000,
            page_index=0,
        )
        data = _json(api_resp)
        return PaginatedResponse[EdFiAdminInstanceClaimSet](
            page_index=data["pageIndex"],
            page_size=data["pageSize"],
            count=data["count"],
            data=[EdFiAdminInstanceClaimSet(**item) for item in data["data"]],
        )

    async def find_claimset_by_name(self, instance_id: str, claimset_name: str) -> EdFiAdminInstanceClaimSet:
        """Returns the claim set with the given name, or raises ClaimSetNotFoundError."""
        result: PaginatedResponse[EdFiAdminInstanceClaimSet] = await self.get_edfi_instance_claimsets(instance_id)
        match: EdFiAdminInstanceClaimSet | None = next(
            (cs for cs in result.data if cs.claim_set_name == claimset_name), None
        )
        if match is None:
            raise ClaimSetNotFoundError(
                f"Claim set '{claimset_name}' not found in instance '{instance_id}'. "
                "Verify the claim set exists or create it manually before running this script."
            )
        return match

    @_RETRY
    async def get_instance_resource_claims(self, instance_id: str) -> list[Any]:
        api_resp = await self._claimsets.get_resource_claims_grid_async_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            claim_set_id=0,
        )
        return _json(api_resp).get("resourceClaims") or []

    @_RETRY
    async def create_edfi_instance_claimset(
        self, instance_id: str, name: str, resource_claims: list[Any]
    ) -> EdFiAdminClaimSetCreatedResponse:
        logger.info("Creating claim set '%s' in instance '%s'.", name, instance_id)
        request = EdfiAdminApiEdfiAdminV1SaveClaimSetRequest.model_validate(
            {
                "tenantId": self._tenant_id,
                "instanceId": instance_id,
                "claimSetId": 0,
                "claimSetName": name,
                "resourceClaims": resource_claims,
            }
        )
        api_resp = await self._claimsets.create_claim_set_async_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            edfi_admin_api_edfi_admin_v1_save_claim_set_request=request,
        )
        return EdFiAdminClaimSetCreatedResponse(**_json(api_resp))

    @_RETRY
    async def create_placeholder_lea(
        self, instance_id: str, school_year: int, lea_body: dict[str, Any]
    ) -> EdFiAdminPlaceholderLeaCreatedResponse:
        logger.info("Creating placeholder LEA in instance '%s' for year %s.", instance_id, school_year)
        request = EdfiAdminApiEdfiAdminV1CreateLocalEducationAgencyRequest.model_validate(
            {
                "tenantId": self._tenant_id,
                "instanceId": instance_id,
                "year": school_year,
                "localEducationAgency": lea_body,
            }
        )
        api_resp = await self._leas.create_local_education_agency_async_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            year=school_year,
            edfi_admin_api_edfi_admin_v1_create_local_education_agency_request=request,
        )
        data = _json(api_resp)
        lea = data["localEducationAgency"]
        return EdFiAdminPlaceholderLeaCreatedResponse(
            id=lea["id"],
            education_organization_id=lea["educationOrganizationId"],
        )

    @_RETRY
    async def create_edfi_instance_vendor(
        self, instance_id: str, request: CreateEdFiAdminVendorRequest
    ) -> EdFiAdminVendorCreatedResponse:
        logger.info("Creating vendor '%s' in instance '%s'.", request.vendor_name, instance_id)
        api_resp = await self._vendors.create_vendor_async_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            edfi_admin_api_edfi_admin_v1_create_vendor_request=request.model_dump(by_alias=True),
        )
        return EdFiAdminVendorCreatedResponse(**_json(api_resp))

    @_RETRY
    async def create_edfi_instance_application(
        self, instance_id: str, request: CreateEdFiAdminApplicationRequest
    ) -> EdFiAdminApplicationCreatedResponse:
        logger.info(
            "Creating application '%s' in instance '%s'.",
            request.application_name,
            instance_id,
        )
        api_resp: ApiResponse[Any] = await self._instance_apps.create_application_async_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            edfi_admin_api_edfi_admin_v1_create_ed_fi_application_request=request.model_dump(by_alias=True),
        )
        return EdFiAdminApplicationCreatedResponse(**_json(api_resp))

    @_RETRY
    async def get_edfi_instance_application(
        self, instance_id: str, application_id: int
    ) -> EdFiAdminInstanceApplication:
        api_resp: ApiResponse[Any] = await self._instance_apps.get_application_api_clients_async_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            application_id=str(application_id),
        )
        data = _json(api_resp)
        apps: list[EdFiAdminInstanceApplication] = [EdFiAdminInstanceApplication(**item) for item in data["data"]]
        match: EdFiAdminInstanceApplication | None = next((a for a in apps if a.application_id == application_id), None)
        if match is None:
            raise ApplicationNotFoundError(f"No API client found for application '{application_id}'.")
        return match

    @_RETRY
    async def regenerate_application_secret(
        self, instance_id: str, application_id: int, api_client_id: int
    ) -> EdFiAdminInstanceApplicationSecretRegeneratedResponse:
        logger.info(
            "Regenerating secret for API client '%s' in application '%s'.",
            api_client_id,
            application_id,
        )
        api_resp: ApiResponse[Any] = await self._instance_apps.regenerate_api_client_secret_async_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            application_id=application_id,
            api_client_id=api_client_id,
        )
        return EdFiAdminInstanceApplicationSecretRegeneratedResponse(**_json(api_resp))

    @_RETRY
    async def get_edfi_instance_endpoints(self, instance_id: str, year: int) -> EdFiAdminInstanceApplicationEndpoints:
        api_resp: ApiResponse[Any] = await self._instances.get_ed_fi_admin_instance_year_endpoints_with_http_info(
            tenant_id=self._tenant_id,
            instance_id=instance_id,
            year=year,
        )
        return EdFiAdminInstanceApplicationEndpoints(**_json(api_resp))

    @_RETRY
    async def search_datasync_connections(
        self,
        name: str,
        connection_type_id: str | None = None,
        provider_id: str | None = None,
    ) -> PaginatedResponse[DataSyncConnection]:
        filter_builder = FilterBuilder(filter_str=f'name == "{name}"')
        if connection_type_id:
            filter_builder.and_(filter_str=f'connectionTypeId == "{connection_type_id}"')
        if provider_id:
            filter_builder.and_(filter_str=f'providerId == "{provider_id}"')

        api_resp: ApiResponse[Any] = await self._connections.get_all_tenant_data_sync_connections_with_http_info(
            tenant_id=self._tenant_id,
            page_size=_PAGE_SIZE,
            page_index=0,
            filter=filter_builder.build(),
            order_by="name desc",
        )
        data = _json(api_resp)
        return PaginatedResponse[DataSyncConnection](
            page_index=data["pageIndex"],
            page_size=data["pageSize"],
            count=data["count"],
            data=[DataSyncConnection(**item) for item in data["data"]],
        )

    @_RETRY
    async def test_datasync_connection(
        self, request: EdFiAdminTestConnectionRequest
    ) -> EdFiAdminConnectionTestedResponse:
        api_resp: ApiResponse[Any] = await self._connections.connection_tested_response_with_http_info(
            tenant_id=self._tenant_id,
            data_sync_api_connection_v1_test_connection_request=request.model_dump(by_alias=True),
        )
        return EdFiAdminConnectionTestedResponse(**_json(api_resp))

    @_RETRY
    async def create_datasync_connection(
        self, request: CreateEdFiAdminConnectionRequest
    ) -> EdFiAdminConnectionCreatedResponse:
        logger.info("Creating Data Sync connection '%s'.", request.name)
        api_resp: ApiResponse[None] = await self._connections.create_tenant_data_sync_connection_with_http_info(
            tenant_id=self._tenant_id,
            ed_graph_http_aggregators_tenant_api_controllers_v1_view_models_requests_connections_create_connection_request=request.model_dump(
                by_alias=True
            ),
        )
        location: str | None = (api_resp.headers or {}).get("Location")
        if not location:
            raise LocationHeaderNotFoundError(
                "Response to create_datasync_connection did not contain a Location header."
            )
        connection_id: str = urlparse(location).path.split("/")[-1]
        logger.debug("Created Data Sync connection '%s'.", connection_id)
        return EdFiAdminConnectionCreatedResponse(connection_id=connection_id)

    @_RETRY
    async def create_datasync_job(self, request: DataSyncCreateJobRequest) -> DataSyncJob:
        logger.info("Creating Data Sync job '%s'.", request.name)
        api_resp: ApiResponse[Any] = await self._jobs.create_tenant_data_sync_job_with_http_info(
            tenant_id=self._tenant_id,
            ed_graph_http_aggregators_tenant_api_controllers_v1_view_models_requests_jobs_create_job_request=request.model_dump(
                by_alias=True
            ),
        )
        return DataSyncJob(**_json(api_resp))
