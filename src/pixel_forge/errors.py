"""Exception hierarchy. Every failure the toolkit raises deliberately lands here."""


class ForgeError(Exception):
    """Base class for all pixel-forge errors."""


class SchemaError(ForgeError):
    """A source document is malformed, or its schema_version is unsupported."""


class PathSecurityError(ForgeError):
    """A path escaped the project root."""


class AssetNotFoundError(ForgeError):
    """No asset with the requested id exists in the project."""


class PaletteError(ForgeError):
    """A colour was referenced that the palette does not define."""


class RenderError(ForgeError):
    """A frame could not be rendered from its specification."""


class OperationError(ForgeError):
    """A revision operation is invalid or would violate a protection rule."""


class ExportError(ForgeError):
    """An export manifest could not be produced."""
