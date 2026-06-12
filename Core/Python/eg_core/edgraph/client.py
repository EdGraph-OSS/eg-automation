import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import (
    after_log,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

from .auth import EdGraphTokenRetriever
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
    EdFiAdminConnection,
    EdFiAdminConnectionCreatedResponse,
    EdFiAdminConnectionTestedResponse,
    EdFiAdminInstance,
    EdFiAdminInstanceApplication,
    EdFiAdminInstanceApplicationEndpoints,
    EdFiAdminInstanceApplicationSecretRegeneratedResponse,
    EdFiAdminInstanceClaimSet,
    EdFiAdminInstanceCreatedResponse,
    EdFiAdminTestConnectionRequest,
    EdFiAdminVendorCreatedResponse,
    OdsBackupCode,
    PaginatedResponse,
)

logger: logging.Logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000
_OPERATIONAL_CONTEXT_URI = "uri://edgraph.com"


def _log_error_response(response: httpx.Response) -> None:
    try:
        body: str = response.text
        if body:
            logger.error("Response body: %s", body)
    except Exception as exc:
        logger.error("Error reading response body: %s", exc)


class EdGraphClient:
    """HTTP client scoped to a single EdGraph tenant.

    Wraps the EdGraph Tenant API (Ed-Fi Admin + Data Sync endpoints).
    All HTTP methods use a shared httpx.Client that injects the Bearer token.
    Transport-level errors are retried up to 3 times with exponential back-off.
    """

    def __init__(self, tenant_url: str, tenant_id: str, token_retriever: EdGraphTokenRetriever) -> None:
        self._tenant_id: str = tenant_id
        self._token_retriever = token_retriever
        self._http = httpx.Client(
            base_url=tenant_url,
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EdGraphClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _path(self, relative: str) -> str:
        return f"/tenants/{self._tenant_id}/{relative}"

    @retry(
        stop=stop_after_attempt(max_attempt_number=3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(exception_types=httpx.TransportError),
        reraise=True,
    )
    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        return self._http.get(
            url=path,
            params=params,
            headers={"Authorization": f"Bearer {self._token_retriever.get()}"},
        )

    @retry(
        stop=stop_after_attempt(max_attempt_number=3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(exception_types=httpx.TransportError),
        reraise=True,
    )
    def _post(self, path: str, json: dict) -> httpx.Response:
        return self._http.post(
            url=path,
            json=json,
            headers={"Authorization": f"Bearer {self._token_retriever.get()}"},
        )

    @retry(
        stop=stop_after_attempt(max_attempt_number=3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(exception_types=httpx.TransportError),
        reraise=True,
    )
    def _put(self, path: str, json: dict | None = None) -> httpx.Response:
        return self._http.put(
            url=path,
            json=json,
            headers={"Authorization": f"Bearer {self._token_retriever.get()}"},
        )

    def get_edfi_connections(
        self,
        database_engine: str,
        connection_type: str = "EdGraphManagedHosted",
    ) -> PaginatedResponse[EdFiAdminConnection]:
        params: dict[str, int | str] = {
            "pageSize": _PAGE_SIZE,
            "pageIndex": 0,
            "filter": FilterBuilder(filter_str=f'databaseEngine == "{database_engine}"')
            .and_(filter_str=f'connectionType == "{connection_type}"')
            .build(),
            "orderBy": "connectionName desc",
        }
        response: httpx.Response = self._get(self._path(relative="edfiadmin/connections"), params=params)
        if response.status_code != 200:
            _log_error_response(response)
            response.raise_for_status()
        data: Any = response.json()
        return PaginatedResponse[EdFiAdminConnection](
            page_index=data["pageIndex"],
            page_size=data["pageSize"],
            count=data["count"],
            data=[EdFiAdminConnection(**item) for item in data["data"]],
        )

    def get_ods_backup_codes(self) -> PaginatedResponse[OdsBackupCode]:
        response: httpx.Response = self._get(self._path(relative="edfiadmin/connections/odsBackupCodes"))
        if response.status_code != 200:
            _log_error_response(response)
            response.raise_for_status()
        data: Any = response.json()
        return PaginatedResponse[OdsBackupCode](
            page_index=data["pageIndex"],
            page_size=data["pageSize"],
            count=data["count"],
            data=[OdsBackupCode(**item) for item in data["data"]],
        )

    def create_edfi_instance(self, request: CreateEdFiAdminInstanceRequest) -> EdFiAdminInstanceCreatedResponse:
        logger.info("Creating Ed-Fi instance '%s'.", request.instance_name)
        response: httpx.Response = self._post(
            self._path(relative="edfiadmin/instances"),
            json=request.model_dump(by_alias=True),
        )
        if response.status_code != 201:
            logger.error("Could not create Ed-Fi instance.")
            _log_error_response(response)
            response.raise_for_status()
        return EdFiAdminInstanceCreatedResponse(**response.json())

    def search_edfi_instances(self, instance_id: str) -> list[EdFiAdminInstance]:
        params: dict[str, int | str] = {
            "pageSize": 10,
            "pageIndex": 0,
            "filter": FilterBuilder(filter_str=f'id == "{instance_id}"').build(),
        }
        response: httpx.Response = self._get(self._path(relative="edfiadmin/instances"), params=params)
        if response.status_code != 200:
            _log_error_response(response)
            response.raise_for_status()
        data: Any = response.json()
        return [EdFiAdminInstance(**item) for item in data["data"]]

    @retry(
        stop=(stop_after_attempt(max_attempt_number=10) | stop_after_delay(2700)),
        wait=wait_exponential(multiplier=3, min=60, max=600),
        retry=retry_if_exception_type(exception_types=InstanceNotProvisionedError),
        before_sleep=before_sleep_log(logger, log_level=logging.INFO),
        after=after_log(logger, log_level=logging.INFO),
    )
    def poll_edfi_instance_provisioned(self, instance_id: str) -> bool:
        """Polls until the instance is provisioned. Retries up to 10 times over 45 minutes."""
        instances: list[EdFiAdminInstance] = self.search_edfi_instances(instance_id)
        if not instances:
            raise InstanceNotFoundError(f"Instance '{instance_id}' not found.")
        instance: EdFiAdminInstance | None = next((i for i in instances if i.id == instance_id), None)
        if instance is None:
            raise InstanceNotFoundError(f"Instance '{instance_id}' not found.")
        if not instance.is_provisioned:
            raise InstanceNotProvisionedError(f"Instance '{instance_id}' is not yet provisioned.")
        return True

    def get_edfi_instance_claimsets(self, instance_id: str) -> PaginatedResponse[EdFiAdminInstanceClaimSet]:
        params: dict[str, int] = {"pageSize": 2000, "pageIndex": 0}
        response: httpx.Response = self._get(
            self._path(relative=f"edfiadmin/instances/{instance_id}/claimsets"), params=params
        )
        if response.status_code != 200:
            _log_error_response(response)
            response.raise_for_status()
        data: Any = response.json()
        return PaginatedResponse[EdFiAdminInstanceClaimSet](
            page_index=data["pageIndex"],
            page_size=data["pageSize"],
            count=data["count"],
            data=[EdFiAdminInstanceClaimSet(**item) for item in data["data"]],
        )

    def find_claimset_by_name(self, instance_id: str, claimset_name: str) -> EdFiAdminInstanceClaimSet:
        """Returns the claim set with the given name, or raises ClaimSetNotFoundError."""
        result: PaginatedResponse[EdFiAdminInstanceClaimSet] = self.get_edfi_instance_claimsets(instance_id)
        match: EdFiAdminInstanceClaimSet | None = next(
            (cs for cs in result.data if cs.claim_set_name == claimset_name), None
        )
        if match is None:
            raise ClaimSetNotFoundError(
                f"Claim set '{claimset_name}' not found in instance '{instance_id}'. "
                "Verify the claim set exists or create it manually before running this script."
            )
        return match

    def create_edfi_instance_vendor(
        self, instance_id: str, request: CreateEdFiAdminVendorRequest
    ) -> EdFiAdminVendorCreatedResponse:
        logger.info("Creating vendor '%s' in instance '%s'.", request.vendor_name, instance_id)
        response: httpx.Response = self._post(
            self._path(relative=f"edfiadmin/instances/{instance_id}/vendors"),
            json=request.model_dump(by_alias=True),
        )
        if response.status_code != 201:
            logger.error("Could not create Ed-Fi vendor.")
            _log_error_response(response)
            response.raise_for_status()
        return EdFiAdminVendorCreatedResponse(**response.json())

    def create_edfi_instance_application(
        self, instance_id: str, request: CreateEdFiAdminApplicationRequest
    ) -> EdFiAdminApplicationCreatedResponse:
        logger.info(
            "Creating application '%s' in instance '%s'.",
            request.application_name,
            instance_id,
        )
        response: httpx.Response = self._post(
            self._path(relative=f"edfiadmin/instances/{instance_id}/applications"),
            json=request.model_dump(by_alias=True),
        )
        if response.status_code != 201:
            logger.error("Could not create Ed-Fi application.")
            _log_error_response(response)
            response.raise_for_status()
        return EdFiAdminApplicationCreatedResponse(**response.json())

    def get_edfi_instance_application(self, instance_id: str, application_id: int) -> EdFiAdminInstanceApplication:
        response: httpx.Response = self._get(
            self._path(relative=f"edfiadmin/instances/{instance_id}/applications/{application_id}/apiClients")
        )
        if response.status_code != 200:
            _log_error_response(response)
            response.raise_for_status()
        data: Any = response.json()
        apps: list[EdFiAdminInstanceApplication] = [EdFiAdminInstanceApplication(**item) for item in data["data"]]
        match: EdFiAdminInstanceApplication | None = next((a for a in apps if a.application_id == application_id), None)
        if match is None:
            raise ApplicationNotFoundError(f"No API client found for application '{application_id}'.")
        return match

    def regenerate_application_secret(
        self, instance_id: str, application_id: int, api_client_id: int
    ) -> EdFiAdminInstanceApplicationSecretRegeneratedResponse:
        logger.info(
            "Regenerating secret for API client '%s' in application '%s'.",
            api_client_id,
            application_id,
        )
        response: httpx.Response = self._put(
            self._path(
                relative=f"edfiadmin/instances/{instance_id}/applications/{application_id}/apiClients/{api_client_id}/regenerate"
            )
        )
        if response.status_code != 200:
            logger.error("Could not regenerate application secret.")
            _log_error_response(response)
            response.raise_for_status()
        return EdFiAdminInstanceApplicationSecretRegeneratedResponse(**response.json())

    def get_edfi_instance_endpoints(self, instance_id: str, year: int) -> EdFiAdminInstanceApplicationEndpoints:
        response: httpx.Response = self._get(
            self._path(relative=f"edfiadmin/instances/{instance_id}/years/{year}/endpoints")
        )
        if response.status_code != 200:
            _log_error_response(response)
            response.raise_for_status()
        return EdFiAdminInstanceApplicationEndpoints(**response.json())

    def search_datasync_connections(
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

        params: dict[str, int | str] = {
            "pageSize": _PAGE_SIZE,
            "pageIndex": 0,
            "filter": filter_builder.build(),
            "orderBy": "name desc",
        }
        response: httpx.Response = self._get(self._path(relative="datasync/connections"), params=params)
        if response.status_code != 200:
            _log_error_response(response)
            response.raise_for_status()
        data: Any = response.json()
        return PaginatedResponse[DataSyncConnection](
            page_index=data["pageIndex"],
            page_size=data["pageSize"],
            count=data["count"],
            data=[DataSyncConnection(**item) for item in data["data"]],
        )

    def test_datasync_connection(self, request: EdFiAdminTestConnectionRequest) -> EdFiAdminConnectionTestedResponse:
        response: httpx.Response = self._post(
            self._path(relative="datasync/connections/testconnection"),
            json=request.model_dump(by_alias=True),
        )
        if response.status_code != 200:
            _log_error_response(response)
            response.raise_for_status()
        return EdFiAdminConnectionTestedResponse(**response.json())

    def create_datasync_connection(
        self, request: CreateEdFiAdminConnectionRequest
    ) -> EdFiAdminConnectionCreatedResponse:
        logger.info("Creating Data Sync connection '%s'.", request.name)
        response: httpx.Response = self._post(
            self._path(relative="datasync/connections"),
            json=request.model_dump(by_alias=True),
        )
        # The API returns 202 Accepted and places the ID in the Location header.
        if response.status_code != 202:
            logger.error("Could not create Data Sync connection.")
            _log_error_response(response)
            response.raise_for_status()

        location: str | None = response.headers.get("Location")
        if not location:
            raise LocationHeaderNotFoundError(
                "Response to create_datasync_connection did not contain a Location header."
            )

        connection_id: str = urlparse(location).path.split("/")[-1]
        logger.debug("Created Data Sync connection '%s'.", connection_id)
        return EdFiAdminConnectionCreatedResponse(connection_id=connection_id)

    def create_datasync_job(self, request: DataSyncCreateJobRequest) -> DataSyncJob:
        logger.info("Creating Data Sync job '%s'.", request.name)
        response: httpx.Response = self._post(
            self._path(relative="datasync/jobs"),
            json=request.model_dump(by_alias=True),
        )
        if response.status_code != 201:
            logger.error("Could not create Data Sync job.")
            _log_error_response(response)
            response.raise_for_status()
        return DataSyncJob(**response.json())
