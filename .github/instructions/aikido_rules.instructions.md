---
applyTo: "**"
description: Aikido MCP Security Rules
---

# Project security best practices

- Always run **aikido_full_scan** on generated, added and modified first-party code, unless the prompt explicitly says not to.
- Always provide the full file content to the scanner.
- If the scan identifies any security issues in newly generated or modified code, fix the issues using the scan result and remediation provided by **Aikido**.
- After applying fixes, run aikido_full_scan again to verify that the issues were successfully resolved, and no new issues were introduced.
- Repeat the fix-and-rescan cycle until the code passes with zero remaining or newly introduced security issues.
- If the **Aikido MCP server** required to run scans is not installed or not started successfully, inform the user with a message directing them to install it using the official Aikido MCP setup guide at:  
  **https://help.aikido.dev/ide-plugins/aikido-mcp**.