"""mindslice-forge: turn documents into a MindSlice workspace by driving an agent harness.

The forge itself only orchestrates: it inventories the sources, runs agent sessions, keeps
resumable state and checks every result with the ``mindslice`` command. Everything that needs
understanding of the text is done by the agent, following the templates in ``prompts/``.
"""

__version__ = "0.1.0"


class ForgeError(Exception):
    """A failure the user can act on; the CLI prints it without a traceback."""
