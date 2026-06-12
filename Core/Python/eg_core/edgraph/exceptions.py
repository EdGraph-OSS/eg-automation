class EdGraphError(Exception):
    pass


class InstanceNotFoundError(EdGraphError):
    pass


class InstanceNotProvisionedError(EdGraphError):
    pass


class LocationHeaderNotFoundError(EdGraphError):
    pass


class ApplicationNotFoundError(EdGraphError):
    pass


class ApplicationEndpointNotFoundError(EdGraphError):
    pass


class ConnectionTestFailedError(EdGraphError):
    pass


class ClaimSetNotFoundError(EdGraphError):
    pass
