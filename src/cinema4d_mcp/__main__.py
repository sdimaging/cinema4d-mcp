"""Allow `python -m cinema4d_mcp` to start the MCP server.

The bin/cinema4d-mcp-wrapper script invokes `$PYTHON_EXEC -m cinema4d_mcp`.
Without this file Python errors with "No module named cinema4d_mcp.__main__".
"""

from . import main

if __name__ == "__main__":
    main()
