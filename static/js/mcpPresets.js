// static/js/mcpPresets.js — MCP server preset catalog
//
// Single source of truth for the "Add MCP Server" preset dropdown. Consumed by
// the live Integrations form (settings.js showMcpForm) and by the legacy admin
// form (admin.js initMcpForm). Each entry fills Name / Command / Args / Env.
//
// `help` is shown as setup instructions after a preset is picked. `oauthFile`,
// `oauth` and `providerDropdown` are extra hints the admin form used; the live
// form only needs name/command/args/env/help.

export const MCP_PRESETS = [
  { name: "Gmail",           command: "npx", args: ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],      env: { GOOGLE_CLIENT_ID: "", GOOGLE_CLIENT_SECRET: "" },
    oauthFile: { dir: "gmail", filename: "gcp-oauth.keys.json" },
    oauth: {
      provider: "google",
      keys_file: "gmail/gcp-oauth.keys.json",
      token_file: "gmail/credentials.json",
      scopes: ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.settings.basic"],
    },
    help: `Setup:
1. Go to console.cloud.google.com > create or select a project
2. APIs & Services > Library > search "Gmail API" > Enable
3. APIs & Services > OAuth consent screen > set up (External is fine)
4. Under Audience, add your Gmail address as a test user
5. APIs & Services > Credentials > + Create Credentials > OAuth Client ID
6. Application type: Desktop App > Create
7. Copy the Client ID and Client Secret into the fields above
8. Click Add Server, then click the Authorize button
9. Sign in with Google, copy the URL from the error page, paste it back` },
  { name: "Email (IMAP/SMTP)", command: "npx", args: ["-y", "@codefuturist/email-mcp", "stdio"],        env: { MCP_EMAIL_ADDRESS: "", MCP_EMAIL_PASSWORD: "", MCP_EMAIL_IMAP_HOST: "", MCP_EMAIL_SMTP_HOST: "" },
    providerDropdown: {
      label: "Provider",
      targets: { MCP_EMAIL_IMAP_HOST: "imap", MCP_EMAIL_SMTP_HOST: "smtp" },
      options: [
        { name: "Migadu",        imap: "imap.migadu.com",     smtp: "smtp.migadu.com" },
        { name: "Fastmail",      imap: "imap.fastmail.com",   smtp: "smtp.fastmail.com" },
        { name: "Proton Bridge", imap: "127.0.0.1",           smtp: "127.0.0.1" },
        { name: "Outlook/Hotmail", imap: "outlook.office365.com", smtp: "smtp.office365.com" },
        { name: "Yahoo",         imap: "imap.mail.yahoo.com", smtp: "smtp.mail.yahoo.com" },
        { name: "iCloud",        imap: "imap.mail.me.com",    smtp: "smtp.mail.me.com" },
        { name: "Zoho",          imap: "imap.zoho.com",       smtp: "smtp.zoho.com" },
        { name: "Custom",        imap: "",                    smtp: "" },
      ],
    },
    help: "Works with any IMAP/SMTP email provider.\n1. Pick your provider from the dropdown (or choose Custom)\n2. Enter your email address and password (or app password)\n3. Click Add Server" },
  { name: "CalDAV (Radicale/Nextcloud)", command: "npx", args: ["-y", "caldav-mcp"],                     env: { CALDAV_BASE_URL: "http://localhost:5232", CALDAV_USERNAME: "", CALDAV_PASSWORD: "" },
    help: "Works with any CalDAV server (Radicale, Nextcloud, etc.).\n1. Enter your CalDAV server URL (e.g. http://localhost:5232)\n2. Enter your username and password\n3. Click Add Server" },
  { name: "Google Calendar", command: "npx", args: ["-y", "@cocal/google-calendar-mcp"],                 env: { GOOGLE_OAUTH_CREDENTIALS: "" },
    help: `Setup:
1. Go to console.cloud.google.com > create/select a project
2. APIs & Services > Library > enable Google Calendar API
3. APIs & Services > Credentials > + Create Credentials > OAuth Client ID
4. Application type: Desktop App > Create
5. Click "Download JSON" on the credential you just created
6. Set Google Oauth Credentials to the full path of the downloaded JSON file` },
  { name: "Google Drive",    command: "npx", args: ["-y", "@modelcontextprotocol/server-gdrive"],        env: {},
    help: "Google Drive uses browser-based OAuth on first run. No env vars needed — just click Add and authorize when prompted." },
  { name: "GitHub",          command: "npx", args: ["-y", "@modelcontextprotocol/server-github"],        env: { GITHUB_PERSONAL_ACCESS_TOKEN: "" },
    help: "1. Go to github.com > Settings > Developer Settings > Personal Access Tokens > Fine-grained tokens\n2. Generate a new token with the repo permissions you need\n3. Paste it as Github Personal Access Token" },
  { name: "Slack",           command: "npx", args: ["-y", "@modelcontextprotocol/server-slack"],         env: { SLACK_BOT_TOKEN: "", SLACK_TEAM_ID: "" },
    help: "1. Go to api.slack.com/apps > Create New App > From Scratch\n2. Add Bot Token Scopes (channels:read, chat:write, etc.)\n3. Install to workspace, copy the Bot User OAuth Token (xoxb-...)\n4. Team ID is in your workspace URL or Slack admin settings" },
  { name: "Notion",          command: "npx", args: ["-y", "@notionhq/notion-mcp-server"],               env: { OPENAPI_MCP_HEADERS: "" },
    help: "1. Go to notion.so/my-integrations\n2. Create a new integration\n3. Copy the Internal Integration Secret\n4. Share the Notion pages/databases you want accessible with the integration\n5. For Openapi Mcp Headers enter:\n   {\"Authorization\": \"Bearer YOUR_SECRET\", \"Notion-Version\": \"2022-06-28\"}" },
  { name: "Linear",          command: "npx", args: ["-y", "mcp-linear"],                                env: { LINEAR_API_KEY: "" },
    help: "1. Go to linear.app > Settings > API\n2. Create a Personal API Key\n3. Paste it as Linear Api Key" },
  { name: "Brave Search",    command: "npx", args: ["-y", "@modelcontextprotocol/server-brave-search"], env: { BRAVE_API_KEY: "" },
    help: "1. Go to brave.com/search/api\n2. Sign up for a free plan (2000 queries/month)\n3. Copy your API key" },
  { name: "Browser (Playwright)", command: "npx", args: ["-y", "@playwright/mcp@latest", "--headless"],  env: {},
    help: "Browser automation via Playwright. The AI can navigate pages, click, fill forms, and read content.\nRuns headless by default. Remove --headless from Args to see the browser window.\nFirst run installs Chromium automatically." },
  { name: "Unbrowse (web)",  command: "npx", args: ["-y", "unbrowse@12.1.0", "mcp"],                    env: { UNBROWSE_API_KEY: "" },
    help: "Read any page as clean markdown, run tasks on sites through APIs Unbrowse learned (no local browser), and fall back to a hosted browser with saved logins.\n1. Sign up at unbrowse.ai and create an API key (unbrowse.ai/app)\n2. Paste it as Unbrowse Api Key" },
  { name: "Filesystem",      command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem", "/home"], env: {},
    help: "Edit the Args field to change which directory the server has access to." },
  { name: "Memory",          command: "npx", args: ["-y", "@modelcontextprotocol/server-memory"],        env: {} },
  { name: "Postgres",        command: "npx", args: ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost/db"], env: {},
    help: "Replace the connection string in the Args field with your actual Postgres connection URL." },
  { name: "Todoist",         command: "npx", args: ["-y", "todoist-mcp-server"],                         env: { TODOIST_API_TOKEN: "" },
    help: "1. Go to todoist.com > Settings > Integrations > Developer\n2. Copy your API token" },
];

// Turn a preset display name into a usable server name:
// "Unbrowse (web)" -> "unbrowse-web", "Email (IMAP/SMTP)" -> "email-imap-smtp".
export function presetServerName(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}
