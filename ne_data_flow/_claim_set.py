import json
import logging
from pathlib import Path
from typing import Any

from edgraph.client import EdGraphClient
from edgraph.exceptions import ClaimSetNotFoundError
from edgraph.models import EdFiAdminInstanceClaimSet

logger = logging.getLogger(__name__)

_DISTRICT_ONLY_CONFIG = (
    Path(__file__).parent.parent / "Core" / "claim_set_configurations" / "district_only.json"
)


def _merge_resource_claims(instance_claims: list[Any], config_claims: list[Any]) -> list[Any]:
    config_by_name = {c["name"]: c for c in config_claims}
    result = []
    for claim in instance_claims:
        name = claim.get("name")
        config = config_by_name.get(name, {})
        merged: dict = {
            "resourceClaimId": claim.get("resourceClaimId"),
            "name": name,
            "create": config.get("create", False),
            "read": config.get("read", False),
            "update": config.get("update", False),
            "delete": config.get("delete", False),
            "readChanges": config.get("readChanges", False),
        }
        for key in ("createAuthStrategy", "readAuthStrategy", "updateAuthStrategy", "deleteAuthStrategy"):
            if key in config:
                merged[key] = config[key]
        merged["children"] = _merge_resource_claims(
            claim.get("children") or [],
            config.get("children") or [],
        )
        result.append(merged)
    return result


def ensure_district_only_claimset(
    client: EdGraphClient,
    instance_id: str,
    claimset_name: str,
) -> EdFiAdminInstanceClaimSet:
    """Returns the district-only claim set, creating it from the bundled config if it doesn't exist."""
    try:
        return client.find_claimset_by_name(instance_id, claimset_name)
    except ClaimSetNotFoundError:
        logger.info("Claim set '%s' not found; creating it.", claimset_name)

    with open(_DISTRICT_ONLY_CONFIG, encoding="utf-8") as f:
        config = json.load(f)

    instance_claims = client.get_instance_resource_claims(instance_id)
    merged = _merge_resource_claims(instance_claims, config.get("resourceClaims") or [])
    created = client.create_edfi_instance_claimset(instance_id, claimset_name, merged)
    return EdFiAdminInstanceClaimSet(
        claim_set_id=created.claim_set_id,
        claim_set_name=claimset_name,
        is_system_reserved=False,
        applications_count=0,
    )
