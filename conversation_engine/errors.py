class EngineError(Exception):
    """Base class for bounded engine errors."""


class ConfigurationError(EngineError):
    pass


class VaultIdentityError(EngineError):
    pass


class MigrationError(EngineError):
    pass


class ContractError(EngineError):
    pass


class ItemFailure(EngineError):
    """A bounded item failure that requires complete quarantine."""

    def __init__(
        self,
        message: str,
        *,
        message_id: str | None = None,
        source_path: str | None = None,
        stage: str | None = None,
    ):
        super().__init__(message)
        self.message_id = message_id
        self.source_path = source_path
        self.stage = stage


class SystemFailure(RuntimeError):
    """An infrastructure failure after which the service must stop."""
