"""obfuscated_sync_to_esu — Item #16706

Configures the obfuscated district→ESU data sync:
  - In the ESU tenant: creates application "{districtName} - ESU Obfuscated Sync"
    under the ESU vendor, with the "Read/Write All - District Only" claim set.
  - In the district tenant:
    - Creates Data Sync connection: "Ed-Fi {year} (Source)"
      Credentials from the "District to ESU Obfuscated Sync" application (tenant-state.json).
    - Creates Data Sync connection: "{esuName} Ed-Fi {year} (Destination)"
      Credentials from the new ESU application created above.
    - Creates Data Sync job: "District to {esuName} Obfuscated Sync ({year})",
      nightly @ 23:00 CST.

Prerequisites: setup_tenant, sync_from_sea, sync_from_act, and setup_esu must have run.
State written: esu-sync-state.json
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
from edgraph.exceptions import ClaimSetNotFoundError, ConnectionTestFailedError
from edgraph.models import (
    ConnectionMetadataField,
    CreateEdFiAdminApplicationRequest,
    CreateEdFiAdminConnectionRequest,
    DataSyncCreateJobRequest,
    DataSyncCreateJobScheduleRequest,
    EdFiAdminTestConnectionRequest,
    VendorRequest,
)

from ._constants import (
    CLAIMSET_READ_WRITE_ALL_DISTRICT_ONLY,
    DATASYNC_JOB_TYPE_ID,
    DATASYNC_PROFILE_ID,
    EDFI_CONNECTION_PROVIDER_ID,
    EDFI_CONNECTION_TYPE_ID,
    OPERATIONAL_CONTEXT_URI,
    SCHEDULE_TIMEZONE,
)
from .models import ApplicationCredentials, EsuState, EsuSyncState, TenantState

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

STATE_FILENAME = "esu-sync-state.json"
TENANT_STATE_FILENAME = "tenant-state.json"
ESU_STATE_FILENAME = "esu-state.json"


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
    esu_state_path = Path(ESU_STATE_FILENAME)
    state_path = Path(STATE_FILENAME)

    for required in (tenant_state_path, esu_state_path):
        if not required.exists():
            raise FileNotFoundError(f"'{required}' not found. Run all prerequisite scripts first.")

    environment: EdGraphEnvironment = cast(EdGraphEnvironment, os.environ.get("EDGRAPH_ENVIRONMENT", "Dev"))
    client_id: str = os.environ["EDGRAPH_CLIENT_ID"]
    client_secret: str = os.environ["EDGRAPH_CLIENT_SECRET"]
    tenant_id: str = os.environ["TENANT_ID"]
    district_name: str = os.environ["DISTRICT_NAME"]
    esu_tenant_id: str = os.environ["ESU_TENANT_ID"]

    tenant_state = TenantState.load(tenant_state_path)
    esu_state = EsuState.load(esu_state_path)
    state = EsuSyncState.load(state_path) if state_path.exists() else EsuSyncState()
    school_year = tenant_state.school_year
    esu_name = esu_state.esu_name

    if tenant_state.esu_sync_credentials is None:
        raise ValueError(
            "ESU sync application credentials are missing from tenant-state.json. Re-run setup_tenant to populate them."
        )
    if esu_state.instance_id is None or esu_state.vendor_id is None:
        raise ValueError("ESU instance or vendor ID is missing from esu-state.json. Re-run setup_esu to populate them.")

    async with (
        EdGraphClient(environment, tenant_id, client_id, client_secret) as district_client,
        EdGraphClient(environment, esu_tenant_id, client_id, client_secret) as esu_client,
    ):
        if not state.esu_application_id:
            try:
                claimset = await esu_client.find_claimset_by_name(
                    esu_state.instance_id, CLAIMSET_READ_WRITE_ALL_DISTRICT_ONLY
                )
            except ClaimSetNotFoundError as exc:
                logger.error("%s\nCreate the missing claim set manually in EdGraph before re-running.", exc)
                raise

            app = await esu_client.create_edfi_instance_application(
                esu_state.instance_id,
                CreateEdFiAdminApplicationRequest(
                    application_name=f"{district_name} - ESU Obfuscated Sync",
                    claim_set_name=claimset.claim_set_name,
                    operational_context_uri=OPERATIONAL_CONTEXT_URI,
                    vendor_id=esu_state.vendor_id,
                    vendor=VendorRequest(namespace_prefixes=[]),
                    education_organizations=[],
                ),
            )
            state.esu_application_id = app.application_id
            state.save(state_path)

        if not state.esu_sync_credentials:
            esu_api_client = await esu_client.get_edfi_instance_application(esu_state.instance_id, state.esu_application_id)
            secret_resp = await esu_client.regenerate_application_secret(
                esu_state.instance_id, state.esu_application_id, esu_api_client.api_client_id
            )
            esu_endpoints = await esu_client.get_edfi_instance_endpoints(esu_state.instance_id, school_year)
            state.esu_sync_credentials = ApplicationCredentials(
                auth_url=esu_endpoints.auth_url,
                resources_url=esu_endpoints.get_url("Resource", "Primary (Read-Write)"),
                key=esu_api_client.key,
                secret=secret_resp.new_secret,
            )
            state.save(state_path)

        source_name = f"Ed-Fi {school_year} (Source)"
        esu_creds_district = tenant_state.esu_sync_credentials
        if not state.source_connection_id:
            existing = await district_client.search_datasync_connections(source_name)
            if existing.has_elements():
                state.source_connection_id = existing.data[0].connection_id
                logger.info("Reusing existing source connection '%s'.", state.source_connection_id)
            else:
                created = await district_client.create_datasync_connection(
                    CreateEdFiAdminConnectionRequest(
                        tenant_id=tenant_id,
                        name=source_name,
                        provider_id=EDFI_CONNECTION_PROVIDER_ID,
                        connection_type_id=EDFI_CONNECTION_TYPE_ID,
                        connection_metadata=_edfi_connection_metadata(
                            esu_creds_district.auth_url,
                            esu_creds_district.resources_url,
                            esu_creds_district.key,
                            esu_creds_district.secret,
                        ),
                    )
                )
                state.source_connection_id = created.connection_id
            state.save(state_path)

        dest_name = f"{esu_name} Ed-Fi {school_year} (Destination)"
        esu_creds = state.esu_sync_credentials
        if not state.destination_connection_id:
            test_result = await district_client.test_datasync_connection(
                EdFiAdminTestConnectionRequest(
                    connection_id=None,
                    provider_id=EDFI_CONNECTION_PROVIDER_ID,
                    connection_type_id=EDFI_CONNECTION_TYPE_ID,
                    connection_metadata=_edfi_connection_metadata(
                        esu_creds.auth_url, esu_creds.resources_url, esu_creds.key, esu_creds.secret
                    ),
                )
            )
            if not test_result.is_successful:
                raise ConnectionTestFailedError(
                    f"ESU destination connection test failed. Result code: {test_result.connection_result_code}"
                )

            existing = await district_client.search_datasync_connections(dest_name)
            if existing.has_elements():
                state.destination_connection_id = existing.data[0].connection_id
                logger.info("Reusing existing destination connection '%s'.", state.destination_connection_id)
            else:
                created = await district_client.create_datasync_connection(
                    CreateEdFiAdminConnectionRequest(
                        tenant_id=tenant_id,
                        name=dest_name,
                        provider_id=EDFI_CONNECTION_PROVIDER_ID,
                        connection_type_id=EDFI_CONNECTION_TYPE_ID,
                        connection_metadata=_edfi_connection_metadata(
                            esu_creds.auth_url, esu_creds.resources_url, esu_creds.key, esu_creds.secret
                        ),
                    )
                )
                state.destination_connection_id = created.connection_id
            state.save(state_path)

        if not state.job_id:
            job = await district_client.create_datasync_job(
                DataSyncCreateJobRequest(
                    name=f"District to {esu_name} Obfuscated Sync ({school_year})",
                    job_type_id=DATASYNC_JOB_TYPE_ID,
                    source_connection_id=state.source_connection_id,
                    destination_connection_id=state.destination_connection_id,
                    profile_id=DATASYNC_PROFILE_ID,
                    schedule=DataSyncCreateJobScheduleRequest(
                        enabled=False,
                        begin_date=datetime.date.today().isoformat(),
                        cron="0 23 * * *",
                        time_zone=SCHEDULE_TIMEZONE,
                    ),
                )
            )
            state.job_id = job.job_id
            state.save(state_path)
            logger.info("Created Data Sync job '%s'.", job.job_id)

        logger.info("obfuscated_sync_to_esu completed. State saved to '%s'.", state_path)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
