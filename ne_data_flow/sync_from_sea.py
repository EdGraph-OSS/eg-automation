"""sync_from_sea — Item #16704

Configures data synchronization from NDE/Adviser into the district's Ed-Fi instance:
  - Resolves Data Sync source connection by name from NDE_CONNECTION_ID env var.
    The connection must already be provisioned; this script will raise an error if not found.
  - Creates Data Sync connection: "Ed-Fi {year} NE SEA (Destination)"
    Credentials sourced from the "NE SEA to District Sync" application (tenant-state.json).
  - Creates Data Sync job: "NE SEA to Ed-Fi Sync ({year})", nightly @ 21:00 CST.

Prerequisite: setup_tenant must have run (reads tenant-state.json).
State written: sea-sync-state.json
"""

import asyncio
import datetime
import logging
import os
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from edgraph.client import EdGraphClient
from edgraph.config import EdGraphEnvironment
from edgraph.exceptions import ConnectionTestFailedError
from edgraph.models import (
    ConnectionMetadataField,
    CreateEdFiAdminConnectionRequest,
    DataSyncConnection,
    DataSyncCreateJobMetadataRequest,
    DataSyncCreateJobRequest,
    DataSyncCreateJobScheduleRequest,
    DataSyncJobCreatedResponse,
    EdFiAdminConnectionCreatedResponse,
    EdFiAdminConnectionTestedResponse,
    EdFiAdminTestConnectionRequest,
    PaginatedResponse,
)

from ._constants import (
    DATASYNC_JOB_TYPE_NAME,
    EDFI_CONNECTION_PROVIDER_ID,
    EDFI_CONNECTION_TYPE_ID,
    SCHEDULE_TIMEZONE,
)
from ._edfi_resources import get_entities_value
from .models import ApplicationCredentials, SyncState, TenantState

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

STATE_FILENAME = "sea-sync-state.json"
TENANT_STATE_FILENAME = "tenant-state.json"


def _edfi_connection_metadata(
    auth_url: str, resources_url: str, key: str, secret: str
) -> list[ConnectionMetadataField]:
    return [
        ConnectionMetadataField(code="apiAuthorizationUrl", value=auth_url, is_secret=False),
        ConnectionMetadataField(code="apiResourcesUrl", value=resources_url, is_secret=False),
        ConnectionMetadataField(code="apiKey", value=key, is_secret=False),
        ConnectionMetadataField(code="apiSecret", value=secret, is_secret=True),
    ]


async def _main() -> None:
    load_dotenv(override=True)
    tenant_state_path = Path(TENANT_STATE_FILENAME)
    state_path = Path(STATE_FILENAME)

    if not tenant_state_path.exists():
        raise FileNotFoundError(f"'{tenant_state_path}' not found. Run setup_tenant first.")

    environment: EdGraphEnvironment = cast(EdGraphEnvironment, os.environ.get("EDGRAPH_ENVIRONMENT", "Dev"))
    client_id: str = os.environ["EDGRAPH_CLIENT_ID"]
    client_secret: str = os.environ["EDGRAPH_CLIENT_SECRET"]
    tenant_id: str = os.environ["TENANT_ID"]
    nde_connection_id: str = os.environ["NDE_CONNECTION_ID"]

    tenant_state: TenantState = TenantState.load(tenant_state_path)
    state: SyncState = SyncState.load(state_path) if state_path.exists() else SyncState()
    school_year: int = tenant_state.school_year

    if tenant_state.sea_sync_application_id is None:
        raise ValueError(
            "SEA sync application ID is missing from tenant-state.json. Re-run setup_tenant to populate it."
        )

    if tenant_state.sea_sync_credentials is None:
        raise ValueError(
            "SEA sync application credentials are missing from tenant-state.json. Re-run setup_tenant to populate them."
        )

    async with EdGraphClient(environment, tenant_id, client_id, client_secret) as client:
        if not state.source_connection_id:
            connection: DataSyncConnection = await client.get_datasync_connection(nde_connection_id)
            state.source_connection_id = connection.connection_id
            logger.info("Found source connection '%s'.", state.source_connection_id)
            state.save(state_path)

        dest_name = f"Ed-Fi {school_year} NE SEA (Destination)"
        sea_creds: ApplicationCredentials = tenant_state.sea_sync_credentials
        if not state.destination_connection_id:
            test_result: EdFiAdminConnectionTestedResponse = await client.test_datasync_connection(
                EdFiAdminTestConnectionRequest(
                    connection_id=None,
                    provider_id=EDFI_CONNECTION_PROVIDER_ID,
                    connection_type_id=EDFI_CONNECTION_TYPE_ID,
                    connection_metadata=_edfi_connection_metadata(
                        sea_creds.auth_url, sea_creds.resources_url, sea_creds.key, sea_creds.secret
                    ),
                )
            )
            if not test_result.is_successful:
                raise ConnectionTestFailedError(
                    f"Destination connection test failed. Result code: {test_result.connection_result_code}"
                )

            existing: PaginatedResponse[DataSyncConnection] = await client.search_datasync_connections(dest_name)
            if existing.has_elements():
                state.destination_connection_id = existing.data[0].connection_id
                logger.info("Reusing existing destination connection '%s'.", state.destination_connection_id)
            else:
                created: EdFiAdminConnectionCreatedResponse = await client.create_datasync_connection(
                    CreateEdFiAdminConnectionRequest(
                        tenant_id=tenant_id,
                        name=dest_name,
                        provider_id=EDFI_CONNECTION_PROVIDER_ID,
                        connection_type_id=EDFI_CONNECTION_TYPE_ID,
                        connection_metadata=_edfi_connection_metadata(
                            sea_creds.auth_url, sea_creds.resources_url, sea_creds.key, sea_creds.secret
                        ),
                    )
                )
                state.destination_connection_id = created.connection_id
            state.save(state_path)

        if not state.job_id:
            job_type_id, profile_id = await client.get_datasync_job_type(DATASYNC_JOB_TYPE_NAME)
            nde_edfi_base_url: str = sea_creds.resources_url.rstrip("/").removesuffix("/data/v3")
            entities: str = await get_entities_value(nde_edfi_base_url, sea_creds.key, sea_creds.secret)
            logger.info("Resolved %d entities for job metadata.", entities.count(";"))
            job: DataSyncJobCreatedResponse = await client.create_datasync_job(
                request=DataSyncCreateJobRequest(
                    name=f"NE SEA to Ed-Fi Sync ({school_year})",
                    job_type_id=job_type_id,
                    source_connection_id=state.source_connection_id,
                    destination_connection_id=state.destination_connection_id,
                    profile_id=profile_id,
                    schedule=DataSyncCreateJobScheduleRequest(
                        enabled=True,
                        begin_date=datetime.date.today().isoformat(),
                        cron="0 0 21 * * ? *",
                        time_zone=SCHEDULE_TIMEZONE,
                    ),
                    job_metadata=[
                        DataSyncCreateJobMetadataRequest(code="entities", value=entities),
                        DataSyncCreateJobMetadataRequest(code="maxLimitRecord", value="100"),
                        DataSyncCreateJobMetadataRequest(code="studentIdSystemDescriptor", value=""),
                        DataSyncCreateJobMetadataRequest(code="staffIdSystemDescriptor", value=""),
                        DataSyncCreateJobMetadataRequest(
                            code="schoolEducationOrganizationIdSystemDescriptor", value=""
                        ),
                        DataSyncCreateJobMetadataRequest(code="localEducationOrganizationIdSystemDescriptor", value=""),
                        DataSyncCreateJobMetadataRequest(code="stateEducationOrganizationIdSystemDescriptor", value=""),
                        DataSyncCreateJobMetadataRequest(
                            code="serviceCenterEducationOrganizationIdSystemDescriptor", value=""
                        ),
                        DataSyncCreateJobMetadataRequest(code="ObfuscateDocumentId", value=""),
                    ],
                )
            )
            state.job_id = job.job_id
            state.save(state_path)
            logger.info("Created Data Sync job '%s'.", job.job_id)

        logger.info("sync_from_sea completed. State saved to '%s'.", state_path)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
