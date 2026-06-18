"""setup_esu — Item #16708

Pre-configures Ed-Fi artifacts for an ESU tenant:
  - Provisions NE-extended Ed-Fi instance for the current school year
  - Creates vendor: the ESU itself
  - Provisions claim set: "Read/Write All - District Only (Relationship-Based Auth)"
    (created automatically from Core/claim_set_configurations/district_only.json if missing)

Prerequisite: none
State written: esu-state.json
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from edgraph.client import EdGraphClient
from edgraph.config import EdGraphEnvironment
from edgraph.exceptions import InstanceNotProvisionedError
from edgraph.models import (
    CreateEdFiAdminInstanceRequest,
    CreateEdFiAdminInstanceSchoolYearsRequest,
    CreateEdFiAdminVendorRequest,
    EdFiAdminConnection,
    EdFiAdminConnectionTier,
    EdFiAdminInstanceCreatedResponse,
    EdFiAdminVendorCreatedResponse,
    OdsBackupCode,
    PaginatedResponse,
)

from ._claim_set import ensure_district_only_claimset
from ._constants import (
    CLAIMSET_READ_WRITE_ALL_DISTRICT_ONLY,
    NE_ED_FI_DATABASE_ENGINE,
    NE_ED_FI_EXTENSION,
    NE_ED_FI_ODS_BACKUP_CODE,
    NE_ED_FI_TIER,
    NE_ED_FI_VERSION,
)
from .models import EsuState

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    level=logging.DEBUG,
)
logger: logging.Logger = logging.getLogger(__name__)

STATE_FILENAME = "esu-state.json"


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
    load_dotenv()
    state_path = Path(STATE_FILENAME)
    environment: EdGraphEnvironment = cast(EdGraphEnvironment, os.environ.get("EDGRAPH_ENVIRONMENT", "Dev"))
    client_id: str = os.environ["EDGRAPH_CLIENT_ID"]
    client_secret: str = os.environ["EDGRAPH_CLIENT_SECRET"]
    esu_tenant_id: str = os.environ["ESU_TENANT_ID"]
    school_year = int(os.environ.get("SCHOOL_YEAR", "2026"))
    esu_name: str = os.environ["ESU_NAME"]

    state: EsuState = (
        EsuState.load(state_path)
        if state_path.exists()
        else EsuState(
            esu_tenant_id=esu_tenant_id,
            esu_name=esu_name,
            school_year=school_year,
        )
    )

    async with EdGraphClient(environment, esu_tenant_id, client_id, client_secret) as client:
        if not state.instance_id:
            connection: EdFiAdminConnection = await _resolve_connection(client)
            tier: EdFiAdminConnectionTier = connection.get_tier(NE_ED_FI_TIER)
            backup_code: OdsBackupCode = await _resolve_ods_backup_code(client)

            if tier.tier_name is None:
                raise ValueError(f"Tier '{NE_ED_FI_TIER}' has no name on connection '{connection.connection_name}'.")

            instance_name = f"{esu_name} {environment} {school_year}"
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

        instance_id: str = state.instance_id

        if not state.vendor_id:
            r: EdFiAdminVendorCreatedResponse = await client.create_edfi_instance_vendor(
                instance_id,
                CreateEdFiAdminVendorRequest(vendor_name=esu_name, namespace_prefixes=[]),
            )
            state.vendor_id = r.vendor_id
            state.save(state_path)

        await ensure_district_only_claimset(client, instance_id, CLAIMSET_READ_WRITE_ALL_DISTRICT_ONLY)

        logger.info("setup_esu completed. State saved to '%s'.", state_path)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
