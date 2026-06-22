from ed_fi.accessible_resources import BlacklistEntry, get_accessible_resources

NDE_BLACKLIST: list[BlacklistEntry] = []

_RESOURCES_PATH_SUFFIX = "/data/v3"


def _edfi_base_url(resources_url: str) -> str:
    stripped: str = resources_url.rstrip("/")
    base: str = stripped.removesuffix(_RESOURCES_PATH_SUFFIX)
    if base == stripped:
        raise ValueError(
            f"resources_url '{resources_url}' does not end with '{_RESOURCES_PATH_SUFFIX}'; "
            "cannot derive the Ed-Fi base URL."
        )
    return base


async def get_entities_value(resources_url: str, client_id: str, client_secret: str) -> str:
    base_url: str = _edfi_base_url(resources_url)
    return await get_accessible_resources(base_url, client_id, client_secret, blacklist=NDE_BLACKLIST)
