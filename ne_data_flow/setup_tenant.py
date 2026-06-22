"""setup_tenant — Item #16703

Pre-configures Ed-Fi artifacts for a district tenant:
  - Provisions NE-extended Ed-Fi instance for the current school year
  - Creates vendors: NDE, ACT, and the district itself
  - Resolves claim set: "Read/Write All - No Further Auth" (must already exist in the instance)
  - Provisions claim set: "Read/Write All - District Only (Relationship-Based Auth)"
    (created automatically from Core/claim_set_configurations/district_only.json if missing)
  - Creates applications: NE SEA to District Sync, ACT to District Sync,
    District to ESU Obfuscated Sync
  - Regenerates application secrets and saves credentials to state

Prerequisite: none
State written: tenant-state.json
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from edgraph.client import EdGraphClient
from edgraph.config import EdGraphEnvironment
from edgraph.exceptions import InstanceNotProvisionedError
from edgraph.models import (
    CreateEdFiAdminApplicationRequest,
    CreateEdFiAdminInstanceRequest,
    CreateEdFiAdminInstanceSchoolYearsRequest,
    CreateEdFiAdminVendorRequest,
    EdFiAdminApplicationCreatedResponse,
    EdFiAdminConnection,
    EdFiAdminConnectionTier,
    EdFiAdminInstanceApplication,
    EdFiAdminInstanceApplicationEndpoints,
    EdFiAdminInstanceApplicationSecretRegeneratedResponse,
    EdFiAdminInstanceClaimSet,
    EdFiAdminInstanceCreatedResponse,
    EdFiAdminPlaceholderLeaCreatedResponse,
    EdFiAdminVendorCreatedResponse,
    EducationOrganizationRequest,
    OdsBackupCode,
    PaginatedResponse,
    VendorRequest,
)

from ._claim_set import ensure_district_only_claimset
from ._constants import (
    CLAIMSET_READ_WRITE_ALL_DISTRICT_ONLY,
    CLAIMSET_READ_WRITE_NO_FURTHER_AUTH,
    NE_ED_FI_DATABASE_ENGINE,
    NE_ED_FI_EXTENSION,
    NE_ED_FI_ODS_BACKUP_CODE,
    NE_ED_FI_TIER,
    NE_ED_FI_VERSION,
    OPERATIONAL_CONTEXT_URI,
)
from .models import ApplicationCredentials, TenantState

_PLACEHOLDER_LEA_PATH = Path(__file__).parent.parent / "Core" / "placeholders" / "edgraph_placeholder_lea.json"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

STATE_FILENAME = "tenant-state.json"


async def _resolve_connection(client: EdGraphClient) -> EdFiAdminConnection:
    result: PaginatedResponse[EdFiAdminConnection] = await client.get_edfi_connections(
        database_engine=NE_ED_FI_DATABASE_ENGINE
    )
    matches: list[EdFiAdminConnection] = [
        c for c in result.data if c.ed_fi_version == NE_ED_FI_VERSION and c.ed_fi_extension == NE_ED_FI_EXTENSION
    ]
    if not matches:
        raise ValueError(f"No connection found for version '{NE_ED_FI_VERSION}' and extension '{NE_ED_FI_EXTENSION}'.")
    if len(matches) > 1:
        logger.warning("Multiple matching connections found; using first: %s.", matches[0].connection_name)
    return matches[0]


async def _resolve_ods_backup_code(client: EdGraphClient) -> OdsBackupCode:
    result: PaginatedResponse[OdsBackupCode] = await client.get_ods_backup_codes()
    match: OdsBackupCode | None = next((b for b in result.data if b.code == NE_ED_FI_ODS_BACKUP_CODE), None)
    if match is None:
        raise ValueError(f"ODS backup code '{NE_ED_FI_ODS_BACKUP_CODE}' not found.")
    return match


async def _main() -> None:
    load_dotenv(override=True)
    state_path = Path(STATE_FILENAME)
    environment: EdGraphEnvironment = cast(EdGraphEnvironment, os.environ.get("EDGRAPH_ENVIRONMENT", "Dev"))
    client_id: str = os.environ["EDGRAPH_CLIENT_ID"]
    client_secret: str = os.environ["EDGRAPH_CLIENT_SECRET"]
    tenant_id: str = os.environ["TENANT_ID"]
    school_year = int(os.environ.get("SCHOOL_YEAR", "2026"))
    district_name: str = os.environ["DISTRICT_NAME"]

    state: TenantState = (
        TenantState.load(state_path)
        if state_path.exists()
        else TenantState(
            tenant_id=tenant_id,
            district_name=district_name,
            school_year=school_year,
        )
    )

    async with EdGraphClient(environment, tenant_id, client_id, client_secret) as client:
        if not state.instance_id:
            connection: EdFiAdminConnection = await _resolve_connection(client)
            tier: EdFiAdminConnectionTier = connection.get_tier(NE_ED_FI_TIER)
            backup_code: OdsBackupCode = await _resolve_ods_backup_code(client)

            if tier.tier_name is None:
                raise ValueError(f"Tier '{NE_ED_FI_TIER}' has no name on connection '{connection.connection_name}'.")

            instance_name = f"{district_name} {environment} {school_year}"
            created: EdFiAdminInstanceCreatedResponse = await client.create_edfi_instance(
                CreateEdFiAdminInstanceRequest(
                    instance_name=instance_name,
                    database_engine=NE_ED_FI_DATABASE_ENGINE,
                    selected_connection_id=connection.id,
                    selected_connection_name=connection.connection_name,
                    school_years=[
                        CreateEdFiAdminInstanceSchoolYearsRequest(
                            year=school_year,
                            selected_tier_id=tier.tier_id,
                            selected_tier_name=tier.tier_name,
                            ods_backup_code=backup_code.code,
                            ods_backup_description=backup_code.description,
                        )
                    ],
                )
            )
            state.instance_id = created.instance_id
            state.save(state_path)
            logger.info("Created instance '%s'.", created.instance_id)
        else:
            logger.info("Reusing existing instance '%s'.", state.instance_id)

        try:
            await client.poll_edfi_instance_provisioned(state.instance_id)
            logger.info("Instance '%s' is provisioned.", state.instance_id)
        except InstanceNotProvisionedError:
            logger.error("Instance '%s' did not provision within the timeout window.", state.instance_id)
            raise

        instance_id = state.instance_id

        if not state.nde_vendor_id:
            r: EdFiAdminVendorCreatedResponse = await client.create_edfi_instance_vendor(
                instance_id, CreateEdFiAdminVendorRequest(vendor_name="NDE", namespace_prefixes=[])
            )
            state.nde_vendor_id = r.vendor_id
            state.save(state_path)

        if not state.act_vendor_id:
            r: EdFiAdminVendorCreatedResponse = await client.create_edfi_instance_vendor(
                instance_id, CreateEdFiAdminVendorRequest(vendor_name="ACT", namespace_prefixes=[])
            )
            state.act_vendor_id = r.vendor_id
            state.save(state_path)

        if not state.district_vendor_id:
            r: EdFiAdminVendorCreatedResponse = await client.create_edfi_instance_vendor(
                instance_id,
                CreateEdFiAdminVendorRequest(vendor_name=district_name, namespace_prefixes=[]),
            )
            state.district_vendor_id = r.vendor_id
            state.save(state_path)

        rw_no_auth: EdFiAdminInstanceClaimSet = await client.find_claimset_by_name(
            instance_id, CLAIMSET_READ_WRITE_NO_FURTHER_AUTH
        )
        rw_district_only: EdFiAdminInstanceClaimSet = await ensure_district_only_claimset(
            client, instance_id, CLAIMSET_READ_WRITE_ALL_DISTRICT_ONLY
        )

        if not state.placeholder_lea_id:
            with open(_PLACEHOLDER_LEA_PATH, encoding="utf-8") as f:
                placeholder_lea_data: Any = json.load(f)
            lea_body: Any = placeholder_lea_data["localEducationAgency"]
            lea: EdFiAdminPlaceholderLeaCreatedResponse = await client.create_placeholder_lea(
                instance_id, school_year, lea_body
            )
            state.placeholder_lea_id = lea.id
            state.placeholder_lea_education_organization_id = lea_body["localEducationAgencyId"]
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

        if not state.sea_sync_application_id:
            app: EdFiAdminApplicationCreatedResponse = await client.create_edfi_instance_application(
                instance_id,
                CreateEdFiAdminApplicationRequest(
                    application_name="NE SEA to District Sync",
                    claim_set_name=rw_no_auth.claim_set_name,
                    operational_context_uri=OPERATIONAL_CONTEXT_URI,
                    vendor_id=state.nde_vendor_id,
                    vendor=VendorRequest(namespace_prefixes=[]),
                    education_organizations=[placeholder_lea],
                ),
            )
            state.sea_sync_application_id = app.application_id
            state.save(state_path)

        if not state.act_sync_application_id:
            app: EdFiAdminApplicationCreatedResponse = await client.create_edfi_instance_application(
                instance_id,
                CreateEdFiAdminApplicationRequest(
                    application_name="ACT to District Sync",
                    claim_set_name=rw_no_auth.claim_set_name,
                    operational_context_uri=OPERATIONAL_CONTEXT_URI,
                    vendor_id=state.act_vendor_id,
                    vendor=VendorRequest(namespace_prefixes=[]),
                    education_organizations=[placeholder_lea],
                ),
            )
            state.act_sync_application_id = app.application_id
            state.save(state_path)

        if not state.esu_sync_application_id:
            app = await client.create_edfi_instance_application(
                instance_id,
                CreateEdFiAdminApplicationRequest(
                    application_name="District to ESU Obfuscated Sync",
                    claim_set_name=rw_district_only.claim_set_name,
                    operational_context_uri=OPERATIONAL_CONTEXT_URI,
                    vendor_id=state.district_vendor_id,
                    vendor=VendorRequest(namespace_prefixes=[]),
                    education_organizations=[placeholder_lea],
                ),
            )
            state.esu_sync_application_id = app.application_id
            state.save(state_path)

        for app_id, cred_attr in (
            (state.sea_sync_application_id, "sea_sync_credentials"),
            (state.act_sync_application_id, "act_sync_credentials"),
            (state.esu_sync_application_id, "esu_sync_credentials"),
        ):
            if getattr(state, cred_attr) is not None:
                continue

            api_client: EdFiAdminInstanceApplication = await client.get_edfi_instance_application(instance_id, app_id)
            secret_resp: EdFiAdminInstanceApplicationSecretRegeneratedResponse = (
                await client.regenerate_application_secret(instance_id, app_id, api_client.api_client_id)
            )
            endpoints: EdFiAdminInstanceApplicationEndpoints = await client.get_edfi_instance_endpoints(
                instance_id, school_year
            )
            creds = ApplicationCredentials(
                auth_url=endpoints.auth_url,
                resources_url=endpoints.get_url("Resource", "Primary (Read-Write)"),
                key=api_client.key,
                secret=secret_resp.new_secret,
            )
            setattr(state, cred_attr, creds)
            state.save(state_path)
            logger.info("Saved credentials for application %s.", app_id)

        logger.info("setup_tenant completed. State saved to '%s'.", state_path)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
