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
import json
import logging
import os
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from edgraph.client import EdGraphClient
from edgraph.config import EdGraphEnvironment
from edgraph.exceptions import ClaimSetNotFoundError, ConnectionTestFailedError
from edgraph.models import (
    ConnectionMetadataField,
    CreateEdFiAdminApplicationRequest,
    CreateEdFiAdminConnectionRequest,
    DataSyncConnection,
    DataSyncCreateJobMetadataRequest,
    DataSyncCreateJobRequest,
    DataSyncCreateJobScheduleRequest,
    DataSyncJobCreatedResponse,
    EdFiAdminApplicationCreatedResponse,
    EdFiAdminConnectionCreatedResponse,
    EdFiAdminConnectionTestedResponse,
    EdFiAdminInstanceApplication,
    EdFiAdminInstanceApplicationEndpoints,
    EdFiAdminInstanceApplicationSecretRegeneratedResponse,
    EdFiAdminInstanceClaimSet,
    EdFiAdminPlaceholderLeaCreatedResponse,
    EdFiAdminTestConnectionRequest,
    EducationOrganizationRequest,
    PaginatedResponse,
    VendorRequest,
)

from ._constants import (
    CLAIMSET_READ_WRITE_ALL_DISTRICT_ONLY,
    DATASYNC_JOB_TYPE_NAME,
    EDFI_CONNECTION_PROVIDER_ID,
    EDFI_CONNECTION_TYPE_ID,
    OPERATIONAL_CONTEXT_URI,
    SCHEDULE_TIMEZONE,
)
from ._edfi_resources import get_entities_value
from .models import ApplicationCredentials, EsuState, EsuSyncState, TenantState

_PLACEHOLDER_LEA_PATH = Path(__file__).parent.parent / "Core" / "placeholders" / "edgraph_placeholder_lea.json"

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
    esu_client_id: str = os.environ["ESU_EDGRAPH_CLIENT_ID"]
    esu_client_secret: str = os.environ["ESU_EDGRAPH_CLIENT_SECRET"]
    tenant_id: str = os.environ["TENANT_ID"]
    district_name: str = os.environ["DISTRICT_NAME"]
    esu_tenant_id: str = os.environ["ESU_TENANT_ID"]

    tenant_state: TenantState = TenantState.load(tenant_state_path)
    esu_state: EsuState = EsuState.load(esu_state_path)
    state: EsuSyncState = EsuSyncState.load(state_path) if state_path.exists() else EsuSyncState()
    school_year: int = tenant_state.school_year
    esu_name: str = esu_state.esu_name

    if tenant_state.esu_sync_credentials is None:
        raise ValueError(
            "ESU sync application credentials are missing from tenant-state.json. Re-run setup_tenant to populate them."
        )
    if esu_state.instance_id is None or esu_state.vendor_id is None:
        raise ValueError("ESU instance or vendor ID is missing from esu-state.json. Re-run setup_esu to populate them.")

    async with (
        EdGraphClient(environment, tenant_id, client_id, client_secret) as district_client,
        EdGraphClient(environment, esu_tenant_id, esu_client_id, esu_client_secret) as esu_client,
    ):
        if not state.placeholder_lea_id:
            with open(_PLACEHOLDER_LEA_PATH, encoding="utf-8") as f:
                placeholder_lea_data: Any = json.load(f)
            lea_body: Any = placeholder_lea_data.get("localEducationAgency")
            if lea_body is None:
                raise ValueError(f"Key 'localEducationAgency' not found in '{_PLACEHOLDER_LEA_PATH}'.")
            lea_education_organization_id: int | None = lea_body.get("localEducationAgencyId")
            if lea_education_organization_id is None:
                raise ValueError(f"Key 'localEducationAgencyId' not found in '{_PLACEHOLDER_LEA_PATH}'.")
            lea_name: str | None = lea_body.get("nameOfInstitution")
            if lea_name is None:
                raise ValueError(f"Key 'nameOfInstitution' not found in '{_PLACEHOLDER_LEA_PATH}'.")

            existing_leas: PaginatedResponse[
                EdFiAdminPlaceholderLeaCreatedResponse
            ] = await esu_client.search_local_education_agencies(esu_state.instance_id, esu_state.school_year, lea_name)
            existing_lea = next(
                (lea for lea in existing_leas.data if lea.education_organization_id == lea_education_organization_id),
                None,
            )
            if existing_lea is not None:
                state.placeholder_lea_id = existing_lea.id
                state.placeholder_lea_education_organization_id = existing_lea.education_organization_id
                state.save(state_path)
                logger.info(
                    "Reusing existing placeholder LEA '%s' (educationOrganizationId=%s).",
                    existing_lea.id,
                    existing_lea.education_organization_id,
                )
            else:
                lea: EdFiAdminPlaceholderLeaCreatedResponse = await esu_client.create_placeholder_lea(
                    esu_state.instance_id, esu_state.school_year, lea_body
                )
                state.placeholder_lea_id = lea.id
                state.placeholder_lea_education_organization_id = lea_education_organization_id
                state.save(state_path)
                logger.info(
                    "Created placeholder LEA '%s' (educationOrganizationId=%s).",
                    lea.id,
                    state.placeholder_lea_education_organization_id,
                )
        else:
            logger.info("Reusing existing placeholder LEA '%s'.", state.placeholder_lea_id)

        if state.placeholder_lea_id is None or state.placeholder_lea_education_organization_id is None:
            raise RuntimeError("Placeholder LEA state is incomplete; re-run with a clean state file.")

        placeholder_lea = EducationOrganizationRequest(
            id=state.placeholder_lea_id,
            education_organization_id=state.placeholder_lea_education_organization_id,
            addresses=[],
        )

        if not state.esu_application_id:
            try:
                claimset: EdFiAdminInstanceClaimSet = await esu_client.find_claimset_by_name(
                    esu_state.instance_id, claimset_name=CLAIMSET_READ_WRITE_ALL_DISTRICT_ONLY
                )
            except ClaimSetNotFoundError as exc:
                logger.error("%s\nCreate the missing claim set manually in EdGraph before re-running.", exc)
                raise

            app: EdFiAdminApplicationCreatedResponse = await esu_client.create_edfi_instance_application(
                esu_state.instance_id,
                CreateEdFiAdminApplicationRequest(
                    application_name=f"{district_name} - ESU Obfuscated Sync",
                    claim_set_name=claimset.claim_set_name,
                    operational_context_uri=OPERATIONAL_CONTEXT_URI,
                    vendor_id=esu_state.vendor_id,
                    vendor=VendorRequest(namespace_prefixes=[]),
                    education_organizations=[placeholder_lea],
                ),
            )
            state.esu_application_id = app.application_id
            state.save(state_path)

        if not state.esu_sync_credentials:
            esu_api_client: EdFiAdminInstanceApplication = await esu_client.get_edfi_instance_application(
                esu_state.instance_id, state.esu_application_id
            )
            secret_resp: EdFiAdminInstanceApplicationSecretRegeneratedResponse = (
                await esu_client.regenerate_application_secret(
                    esu_state.instance_id, state.esu_application_id, esu_api_client.api_client_id
                )
            )
            esu_endpoints: EdFiAdminInstanceApplicationEndpoints = await esu_client.get_edfi_instance_endpoints(
                esu_state.instance_id, school_year
            )
            state.esu_sync_credentials = ApplicationCredentials(
                auth_url=esu_endpoints.auth_url,
                resources_url=esu_endpoints.get_url(url_type="Resource", access_type="Primary (Read-Write)"),
                key=esu_api_client.key,
                secret=secret_resp.new_secret,
            )
            state.save(state_path)

        source_name = f"Ed-Fi {school_year} (Source)"
        esu_creds_district: ApplicationCredentials = tenant_state.esu_sync_credentials
        if not state.source_connection_id:
            existing: PaginatedResponse[DataSyncConnection] = await district_client.search_datasync_connections(
                source_name
            )
            if existing.has_elements():
                state.source_connection_id = existing.data[0].connection_id
                logger.info("Reusing existing source connection '%s'.", state.source_connection_id)
            else:
                created: EdFiAdminConnectionCreatedResponse = await district_client.create_datasync_connection(
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
        esu_creds: ApplicationCredentials = state.esu_sync_credentials
        if not state.destination_connection_id:
            test_result: EdFiAdminConnectionTestedResponse = await district_client.test_datasync_connection(
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

            existing: PaginatedResponse[DataSyncConnection] = await district_client.search_datasync_connections(
                dest_name
            )
            if existing.has_elements():
                state.destination_connection_id = existing.data[0].connection_id
                logger.info("Reusing existing destination connection '%s'.", state.destination_connection_id)
            else:
                created: EdFiAdminConnectionCreatedResponse = await district_client.create_datasync_connection(
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
            job_type_id, profile_id = await district_client.get_datasync_job_type(DATASYNC_JOB_TYPE_NAME)
            entities: str = await get_entities_value(esu_creds.resources_url, esu_creds.key, esu_creds.secret)
            logger.info("Resolved %d entities for job metadata.", sum(1 for e in entities.split(";") if e))
            job: DataSyncJobCreatedResponse = await district_client.create_datasync_job(
                DataSyncCreateJobRequest(
                    name=f"District to {esu_name} Obfuscated Sync ({school_year})",
                    job_type_id=job_type_id,
                    source_connection_id=state.source_connection_id,
                    destination_connection_id=state.destination_connection_id,
                    profile_id=profile_id,
                    schedule=DataSyncCreateJobScheduleRequest(
                        enabled=True,
                        begin_date=datetime.date.today().isoformat(),
                        cron="0 0 23 * * ?",
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
                        DataSyncCreateJobMetadataRequest(code="ObfuscateDocumentId", value="true"),
                    ],
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
