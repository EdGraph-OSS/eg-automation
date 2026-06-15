from typing import Literal

from .models import EdGraphModel

EdGraphEnvironment = Literal["Dev", "QA", "Production", "Local"]


class ApiUrls(EdGraphModel):
    identity: str
    tenant: str
    management: str


ENVIRONMENT_URLS: dict[EdGraphEnvironment, ApiUrls] = {
    "Dev": ApiUrls(
        identity="https://login.dev.edgraph.com",
        tenant="https://api.dev.edgraph.com/tenant",
        management="https://api.dev.edgraph.com/management",
    ),
    "QA": ApiUrls(
        identity="https://login.qa.edgraph.com",
        tenant="https://api.qa.edgraph.com/tenant",
        management="https://api.qa.edgraph.com/management",
    ),
    "Production": ApiUrls(
        identity="https://login.edgraph.com",
        tenant="https://api.edgraph.com/tenant",
        management="https://api.edgraph.com/management",
    ),
    "Local": ApiUrls(
        identity="http://localhost:5305",
        tenant="http://localhost:5102",
        management="http://localhost:5101",
    ),
}
