# LID Planning: Security Hardening Around Admin-Only Tools

## Landscape
Odysseus is a powerful self-hosted workspace that provides deep integrations with the host machine. As noted in the `SECURITY.md` and `README.md`, it includes highly privileged tools such as shell access, file execution, MCP (Model Context Protocol) management, and web research capabilities. While these are gated behind admin accounts, the risks associated with these tools might not be immediately obvious to all users, especially those exposing their instances via Tailscale or reverse proxies. The roadmap calls for security hardening around these admin-only tools and clearer documentation of their risks.

## Initiative
This initiative aims to systematically review and harden the security boundaries protecting admin tools, and to ensure users are explicitly aware of the power they are granting to the system and themselves. We will audit the API endpoints to ensure strict Role-Based Access Control (RBAC) is applied. We will also add explicit, unavoidable warnings in the UI and documentation regarding the "root-like" capabilities of the admin account, particularly concerning shell and file access tools.

## Deliverable
- **Security Audit & Code Patches**: A comprehensive review of all routes in `routes/` that trigger shell commands, file modifications, or MCP integrations. We will ensure that every such route strictly enforces the `is_admin` requirement.
- **Tool Execution Logging**: Implement strict audit logging for any use of admin-only agent tools (e.g., logging every shell command executed by the agent to a secure, separate audit log).
- **UI Risk Disclosures**: Introduce explicit warning banners in the Admin Settings (particularly under integrations, API tokens, and agent tool selection) explaining that enabling shell/file access gives the agent/user equivalent privileges to the host user running the app.
- **Documentation Updates**: Expand the `SECURITY.md` and `README.md` to detail the specific blast radius of a compromised admin account and provide a "Hardening Guide" for users wanting to run Odysseus with least-privilege principles (e.g., using strictly unprivileged Docker containers, read-only mounts where possible).
