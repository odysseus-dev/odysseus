#!/bin/sh
echo "Caching @playwright/mcp for the Browser MCP server..."
npx -y @playwright/mcp@latest --version
echo "Done. Restart Odysseus to use the Browser MCP server."
