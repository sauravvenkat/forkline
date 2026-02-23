"""
Forkline version constants.

This module defines version constants for the Forkline library and its
artifact schemas. These are used to track compatibility and enable
backward-compatible loading of older artifacts.
"""

# Library version (matches pyproject.toml)
FORKLINE_VERSION = "0.3.0"

# Schema version for recording artifacts
# Increment when the artifact format changes in a breaking way
SCHEMA_VERSION = "recording_v0"

# Default values for backward compatibility when loading older artifacts
DEFAULT_FORKLINE_VERSION = "0.1.0"
DEFAULT_SCHEMA_VERSION = "recording_v0"
