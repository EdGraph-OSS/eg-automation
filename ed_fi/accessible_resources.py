"""
Fetch the Ed-Fi resources accessible to a given API client.

Flow:
  1. GET  <base_url>           — discover version, dependencies URL, OAuth URL,
                                  and token-introspection URL.
  2. GET  <dependencies_url>   — fetch ordered dependency list (no auth needed).
  3. Apply blacklist            — remove any dependency that matches an entry.
  4. Version / credential check — if ODS < 7.3, or no credentials supplied, or
                                  the OAuth/introspection URLs are absent, return
                                  all remaining (blacklisted-filtered) dependencies.
  5. POST <oauth_url>           — exchange client credentials for a bearer token.
  6. POST <token_info_url>      — introspect the token to discover which resources
                                  the client can access.
  7. Filter (auth)              — keep only dependencies whose resource path appears
                                  in the token-info "resources" array.
  8. Return semicolon-separated names, e.g. "ed-fi/students;ed-fi/schools;"

BlacklistEntry matches using case-insensitive substring checks against the
module and/or resource segment of the path (/<module>/<resource>).  Both fields
are optional; omitting a field means "match any value for that segment".
All specified fields are ANDed within an entry; entries are ORed across the list.
"""

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class BlacklistEntry:
    module: str | None = None
    resource: str | None = None

    def matches(self, resource_path: str) -> bool:
        parts: list[str] = resource_path.strip("/").split(sep="/", maxsplit=1)
        path_module: str = parts[0].lower() if len(parts) > 0 else ""
        path_resource: str = parts[1].lower() if len(parts) > 1 else ""
        if self.module is not None and self.module.lower() not in path_module:
            return False
        if self.resource is not None and self.resource.lower() not in path_resource:
            return False
        return True


@dataclass
class Dependency:
    resource: str
    order: int
    operations: list[Any] = field(default_factory=list)


def _version_gte(version_str: str, major: int, minor: int) -> bool:
    try:
        parts = tuple(int(p) for p in version_str.split("."))
    except (ValueError, AttributeError):
        parts = (0,)
    return parts >= (major, minor)


def _apply_blacklist(deps: list[Dependency], blacklist: list[BlacklistEntry]) -> list[Dependency]:
    if not blacklist:
        return deps
    return [d for d in deps if not any(entry.matches(d.resource) for entry in blacklist)]


def _to_entities_value(deps: list[Dependency]) -> str:
    return ";".join(d.resource.strip("/") for d in deps) + ";"


async def get_accessible_resources(
    base_url: str,
    client_id: str = "",
    client_secret: str = "",
    blacklist: list[BlacklistEntry] | None = None,
) -> str:
    """
    Returns a semicolon-separated string of accessible Ed-Fi resource paths
    for use as the DataSync job ``entities`` metadata value.

    Falls back to returning all blacklist-filtered dependencies (no auth
    filtering) when:
      - ``client_id`` / ``client_secret`` are not supplied
      - ODS version is below 7.3 (token introspection not supported)
      - The instance does not advertise OAuth or token-introspection URLs
    """
    if blacklist is None:
        blacklist: list[BlacklistEntry] = []

    use_auth = bool(client_id and client_secret)

    async with httpx.AsyncClient(timeout=30.0) as http:
        try:
            resp = await http.get(base_url.rstrip("/"))
            resp.raise_for_status()
            instance_info: dict[str, Any] = resp.json()
        except httpx.HTTPError as exc:
            raise ValueError(f"Failed to fetch instance info from '{base_url}': {exc}") from exc

        version: str = instance_info.get("version", "0.0")
        urls: dict[str, str] = instance_info.get("urls", {})
        dependencies_url: str = urls.get("dependencies", "")
        oauth_url: str = urls.get("oauth", "")
        token_info_url: str = urls.get("oauthTokenIntrospection", "")

        if not dependencies_url:
            raise ValueError(f"No dependencies URL in instance info from '{base_url}'.")

        try:
            resp = await http.get(dependencies_url)
            resp.raise_for_status()
            raw: list[Any] = resp.json()
        except httpx.HTTPError as exc:
            raise ValueError(f"Failed to fetch dependencies from '{dependencies_url}': {exc}") from exc

        dependencies: list[Dependency] = sorted(
            [Dependency(resource=d["resource"], order=d["order"]) for d in raw],
            key=lambda d: d.order,
        )
        dependencies: list[Dependency] = _apply_blacklist(dependencies, blacklist)

        supports_token_info: bool = _version_gte(version, 7, 3)

        if not use_auth or not supports_token_info or not oauth_url or not token_info_url:
            return _to_entities_value(dependencies)

        try:
            resp = await http.post(
                oauth_url,
                data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            )
            resp.raise_for_status()
            token: str = resp.json().get("access_token", "")
        except httpx.HTTPError as exc:
            raise ValueError(f"Failed to obtain access token from '{oauth_url}': {exc}") from exc

        if not token:
            raise ValueError("No access_token in OAuth response.")

        try:
            resp = await http.post(
                token_info_url,
                data={"token": token},
            )
            resp.raise_for_status()
            token_info: dict[str, Any] = resp.json()
        except httpx.HTTPError as exc:
            raise ValueError(f"Failed to fetch token info from '{token_info_url}': {exc}") from exc

    accessible: set[str] = {entry["resource"] for entry in token_info.get("resources", []) if "resource" in entry}
    if not accessible:
        raise ValueError("The API client does not have access to any resources — check credentials and claim sets.")

    filtered: list[Dependency] = [d for d in dependencies if d.resource in accessible]
    if not filtered:
        raise ValueError("No API dependencies matched the accessible resources for this client.")

    return _to_entities_value(filtered)
