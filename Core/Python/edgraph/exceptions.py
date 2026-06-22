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


class DataSyncConnectionNotFoundError(EdGraphError):
    def __init__(self, connection_id: str) -> None:
        super().__init__(f"DataSync connection '{connection_id}' not found. Ensure it is provisioned.")


class JobTypeNotFoundError(EdGraphError):
    def __init__(self, name: str) -> None:
        super().__init__(f"DataSync job type '{name}' not found.")


class ApplicationEndpointNotFoundError(EdGraphError):
    pass


class ConnectionTestFailedError(EdGraphError):
    pass


class ClaimSetNotFoundError(EdGraphError):
    pass
