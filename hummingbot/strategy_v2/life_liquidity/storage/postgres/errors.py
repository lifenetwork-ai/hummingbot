"""Safe migration errors shared by the module CLI and application startup."""


class MigrationError(ValueError):
    """Invalid configuration, migration input or incompatible applied history."""
