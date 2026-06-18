"""sync_from_act — Item #16707

Configures data synchronization from ACT into the district's Ed-Fi instance:
  - Creates Data Sync connection: "ACT {year} (Source)"
    Credentials sourced from the external Ed-Fi instance holding ACT creds.
  - Creates Data Sync connection: "Ed-Fi {year} ACT (Destination)"
    Credentials sourced from the "ACT to District Sync" application (tenant-state.json).
  - Creates Data Sync job: "ACT to Ed-Fi Sync ({year})", nightly @ 22:00 CST.

Prerequisites: setup_tenant and sync_from_sea must have run.
State written: act-sync-state.json
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
    DataSyncCreateJobRequest,
    DataSyncCreateJobScheduleRequest,
    EdFiAdminTestConnectionRequest,
)

from ._constants import (
    DATASYNC_JOB_TYPE_ID,
    DATASYNC_PROFILE_ID,
    EDFI_CONNECTION_PROVIDER_ID,
    EDFI_CONNECTION_TYPE_ID,
    SCHEDULE_TIMEZONE,
)
from .models import SyncState, TenantState

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

STATE_FILENAME = "act-sync-state.json"
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
    load_dotenv()
    tenant_state_path = Path(TENANT_STATE_FILENAME)
    sea_state_path = Path("sea-sync-state.json")
    state_path = Path(STATE_FILENAME)

    for required in (tenant_state_path, sea_state_path):
        if not required.exists():
            raise FileNotFoundError(f"'{required}' not found. Run setup_tenant and sync_from_sea first.")

    environment: EdGraphEnvironment = cast(EdGraphEnvironment, os.environ.get("EDGRAPH_ENVIRONMENT", "Dev"))
    client_id: str = os.environ["EDGRAPH_CLIENT_ID"]
    client_secret: str = os.environ["EDGRAPH_CLIENT_SECRET"]
    tenant_id: str = os.environ["TENANT_ID"]
    act_external_instance_id: str = os.environ["ACT_EXTERNAL_INSTANCE_ID"]

    tenant_state = TenantState.load(tenant_state_path)
    state = SyncState.load(state_path) if state_path.exists() else SyncState()
    school_year = tenant_state.school_year

    if tenant_state.act_sync_credentials is None:
        raise ValueError(
            "ACT sync application credentials are missing from tenant-state.json. Re-run setup_tenant to populate them."
        )

    async with EdGraphClient(environment, tenant_id, client_id, client_secret) as client:
        act_endpoints = await client.get_edfi_instance_endpoints(act_external_instance_id, school_year)
        act_api_client = await client.get_edfi_instance_application(
            act_external_instance_id,
            application_id=0,  # TODO: obtain the correct application ID
        )

        source_name = f"ACT {school_year} (Source)"
        if not state.source_connection_id:
            existing = await client.search_datasync_connections(source_name)
            if existing.has_elements():
                state.source_connection_id = existing.data[0].connection_id
                logger.info("Reusing existing source connection '%s'.", state.source_connection_id)
            else:
                created = await client.create_datasync_connection(
                    CreateEdFiAdminConnectionRequest(
                        tenant_id=tenant_id,
                        name=source_name,
                        provider_id=EDFI_CONNECTION_PROVIDER_ID,
                        connection_type_id=EDFI_CONNECTION_TYPE_ID,
                        connection_metadata=_edfi_connection_metadata(
                            act_endpoints.auth_url,
                            act_endpoints.get_url("Resource", "Primary (Read-Write)"),
                            act_api_client.key,
                            act_api_client.secret,
                        ),
                    )
                )
                state.source_connection_id = created.connection_id
            state.save(state_path)

        dest_name = f"Ed-Fi {school_year} ACT (Destination)"
        act_creds = tenant_state.act_sync_credentials
        if not state.destination_connection_id:
            test_result = await client.test_datasync_connection(
                EdFiAdminTestConnectionRequest(
                    connection_id=None,
                    provider_id=EDFI_CONNECTION_PROVIDER_ID,
                    connection_type_id=EDFI_CONNECTION_TYPE_ID,
                    connection_metadata=_edfi_connection_metadata(
                        act_creds.auth_url, act_creds.resources_url, act_creds.key, act_creds.secret
                    ),
                )
            )
            if not test_result.is_successful:
                raise ConnectionTestFailedError(
                    f"Destination connection test failed. Result code: {test_result.connection_result_code}"
                )

            existing = await client.search_datasync_connections(dest_name)
            if existing.has_elements():
                state.destination_connection_id = existing.data[0].connection_id
                logger.info("Reusing existing destination connection '%s'.", state.destination_connection_id)
            else:
                created = await client.create_datasync_connection(
                    CreateEdFiAdminConnectionRequest(
                        tenant_id=tenant_id,
                        name=dest_name,
                        provider_id=EDFI_CONNECTION_PROVIDER_ID,
                        connection_type_id=EDFI_CONNECTION_TYPE_ID,
                        connection_metadata=_edfi_connection_metadata(
                            act_creds.auth_url, act_creds.resources_url, act_creds.key, act_creds.secret
                        ),
                    )
                )
                state.destination_connection_id = created.connection_id
            state.save(state_path)

        if not state.job_id:
            job = await client.create_datasync_job(
                DataSyncCreateJobRequest(
                    name=f"ACT to Ed-Fi Sync ({school_year})",
                    job_type_id=DATASYNC_JOB_TYPE_ID,
                    source_connection_id=state.source_connection_id,
                    destination_connection_id=state.destination_connection_id,
                    profile_id=DATASYNC_PROFILE_ID,
                    schedule=DataSyncCreateJobScheduleRequest(
                        enabled=False,
                        begin_date=datetime.date.today().isoformat(),
                        cron="0 22 * * *",
                        time_zone=SCHEDULE_TIMEZONE,
                    ),
                )
            )
            state.job_id = job.job_id
            state.save(state_path)
            logger.info("Created Data Sync job '%s'.", job.job_id)

        logger.info("sync_from_act completed. State saved to '%s'.", state_path)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
