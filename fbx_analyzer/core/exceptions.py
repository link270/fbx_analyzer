"""Project-specific exception types."""

class FBXSDKNotAvailableError(ImportError):
    """Raised when the Autodesk FBX SDK Python bindings are missing."""


class FBXLoadError(RuntimeError):
    """Raised when a scene fails to load."""


class FBXSaveError(RuntimeError):
    """Raised when a scene fails to save."""
