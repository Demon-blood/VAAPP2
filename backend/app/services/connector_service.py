from __future__ import annotations

import asyncio
import base64
import hashlib
import imaplib
import json
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text, new_token
from app.core.settings import get_settings
from app.models.entities import OAuthState, ServiceConnector

settings = get_settings()

CONNECTOR_TEMPLATES: dict[str, dict[str, Any]] = {
    "rest_api": {
        "title": "REST API",
        "category": "universal",
        "capabilities": ["read", "write", "test"],
        "fields": [
            {"key": "base_url", "label": "Base URL", "type": "url", "required": True},
            {"key": "auth_type", "label": "Authentication", "type": "choice", "choices": ["none", "bearer", "api_key", "basic"], "required": True},
            {"key": "token", "label": "Token / API key", "type": "secret", "required": False},
            {"key": "api_key_header", "label": "API key header", "type": "text", "required": False},
            {"key": "username", "label": "Username", "type": "text", "required": False},
            {"key": "password", "label": "Password", "type": "secret", "required": False},
            {"key": "default_headers", "label": "Default headers (JSON)", "type": "json", "required": False, "default": "{}"},
            {"key": "default_query", "label": "Default query parameters (JSON)", "type": "json", "required": False, "default": "{}"},
            {"key": "test_path", "label": "Test path", "type": "text", "required": False},
            {"key": "test_method", "label": "Test method", "type": "choice", "choices": ["GET", "POST"], "required": True, "default": "GET"},
            {"key": "test_body", "label": "Test request body (JSON)", "type": "json", "required": False, "default": "{}"},
        ],
        "operations": [
            {"key": "request", "label": "Send API request", "fields": [
                {"key": "method", "label": "Method", "type": "choice", "choices": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                {"key": "path", "label": "Path", "type": "text"},
                {"key": "query", "label": "Query parameters (JSON)", "type": "json", "default": "{}"},
                {"key": "headers", "label": "Additional headers (JSON)", "type": "json", "default": "{}"},
                {"key": "json", "label": "Request body (JSON)", "type": "json", "default": "{}"},
                {"key": "raw_body", "label": "Raw request body (optional)", "type": "multiline"},
                {"key": "content_type", "label": "Raw body content type", "type": "text", "default": "application/xml"},
            ]},
        ],
    },
    "webhook": {
        "title": "Webhook",
        "category": "universal",
        "capabilities": ["send", "test"],
        "fields": [
            {"key": "url", "label": "Webhook URL", "type": "url", "required": True},
            {"key": "secret_header", "label": "Secret header name", "type": "text", "required": False},
            {"key": "secret_value", "label": "Secret header value", "type": "secret", "required": False},
        ],
        "operations": [
            {"key": "send", "label": "Send webhook", "fields": [
                {"key": "payload", "label": "Payload (JSON)", "type": "json", "default": "{}", "required": True},
            ]},
        ],
    },
    "oauth2": {
        "title": "Generic OAuth 2.0 service",
        "category": "universal",
        "capabilities": ["oauth", "read", "write", "test"],
        "fields": [
            {"key": "authorization_url", "label": "Authorization URL", "type": "url", "required": True},
            {"key": "token_url", "label": "Token URL", "type": "url", "required": True},
            {"key": "client_id", "label": "Client ID", "type": "text", "required": True},
            {"key": "client_secret", "label": "Client secret", "type": "secret", "required": True},
            {"key": "scopes", "label": "Scopes separated by spaces", "type": "text", "required": False},
            {"key": "authorization_params", "label": "Additional authorization parameters (JSON)", "type": "json", "required": False, "default": "{}"},
            {"key": "token_params", "label": "Additional token parameters (JSON)", "type": "json", "required": False, "default": "{}"},
            {"key": "token_auth_method", "label": "Token authentication", "type": "choice", "choices": ["body", "basic"], "required": True, "default": "body"},
            {"key": "pkce", "label": "Use PKCE (S256)", "type": "choice", "choices": ["false", "true"], "required": True, "default": "false"},
            {"key": "test_url", "label": "Authenticated test URL", "type": "url", "required": True},
            {"key": "test_method", "label": "Test method", "type": "choice", "choices": ["GET", "POST"], "required": True, "default": "GET"},
            {"key": "test_body", "label": "Test body (JSON)", "type": "json", "required": False, "default": "{}"},
        ],
        "operations": [
            {"key": "request", "label": "Send authorized API request", "fields": [
                {"key": "method", "label": "Method", "type": "choice", "choices": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                {"key": "url", "label": "Full request URL", "type": "url", "required": True},
                {"key": "query", "label": "Query parameters (JSON)", "type": "json", "default": "{}"},
                {"key": "headers", "label": "Additional headers (JSON)", "type": "json", "default": "{}"},
                {"key": "json", "label": "Request body (JSON)", "type": "json", "default": "{}"},
            ]},
        ],
    },
    "client_credentials": {
        "title": "OAuth 2.0 machine-to-machine API",
        "category": "universal",
        "capabilities": ["read", "write", "test"],
        "fields": [
            {"key": "token_url", "label": "Token URL", "type": "url", "required": True},
            {"key": "client_id", "label": "Client ID", "type": "text", "required": True},
            {"key": "client_secret", "label": "Client secret", "type": "secret", "required": True},
            {"key": "scopes", "label": "Scopes separated by spaces", "type": "text", "required": False},
            {"key": "audience", "label": "Audience (optional)", "type": "text", "required": False},
            {"key": "token_auth_method", "label": "Token authentication", "type": "choice", "choices": ["body", "basic"], "required": True, "default": "basic"},
            {"key": "test_url", "label": "Authenticated test URL (optional)", "type": "url", "required": False},
            {"key": "test_method", "label": "Test method", "type": "choice", "choices": ["GET", "POST"], "required": True, "default": "GET"},
            {"key": "test_body", "label": "Test body (JSON)", "type": "json", "required": False, "default": "{}"},
        ],
        "operations": [
            {"key": "request", "label": "Send authorized API request", "fields": [
                {"key": "method", "label": "Method", "type": "choice", "choices": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                {"key": "url", "label": "Full request URL", "type": "url", "required": True},
                {"key": "query", "label": "Query parameters (JSON)", "type": "json", "default": "{}"},
                {"key": "headers", "label": "Additional headers (JSON)", "type": "json", "default": "{}"},
                {"key": "json", "label": "Request body (JSON)", "type": "json", "default": "{}"},
            ]},
        ],
    },
    "imap_smtp": {
        "title": "IMAP and SMTP email",
        "category": "communications",
        "capabilities": ["email_read", "email_send", "test"],
        "fields": [
            {"key": "imap_host", "label": "IMAP host", "type": "text", "required": True},
            {"key": "imap_port", "label": "IMAP port", "type": "number", "required": True, "default": "993"},
            {"key": "smtp_host", "label": "SMTP host", "type": "text", "required": True},
            {"key": "smtp_port", "label": "SMTP port", "type": "number", "required": True, "default": "465"},
            {"key": "username", "label": "Username", "type": "text", "required": True},
            {"key": "password", "label": "App password", "type": "secret", "required": True},
        ],
        "operations": [
            {"key": "unread", "label": "Load unread messages", "fields": [
                {"key": "mailbox", "label": "Mailbox", "type": "text", "default": "INBOX"},
                {"key": "limit", "label": "Maximum messages", "type": "number", "default": "20"},
            ]},
            {"key": "send", "label": "Send email", "fields": [
                {"key": "to", "label": "Recipient", "type": "text", "required": True},
                {"key": "subject", "label": "Subject", "type": "text", "required": True},
                {"key": "body", "label": "Message", "type": "multiline", "required": True},
            ]},
        ],
    },
    "webdav": {
        "title": "WebDAV documents",
        "category": "documents",
        "capabilities": ["file_read", "file_write", "test"],
        "fields": [
            {"key": "base_url", "label": "WebDAV URL", "type": "url", "required": True},
            {"key": "username", "label": "Username", "type": "text", "required": True},
            {"key": "password", "label": "Password", "type": "secret", "required": True},
        ],
        "operations": [
            {"key": "list", "label": "List folder", "fields": [{"key": "path", "label": "Folder path", "type": "text"}]},
            {"key": "upload", "label": "Upload text file", "fields": [
                {"key": "path", "label": "Remote file path", "type": "text", "required": True},
                {"key": "text", "label": "File contents", "type": "multiline", "required": True},
            ]},
        ],
    },
    "sftp": {
        "title": "SFTP files",
        "category": "documents",
        "capabilities": ["file_read", "file_write", "test"],
        "fields": [
            {"key": "host", "label": "Host", "type": "text", "required": True},
            {"key": "port", "label": "Port", "type": "number", "required": True, "default": "22"},
            {"key": "username", "label": "Username", "type": "text", "required": True},
            {"key": "password", "label": "Password", "type": "secret", "required": False},
            {"key": "private_key", "label": "Private key PEM", "type": "multiline_secret", "required": False},
            {"key": "root_path", "label": "Root path", "type": "text", "required": False, "default": "."},
        ],
        "operations": [
            {"key": "list", "label": "List folder", "fields": [{"key": "path", "label": "Folder path", "type": "text"}]},
            {"key": "upload", "label": "Upload text file", "fields": [
                {"key": "path", "label": "Remote file path", "type": "text", "required": True},
                {"key": "text", "label": "File contents", "type": "multiline", "required": True},
            ]},
        ],
    },
    "telegram_bot": {
        "title": "Telegram Bot",
        "category": "communications",
        "capabilities": ["read", "send", "test"],
        "fields": [
            {"key": "token", "label": "Bot token", "type": "secret", "required": True},
        ],
        "operations": [
            {"key": "get_updates", "label": "Load recent bot updates", "fields": [
                {"key": "limit", "label": "Maximum updates", "type": "number", "default": "20"},
                {"key": "offset", "label": "Update offset (optional)", "type": "number", "required": False},
            ]},
            {"key": "send_message", "label": "Send Telegram message", "fields": [
                {"key": "chat_id", "label": "Chat ID", "type": "text", "required": True},
                {"key": "text", "label": "Message", "type": "multiline", "required": True},
                {"key": "parse_mode", "label": "Parse mode", "type": "choice", "choices": ["none", "HTML", "MarkdownV2"], "default": "none"},
            ]},
        ],
    },
    "browserless": {
        "title": "Browser automation (Browserless)",
        "category": "websites",
        "capabilities": ["browser_read", "browser_write", "test"],
        "fields": [
            {"key": "endpoint", "label": "Browserless endpoint", "type": "url", "required": True, "default": "https://production-sfo.browserless.io"},
            {"key": "token", "label": "Browserless API token", "type": "secret", "required": True},
        ],
        "operations": [
            {"key": "content", "label": "Load rendered page content", "fields": [
                {"key": "url", "label": "Page URL", "type": "url", "required": True},
            ]},
            {"key": "function", "label": "Run browser workflow", "fields": [
                {"key": "code", "label": "Puppeteer function", "type": "multiline", "required": True},
                {"key": "context", "label": "Workflow values (JSON)", "type": "json", "default": "{}"},
            ]},
            {"key": "bql", "label": "Run BrowserQL workflow", "fields": [
                {"key": "query", "label": "BrowserQL query", "type": "multiline", "required": True},
                {"key": "variables", "label": "Variables (JSON)", "type": "json", "default": "{}"},
                {"key": "browser", "label": "Browser mode", "type": "choice", "choices": ["chromium", "chrome", "stealth"], "default": "chromium"},
            ]},
        ],
    },
    "rss": {
        "title": "RSS or Atom feed",
        "category": "monitoring",
        "capabilities": ["read", "test"],
        "fields": [{"key": "feed_url", "label": "Feed URL", "type": "url", "required": True}],
        "operations": [
            {"key": "latest", "label": "Load latest entries", "fields": [
                {"key": "limit", "label": "Maximum entries", "type": "number", "default": "20"},
            ]},
        ],
    },
}

CONNECTOR_PRESETS: list[dict[str, Any]] = [
    {"id": "microsoft-365", "title": "Microsoft 365 / Outlook / OneDrive", "description": "Mail, calendar, contacts and files through Microsoft Graph.", "connector_type": "oauth2", "category": "productivity", "setup_url": "https://entra.microsoft.com/", "defaults": {"authorization_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize", "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "scopes": "offline_access User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite Contacts.Read", "test_url": "https://graph.microsoft.com/v1.0/me", "token_auth_method": "body"}},
    {"id": "dropbox", "title": "Dropbox", "description": "Archive, retrieve and organize files.", "connector_type": "oauth2", "category": "documents", "setup_url": "https://www.dropbox.com/developers/apps", "defaults": {"authorization_url": "https://www.dropbox.com/oauth2/authorize", "token_url": "https://api.dropboxapi.com/oauth2/token", "scopes": "account_info.read files.metadata.read files.content.read files.content.write sharing.read", "authorization_params": "{\"token_access_type\":\"offline\"}", "test_url": "https://api.dropboxapi.com/2/users/get_current_account", "test_method": "POST", "token_auth_method": "basic"}},
    {"id": "slack", "title": "Slack", "description": "Read workspace data and send or automate channel actions.", "connector_type": "oauth2", "category": "communications", "setup_url": "https://api.slack.com/apps", "defaults": {"authorization_url": "https://slack.com/oauth/v2/authorize", "token_url": "https://slack.com/api/oauth.v2.access", "scopes": "channels:history channels:read chat:write files:read files:write users:read", "test_url": "https://slack.com/api/auth.test", "token_auth_method": "body"}},
    {"id": "notion", "title": "Notion", "description": "Create and update pages, databases and knowledge records.", "connector_type": "rest_api", "category": "productivity", "setup_url": "https://www.notion.so/profile/integrations", "defaults": {"base_url": "https://api.notion.com/v1", "auth_type": "bearer", "api_key_header": "Authorization", "default_headers": "{\"Notion-Version\":\"2022-06-28\"}", "test_path": "users/me"}},
    {"id": "todoist", "title": "Todoist", "description": "Create and manage personal and business tasks.", "connector_type": "rest_api", "category": "tasks", "setup_url": "https://app.todoist.com/app/settings/integrations/developer", "defaults": {"base_url": "https://api.todoist.com/rest/v2", "auth_type": "bearer", "test_path": "projects"}},
    {"id": "trello", "title": "Trello", "description": "Boards, cards, checklists and project follow-up.", "connector_type": "rest_api", "category": "tasks", "setup_url": "https://trello.com/power-ups/admin", "defaults": {"base_url": "https://api.trello.com/1", "auth_type": "none", "test_path": "members/me"}},
    {"id": "airtable", "title": "Airtable", "description": "Use Airtable bases as structured VA registers.", "connector_type": "rest_api", "category": "data", "setup_url": "https://airtable.com/create/tokens", "defaults": {"base_url": "https://api.airtable.com/v0", "auth_type": "bearer", "test_path": "meta/bases"}},
    {"id": "hubspot", "title": "HubSpot", "description": "Contacts, companies, tickets and CRM follow-up.", "connector_type": "oauth2", "category": "crm", "setup_url": "https://developers.hubspot.com/", "defaults": {"authorization_url": "https://app.hubspot.com/oauth/authorize", "token_url": "https://api.hubapi.com/oauth/v1/token", "scopes": "crm.objects.contacts.read crm.objects.contacts.write crm.objects.companies.read tickets", "test_url": "https://api.hubapi.com/crm/v3/objects/contacts?limit=1", "token_auth_method": "body"}},
    {"id": "calendly", "title": "Calendly", "description": "Scheduling links, invitees and appointment workflows.", "connector_type": "oauth2", "category": "calendar", "setup_url": "https://developer.calendly.com/", "defaults": {"authorization_url": "https://auth.calendly.com/oauth/authorize", "token_url": "https://auth.calendly.com/oauth/token", "test_url": "https://api.calendly.com/users/me", "token_auth_method": "basic"}},
    {"id": "zoom", "title": "Zoom", "description": "Meetings, recordings and scheduling administration.", "connector_type": "oauth2", "category": "communications", "setup_url": "https://marketplace.zoom.us/develop/create", "defaults": {"authorization_url": "https://zoom.us/oauth/authorize", "token_url": "https://zoom.us/oauth/token", "test_url": "https://api.zoom.us/v2/users/me", "token_auth_method": "basic"}},
    {"id": "linkedin", "title": "LinkedIn", "description": "Profile access and approved publishing workflows.", "connector_type": "oauth2", "category": "social", "setup_url": "https://www.linkedin.com/developers/apps", "defaults": {"authorization_url": "https://www.linkedin.com/oauth/v2/authorization", "token_url": "https://www.linkedin.com/oauth/v2/accessToken", "scopes": "openid profile email", "test_url": "https://api.linkedin.com/v2/userinfo", "token_auth_method": "body"}},
    {"id": "meta", "title": "Facebook / Instagram Graph", "description": "Pages, Instagram business content, messages and insights when Meta approves the required permissions.", "connector_type": "oauth2", "category": "social", "setup_url": "https://developers.facebook.com/apps/", "defaults": {"authorization_url": "https://www.facebook.com/v23.0/dialog/oauth", "token_url": "https://graph.facebook.com/v23.0/oauth/access_token", "scopes": "pages_show_list pages_read_engagement pages_manage_posts instagram_basic instagram_content_publish", "test_url": "https://graph.facebook.com/v23.0/me", "token_auth_method": "body"}},
    {"id": "whatsapp-cloud", "title": "WhatsApp Cloud API", "description": "Business messaging through Meta's official Cloud API.", "connector_type": "rest_api", "category": "communications", "setup_url": "https://developers.facebook.com/apps/", "defaults": {"base_url": "https://graph.facebook.com/v23.0", "auth_type": "bearer", "test_path": "me"}},
    {"id": "telegram-bot", "title": "Telegram Bot", "description": "Send alerts, briefings and bot-driven workflows through Telegram's official Bot API.", "connector_type": "telegram_bot", "category": "communications", "setup_url": "https://t.me/BotFather", "defaults": {}},
    {"id": "stripe", "title": "Stripe", "description": "Payments, invoices, customers and subscription administration.", "connector_type": "rest_api", "category": "payments", "setup_url": "https://dashboard.stripe.com/apikeys", "defaults": {"base_url": "https://api.stripe.com/v1", "auth_type": "bearer", "test_path": "balance"}},
    {"id": "mollie", "title": "Mollie", "description": "Belgian/EU payment and refund workflows.", "connector_type": "rest_api", "category": "payments", "setup_url": "https://my.mollie.com/dashboard/developers/api-keys", "defaults": {"base_url": "https://api.mollie.com/v2", "auth_type": "bearer", "test_path": "profiles/me"}},
    {"id": "paypal", "title": "PayPal", "description": "Business payment administration through OAuth client credentials.", "connector_type": "client_credentials", "category": "payments", "setup_url": "https://developer.paypal.com/dashboard/applications/live", "defaults": {"token_url": "https://api-m.paypal.com/v1/oauth2/token", "token_auth_method": "basic"}},
    {"id": "shopify", "title": "Shopify", "description": "Orders, customers, products and fulfillment.", "connector_type": "rest_api", "category": "commerce", "setup_url": "https://admin.shopify.com/", "defaults": {"auth_type": "api_key", "api_key_header": "X-Shopify-Access-Token", "test_path": "shop.json"}},
    {"id": "woocommerce", "title": "WooCommerce", "description": "Orders, customers, products and refunds.", "connector_type": "rest_api", "category": "commerce", "setup_url": "https://woocommerce.com/document/woocommerce-rest-api/", "defaults": {"auth_type": "basic", "test_path": "system_status"}},
    {"id": "twilio", "title": "Twilio", "description": "SMS, WhatsApp, voice and communication records.", "connector_type": "rest_api", "category": "communications", "setup_url": "https://console.twilio.com/", "defaults": {"base_url": "https://api.twilio.com/2010-04-01", "auth_type": "basic", "test_path": "Accounts.json"}},
    {"id": "pushover", "title": "Pushover", "description": "Reliable push alerts to your phone.", "connector_type": "rest_api", "category": "notifications", "setup_url": "https://pushover.net/apps/build", "defaults": {"base_url": "https://api.pushover.net/1", "auth_type": "none", "test_path": "sounds.json"}},
    {"id": "home-assistant", "title": "Home Assistant", "description": "Home devices, sensors and household automations.", "connector_type": "rest_api", "category": "home", "setup_url": "https://www.home-assistant.io/docs/authentication/", "defaults": {"auth_type": "bearer", "test_path": "config"}},
    {"id": "nextcloud", "title": "Nextcloud / ownCloud", "description": "Private files through WebDAV.", "connector_type": "webdav", "category": "documents", "setup_url": "https://docs.nextcloud.com/server/latest/user_manual/en/files/access_webdav.html", "defaults": {}},
    {"id": "asana", "title": "Asana", "description": "Projects, tasks, assignments and follow-up.", "connector_type": "oauth2", "category": "tasks", "setup_url": "https://app.asana.com/0/my-apps", "defaults": {"authorization_url": "https://app.asana.com/-/oauth_authorize", "token_url": "https://app.asana.com/-/oauth_token", "pkce": "true", "test_url": "https://app.asana.com/api/1.0/users/me", "token_auth_method": "body"}},
    {"id": "clickup", "title": "ClickUp", "description": "Tasks, workspaces, documents and time tracking.", "connector_type": "oauth2", "category": "tasks", "setup_url": "https://app.clickup.com/settings/apps", "defaults": {"authorization_url": "https://app.clickup.com/api", "token_url": "https://api.clickup.com/api/v2/oauth/token", "test_url": "https://api.clickup.com/api/v2/user", "token_auth_method": "body"}},
    {"id": "monday", "title": "monday.com", "description": "Boards, items, updates and project automations through GraphQL.", "connector_type": "rest_api", "category": "tasks", "setup_url": "https://developer.monday.com/apps", "defaults": {"base_url": "https://api.monday.com/v2", "auth_type": "bearer", "test_method": "POST", "test_body": "{\"query\":\"query { me { id name } }\"}"}},
    {"id": "gitlab", "title": "GitLab", "description": "Repositories, issues, merge requests and pipelines.", "connector_type": "oauth2", "category": "development", "setup_url": "https://gitlab.com/-/user_settings/applications", "defaults": {"authorization_url": "https://gitlab.com/oauth/authorize", "token_url": "https://gitlab.com/oauth/token", "scopes": "api read_user", "test_url": "https://gitlab.com/api/v4/user", "token_auth_method": "body"}},
    {"id": "google-sheets", "title": "Google Sheets", "description": "Read and update spreadsheets through the official Sheets API.", "connector_type": "oauth2", "category": "data", "setup_url": "https://console.cloud.google.com/apis/library/sheets.googleapis.com", "defaults": {"authorization_url": "https://accounts.google.com/o/oauth2/v2/auth", "token_url": "https://oauth2.googleapis.com/token", "scopes": "openid email https://www.googleapis.com/auth/spreadsheets", "authorization_params": "{\"access_type\":\"offline\",\"prompt\":\"consent\"}", "test_url": "https://openidconnect.googleapis.com/v1/userinfo", "token_auth_method": "body"}},
    {"id": "google-tasks", "title": "Google Tasks", "description": "Task lists and personal follow-up through Google Tasks.", "connector_type": "oauth2", "category": "tasks", "setup_url": "https://console.cloud.google.com/apis/library/tasks.googleapis.com", "defaults": {"authorization_url": "https://accounts.google.com/o/oauth2/v2/auth", "token_url": "https://oauth2.googleapis.com/token", "scopes": "openid email https://www.googleapis.com/auth/tasks", "authorization_params": "{\"access_type\":\"offline\",\"prompt\":\"consent\"}", "test_url": "https://tasks.googleapis.com/tasks/v1/users/@me/lists?maxResults=1", "token_auth_method": "body"}},
    {"id": "sendgrid", "title": "SendGrid", "description": "Transactional email, templates and delivery administration.", "connector_type": "rest_api", "category": "communications", "setup_url": "https://app.sendgrid.com/settings/api_keys", "defaults": {"base_url": "https://api.sendgrid.com/v3", "auth_type": "bearer", "test_path": "user/profile"}},
    {"id": "brevo", "title": "Brevo", "description": "Email campaigns, transactional mail, contacts and SMS.", "connector_type": "rest_api", "category": "communications", "setup_url": "https://app.brevo.com/settings/keys/api", "defaults": {"base_url": "https://api.brevo.com/v3", "auth_type": "api_key", "api_key_header": "api-key", "test_path": "account"}},
    {"id": "zapier-webhook", "title": "Zapier webhook", "description": "Trigger a Zap and reach services connected in Zapier.", "connector_type": "webhook", "category": "automation", "setup_url": "https://zapier.com/apps/webhook/integrations", "defaults": {}},
    {"id": "make-webhook", "title": "Make webhook", "description": "Trigger a Make scenario and reach services connected in Make.", "connector_type": "webhook", "category": "automation", "setup_url": "https://www.make.com/en/integrations/webhooks", "defaults": {}},
    {"id": "n8n-webhook", "title": "n8n webhook", "description": "Trigger a self-hosted or cloud n8n workflow.", "connector_type": "webhook", "category": "automation", "setup_url": "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/", "defaults": {}},
    {"id": "pipedream-webhook", "title": "Pipedream HTTP workflow", "description": "Trigger a Pipedream workflow through its HTTP endpoint.", "connector_type": "webhook", "category": "automation", "setup_url": "https://pipedream.com/docs/workflows/steps/triggers/", "defaults": {}},
    {"id": "browserless", "title": "Browserless website automation", "description": "Operate websites that expose no suitable API using a managed browser workflow.", "connector_type": "browserless", "category": "websites", "setup_url": "https://www.browserless.io/", "defaults": {"endpoint": "https://production-sfo.browserless.io"}},
]


def _connector_config(connector: ServiceConnector) -> dict[str, Any]:
    if not connector.config_json_encrypted:
        return {}
    return json.loads(decrypt_text(connector.config_json_encrypted))


def connector_public(connector: ServiceConnector) -> dict[str, Any]:
    template = CONNECTOR_TEMPLATES.get(connector.connector_type, {})
    config = _connector_config(connector)
    return {
        "id": connector.id,
        "slug": connector.slug,
        "display_name": connector.display_name,
        "category": connector.category,
        "connector_type": connector.connector_type,
        "capabilities": json.loads(connector.capabilities_json or "[]"),
        "enabled": connector.enabled,
        "status": connector.status,
        "last_error": connector.last_error,
        "last_test_at": connector.last_test_at,
        "fields": template.get("fields", []),
        "operations": template.get("operations", []),
        "oauth_callback_url": (
            f"{str(settings.public_base_url).rstrip('/')}/api/connectors/{connector.slug}/oauth/callback"
            if connector.connector_type == "oauth2"
            else ""
        ),
        "configured_fields": sorted([key for key, value in config.items() if str(value).strip()]),
        "current_values": {
            field["key"]: config.get(field["key"], field.get("default", ""))
            for field in template.get("fields", [])
            if "secret" not in str(field.get("type") or "")
            and str(config.get(field["key"], field.get("default", ""))).strip()
        },
    }


async def list_connectors(db: AsyncSession) -> list[dict[str, Any]]:
    rows = (await db.execute(select(ServiceConnector).order_by(ServiceConnector.category, ServiceConnector.display_name))).scalars().all()
    return [connector_public(row) for row in rows]


async def upsert_connector(
    db: AsyncSession,
    *,
    slug: str,
    display_name: str,
    connector_type: str,
    config: dict[str, Any],
    category: str | None = None,
) -> ServiceConnector:
    if connector_type not in CONNECTOR_TEMPLATES:
        raise ValueError(f"Unsupported connector type: {connector_type}")
    template = CONNECTOR_TEMPLATES[connector_type]
    existing = (await db.execute(select(ServiceConnector).where(ServiceConnector.slug == slug))).scalar_one_or_none()
    merged: dict[str, Any] = {}
    if existing is not None:
        merged.update(_connector_config(existing))
    for key, value in config.items():
        if value is not None and str(value).strip() != "":
            merged[key] = value
    missing = [field["key"] for field in template["fields"] if field.get("required") and not str(merged.get(field["key"], "")).strip()]
    if existing is None:
        existing = ServiceConnector(
            slug=slug,
            display_name=display_name,
            category=category or str(template["category"]),
            connector_type=connector_type,
            capabilities_json=json.dumps(template["capabilities"]),
        )
        db.add(existing)
    existing.display_name = display_name
    existing.category = category or existing.category or str(template["category"])
    existing.connector_type = connector_type
    existing.config_json_encrypted = encrypt_text(json.dumps(merged, ensure_ascii=False))
    existing.status = "not_configured" if missing else "configured"
    existing.last_error = "Missing: " + ", ".join(missing) if missing else ""
    await db.commit()
    await db.refresh(existing)
    return existing


def _json_object(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object")
    return parsed


def _http_auth(config: dict[str, Any]) -> tuple[dict[str, str], httpx.BasicAuth | None]:
    headers: dict[str, str] = {"Accept": "application/json"}
    headers.update({str(key): str(value) for key, value in _json_object(config.get("default_headers")).items()})
    basic = None
    auth_type = str(config.get("auth_type") or "none")
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {config.get('token', '')}"
    elif auth_type == "api_key":
        headers[str(config.get("api_key_header") or "X-API-Key")] = str(config.get("token") or "")
    elif auth_type == "basic":
        basic = httpx.BasicAuth(str(config.get("username") or ""), str(config.get("password") or ""))
    return headers, basic


async def test_connector(db: AsyncSession, connector: ServiceConnector) -> dict[str, Any]:
    config = _connector_config(connector)
    connector.last_test_at = datetime.utcnow()
    try:
        result = await _run_test(connector.connector_type, config)
        connector.status = "live"
        connector.last_error = ""
        await db.commit()
        return result
    except Exception as exc:
        connector.status = "error"
        connector.last_error = str(exc)[:2000]
        await db.commit()
        raise


async def _run_test(connector_type: str, config: dict[str, Any]) -> dict[str, Any]:
    if connector_type == "rest_api":
        headers, auth = _http_auth(config)
        url = str(config["base_url"]).rstrip("/") + "/" + str(config.get("test_path") or "").lstrip("/")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.request(
                str(config.get("test_method") or "GET").upper(),
                url,
                headers=headers,
                auth=auth,
                params=_json_object(config.get("default_query")),
                json=_json_object(config.get("test_body")) or None,
            )
            response.raise_for_status()
            return {"status": response.status_code, "content_type": response.headers.get("content-type", "")}
    if connector_type == "webhook":
        headers = {"Content-Type": "application/json"}
        if config.get("secret_header") and config.get("secret_value"):
            headers[str(config["secret_header"])] = str(config["secret_value"])
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(str(config["url"]), headers=headers, json={"event": "full_time_va_connection_test"})
            response.raise_for_status()
            return {"status": response.status_code}
    if connector_type == "imap_smtp":
        return await asyncio.to_thread(_test_mail, config)
    if connector_type == "webdav":
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.request(
                "PROPFIND",
                str(config["base_url"]),
                headers={"Depth": "0"},
                auth=(str(config["username"]), str(config["password"])),
            )
            if response.status_code not in {200, 207}:
                response.raise_for_status()
            return {"status": response.status_code}
    if connector_type == "sftp":
        return await asyncio.to_thread(_test_sftp, config)
    if connector_type == "rss":
        import feedparser
        feed = await asyncio.to_thread(feedparser.parse, str(config["feed_url"]))
        if getattr(feed, "bozo", False) and not getattr(feed, "entries", []):
            raise RuntimeError(str(getattr(feed, "bozo_exception", "Feed could not be parsed")))
        return {"title": str(getattr(feed.feed, "title", "")), "entries": len(getattr(feed, "entries", []))}
    if connector_type == "client_credentials":
        token = await _client_credentials_token(config)
        test_url = str(config.get("test_url") or "").strip()
        if not test_url:
            return {"status": 200, "token_received": bool(token)}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                str(config.get("test_method") or "GET").upper(),
                test_url,
                headers={"Authorization": f"Bearer {token}"},
                json=_json_object(config.get("test_body")) or None,
            )
            response.raise_for_status()
            return {"status": response.status_code}
    if connector_type == "telegram_bot":
        token = str(config["token"]).strip()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("description") or "Telegram rejected the bot token"))
            result = payload.get("result") or {}
            return {"status": response.status_code, "bot_id": result.get("id"), "username": result.get("username")}
    if connector_type == "browserless":
        endpoint = str(config["endpoint"]).rstrip("/")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{endpoint}/content",
                params={"token": str(config["token"])},
                json={"url": "https://example.com/"},
            )
            response.raise_for_status()
            return {"status": response.status_code, "bytes": len(response.content)}
    if connector_type == "oauth2":
        token = config.get("access_token")
        if not token:
            raise RuntimeError("OAuth authorization is not completed")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                str(config.get("test_method") or "GET").upper(),
                str(config["test_url"]),
                headers={"Authorization": f"Bearer {token}"},
                json=_json_object(config.get("test_body")) or None,
            )
            response.raise_for_status()
            return {"status": response.status_code}
    raise ValueError(f"Unsupported connector type: {connector_type}")


def _test_mail(config: dict[str, Any]) -> dict[str, Any]:
    context = ssl.create_default_context()
    with imaplib.IMAP4_SSL(str(config["imap_host"]), int(config["imap_port"]), ssl_context=context) as imap:
        imap.login(str(config["username"]), str(config["password"]))
        status, mailboxes = imap.list()
        if status != "OK":
            raise RuntimeError("IMAP LIST failed")
    smtp_port = int(config["smtp_port"])
    if smtp_port == 465:
        smtp = smtplib.SMTP_SSL(str(config["smtp_host"]), smtp_port, context=context, timeout=30)
    else:
        smtp = smtplib.SMTP(str(config["smtp_host"]), smtp_port, timeout=30)
        smtp.starttls(context=context)
    with smtp:
        smtp.login(str(config["username"]), str(config["password"]))
        smtp.noop()
    return {"imap_mailboxes": len(mailboxes or []), "smtp": "authenticated"}


def _test_sftp(config: dict[str, Any]) -> dict[str, Any]:
    import paramiko
    key = None
    if config.get("private_key"):
        import io
        key = paramiko.RSAKey.from_private_key(io.StringIO(str(config["private_key"])))
    transport = paramiko.Transport((str(config["host"]), int(config.get("port") or 22)))
    try:
        transport.connect(username=str(config["username"]), password=str(config.get("password") or "") or None, pkey=key)
        client = paramiko.SFTPClient.from_transport(transport)
        root = str(config.get("root_path") or ".")
        entries = client.listdir(root)
        client.close()
        return {"root": root, "entries": len(entries)}
    finally:
        transport.close()


async def generic_oauth_start(db: AsyncSession, connector: ServiceConnector, redirect_uri: str) -> str:
    if connector.connector_type != "oauth2":
        raise ValueError("Connector does not use OAuth 2.0")
    config = _connector_config(connector)
    state = new_token(24)
    state_payload: dict[str, Any] = {"redirect_uri": redirect_uri}
    pkce_enabled = str(config.get("pkce") or "false").lower() == "true"
    if pkce_enabled:
        verifier = new_token(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        state_payload["code_verifier"] = verifier
    db.add(OAuthState(state=state, provider=f"connector:{connector.id}", payload_json=json.dumps(state_payload), expires_at=datetime.utcnow() + timedelta(minutes=15)))
    await db.commit()
    params = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": config.get("scopes", ""),
        "state": state,
        **_json_object(config.get("authorization_params")),
    }
    if pkce_enabled:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    return str(config["authorization_url"]) + ("&" if "?" in str(config["authorization_url"]) else "?") + urlencode(params)


async def generic_oauth_callback(db: AsyncSession, connector: ServiceConnector, *, code: str, state: str) -> None:
    saved = (await db.execute(select(OAuthState).where(OAuthState.state == state, OAuthState.provider == f"connector:{connector.id}"))).scalar_one_or_none()
    if saved is None or saved.expires_at < datetime.utcnow():
        raise ValueError("OAuth state is invalid or expired")
    config = _connector_config(connector)
    saved_payload = json.loads(saved.payload_json)
    redirect_uri = saved_payload.get("redirect_uri")
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        **_json_object(config.get("token_params")),
    }
    if saved_payload.get("code_verifier"):
        token_data["code_verifier"] = saved_payload["code_verifier"]
    token_auth = None
    if str(config.get("token_auth_method") or "body") == "basic":
        token_auth = httpx.BasicAuth(str(config["client_id"]), str(config["client_secret"]))
    else:
        token_data.update({"client_id": config["client_id"], "client_secret": config["client_secret"]})
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            str(config["token_url"]),
            data=token_data,
            auth=token_auth,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    config["access_token"] = payload["access_token"]
    if payload.get("refresh_token"):
        config["refresh_token"] = payload["refresh_token"]
    config["token_type"] = payload.get("token_type", "Bearer")
    config["expires_in"] = payload.get("expires_in")
    config["token_acquired_at"] = datetime.utcnow().isoformat()
    connector.config_json_encrypted = encrypt_text(json.dumps(config, ensure_ascii=False))
    connector.status = "configured"
    await db.delete(saved)
    await db.commit()


async def _oauth_access_token(connector: ServiceConnector, config: dict[str, Any]) -> str:
    token = str(config.get("access_token") or "")
    if not token:
        raise RuntimeError("OAuth authorization is not completed")
    acquired = config.get("token_acquired_at")
    expires_in = int(config.get("expires_in") or 0)
    expired = False
    if acquired and expires_in:
        try:
            acquired_at = datetime.fromisoformat(str(acquired))
            expired = datetime.utcnow() >= acquired_at + timedelta(seconds=max(60, expires_in - 60))
        except ValueError:
            expired = True
    if expired and config.get("refresh_token"):
        async with httpx.AsyncClient(timeout=30) as client:
            refresh_data = {
                "grant_type": "refresh_token",
                "refresh_token": config["refresh_token"],
                **_json_object(config.get("token_params")),
            }
            refresh_auth = None
            if str(config.get("token_auth_method") or "body") == "basic":
                refresh_auth = httpx.BasicAuth(str(config["client_id"]), str(config["client_secret"]))
            else:
                refresh_data.update({"client_id": config["client_id"], "client_secret": config["client_secret"]})
            response = await client.post(
                str(config["token_url"]),
                data=refresh_data,
                auth=refresh_auth,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        token = str(payload["access_token"])
        config["access_token"] = token
        if payload.get("refresh_token"):
            config["refresh_token"] = payload["refresh_token"]
        config["expires_in"] = payload.get("expires_in", expires_in)
        config["token_acquired_at"] = datetime.utcnow().isoformat()
        connector.config_json_encrypted = encrypt_text(json.dumps(config, ensure_ascii=False))
    return token


async def _client_credentials_token(config: dict[str, Any]) -> str:
    data: dict[str, Any] = {"grant_type": "client_credentials"}
    if config.get("scopes"):
        data["scope"] = config["scopes"]
    if config.get("audience"):
        data["audience"] = config["audience"]
    auth = None
    if str(config.get("token_auth_method") or "basic") == "basic":
        auth = httpx.BasicAuth(str(config["client_id"]), str(config["client_secret"]))
    else:
        data.update({"client_id": config["client_id"], "client_secret": config["client_secret"]})
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(str(config["token_url"]), data=data, auth=auth, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise RuntimeError("Token endpoint did not return an access token")
    return token


def _bounded_response(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:100_000]
    else:
        body = response.text[:100_000]
    return {
        "status": response.status_code,
        "content_type": content_type,
        "body": body,
    }


async def execute_connector(
    db: AsyncSession,
    connector: ServiceConnector,
    operation: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    if not connector.enabled:
        raise RuntimeError("Connector is disabled")
    config = _connector_config(connector)
    kind = connector.connector_type
    if kind == "rest_api":
        if operation != "request":
            raise ValueError("REST connector supports operation=request")
        headers, auth = _http_auth(config)
        headers.update({str(k): str(v) for k, v in dict(parameters.get("headers") or {}).items()})
        base = str(config["base_url"]).rstrip("/")
        path = str(parameters.get("path") or "").lstrip("/")
        url = base + ("/" + path if path else "")
        method = str(parameters.get("method") or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Unsupported REST method")
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            query = _json_object(config.get("default_query"))
            query.update(dict(parameters.get("query") or {}))
            raw_body = parameters.get("raw_body")
            if raw_body not in (None, ""):
                headers["Content-Type"] = str(parameters.get("content_type") or "application/xml")
            response = await client.request(
                method,
                url,
                headers=headers,
                auth=auth,
                params=query,
                json=None if raw_body not in (None, "") else parameters.get("json"),
                content=str(raw_body).encode("utf-8") if raw_body not in (None, "") else None,
            )
            response.raise_for_status()
            return _bounded_response(response)
    if kind == "webhook":
        if operation != "send":
            raise ValueError("Webhook connector supports operation=send")
        headers = {"Content-Type": "application/json"}
        if config.get("secret_header") and config.get("secret_value"):
            headers[str(config["secret_header"])] = str(config["secret_value"])
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(str(config["url"]), headers=headers, json=parameters.get("payload") or {})
            response.raise_for_status()
            return _bounded_response(response)
    if kind == "oauth2":
        if operation != "request":
            raise ValueError("OAuth connector supports operation=request")
        token = await _oauth_access_token(connector, config)
        url = str(parameters.get("url") or config.get("test_url") or "")
        if not url:
            raise ValueError("OAuth request URL is required")
        method = str(parameters.get("method") or "GET").upper()
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", **{str(k): str(v) for k, v in dict(parameters.get("headers") or {}).items()}},
                params=dict(parameters.get("query") or {}),
                json=parameters.get("json"),
            )
            response.raise_for_status()
        connector.config_json_encrypted = encrypt_text(json.dumps(config, ensure_ascii=False))
        await db.commit()
        return _bounded_response(response)
    if kind == "client_credentials":
        if operation != "request":
            raise ValueError("Client-credentials connector supports operation=request")
        token = await _client_credentials_token(config)
        url = str(parameters.get("url") or config.get("test_url") or "")
        if not url:
            raise ValueError("Request URL is required")
        method = str(parameters.get("method") or "GET").upper()
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", **{str(k): str(v) for k, v in dict(parameters.get("headers") or {}).items()}},
                params=dict(parameters.get("query") or {}),
                json=parameters.get("json"),
            )
            response.raise_for_status()
        return _bounded_response(response)
    if kind == "telegram_bot":
        token = str(config["token"]).strip()
        base = f"https://api.telegram.org/bot{token}"
        async with httpx.AsyncClient(timeout=45) as client:
            if operation == "get_updates":
                query: dict[str, Any] = {"limit": max(1, min(int(parameters.get("limit") or 20), 100))}
                if parameters.get("offset") not in (None, ""):
                    query["offset"] = int(parameters["offset"])
                response = await client.get(f"{base}/getUpdates", params=query)
            elif operation == "send_message":
                body: dict[str, Any] = {
                    "chat_id": str(parameters.get("chat_id") or "").strip(),
                    "text": str(parameters.get("text") or ""),
                }
                if not body["chat_id"] or not body["text"]:
                    raise ValueError("Telegram chat ID and message are required")
                parse_mode = str(parameters.get("parse_mode") or "none")
                if parse_mode != "none":
                    body["parse_mode"] = parse_mode
                response = await client.post(f"{base}/sendMessage", json=body)
            else:
                raise ValueError("Telegram connector supports get_updates or send_message")
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("description") or "Telegram request failed"))
            return {"status": response.status_code, "body": payload.get("result")}
    if kind == "browserless":
        endpoint = str(config["endpoint"]).rstrip("/")
        token_params = {"token": str(config["token"])}
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            if operation == "content":
                response = await client.post(
                    f"{endpoint}/content",
                    params=token_params,
                    json={"url": str(parameters.get("url") or "")},
                )
            elif operation == "function":
                response = await client.post(
                    f"{endpoint}/function",
                    params=token_params,
                    json={"code": str(parameters.get("code") or ""), "context": dict(parameters.get("context") or {})},
                )
            elif operation == "bql":
                browser = str(parameters.get("browser") or "chromium")
                if browser not in {"chromium", "chrome", "stealth"}:
                    raise ValueError("Unsupported BrowserQL browser mode")
                response = await client.post(
                    f"{endpoint}/{browser}/bql",
                    params=token_params,
                    json={"query": str(parameters.get("query") or ""), "variables": dict(parameters.get("variables") or {})},
                )
            else:
                raise ValueError("Browserless connector supports content, function or bql")
            response.raise_for_status()
            return _bounded_response(response)
    if kind == "rss":
        if operation != "latest":
            raise ValueError("RSS connector supports operation=latest")
        import feedparser
        feed = await asyncio.to_thread(feedparser.parse, str(config["feed_url"]))
        limit = max(1, min(int(parameters.get("limit") or 20), 100))
        entries = []
        for entry in list(getattr(feed, "entries", []))[:limit]:
            entries.append(
                {
                    "title": str(entry.get("title") or ""),
                    "link": str(entry.get("link") or ""),
                    "published": str(entry.get("published") or entry.get("updated") or ""),
                    "summary": str(entry.get("summary") or "")[:5000],
                }
            )
        return {"feed_title": str(getattr(feed.feed, "title", "")), "entries": entries}
    if kind == "imap_smtp":
        if operation == "unread":
            return await asyncio.to_thread(_imap_unread, config, parameters)
        if operation == "send":
            return await asyncio.to_thread(_smtp_send, config, parameters)
        raise ValueError("Mail connector supports operation=unread or operation=send")
    if kind == "webdav":
        auth = (str(config["username"]), str(config["password"]))
        if operation == "list":
            target = str(config["base_url"]).rstrip("/") + "/" + str(parameters.get("path") or "").lstrip("/")
            async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
                response = await client.request("PROPFIND", target, headers={"Depth": "1"}, auth=auth)
                if response.status_code not in {200, 207}:
                    response.raise_for_status()
                return _bounded_response(response)
        if operation == "upload":
            target = str(config["base_url"]).rstrip("/") + "/" + str(parameters.get("path") or "").lstrip("/")
            raw = parameters.get("content_base64")
            content = base64.b64decode(str(raw)) if raw else str(parameters.get("text") or "").encode("utf-8")
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                response = await client.put(target, content=content, auth=auth)
                response.raise_for_status()
                return {"status": response.status_code, "bytes": len(content)}
        raise ValueError("WebDAV connector supports operation=list or operation=upload")
    if kind == "sftp":
        if operation == "list":
            return await asyncio.to_thread(_sftp_list, config, parameters)
        if operation == "upload":
            return await asyncio.to_thread(_sftp_upload, config, parameters)
        raise ValueError("SFTP connector supports operation=list or operation=upload")
    raise ValueError(f"Unsupported connector type: {kind}")


def _imap_unread(config: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    import email
    context = ssl.create_default_context()
    mailbox = str(parameters.get("mailbox") or "INBOX")
    limit = max(1, min(int(parameters.get("limit") or 20), 100))
    output: list[dict[str, str]] = []
    with imaplib.IMAP4_SSL(str(config["imap_host"]), int(config["imap_port"]), ssl_context=context) as imap:
        imap.login(str(config["username"]), str(config["password"]))
        status, _ = imap.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Could not open mailbox {mailbox}")
        status, ids = imap.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("IMAP search failed")
        message_ids = (ids[0].split() if ids and ids[0] else [])[-limit:]
        for message_id in reversed(message_ids):
            status, data = imap.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            message = email.message_from_bytes(data[0][1])
            output.append(
                {
                    "id": message_id.decode(),
                    "from": str(message.get("From") or ""),
                    "to": str(message.get("To") or ""),
                    "subject": str(message.get("Subject") or ""),
                    "date": str(message.get("Date") or ""),
                    "message_id": str(message.get("Message-ID") or ""),
                }
            )
    return {"mailbox": mailbox, "messages": output}


def _smtp_send(config: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    recipient = str(parameters.get("to") or "").strip()
    subject = str(parameters.get("subject") or "").strip()
    body = str(parameters.get("body") or "")
    if not recipient or not subject:
        raise ValueError("Recipient and subject are required")
    message = EmailMessage()
    message["From"] = str(parameters.get("from") or config["username"])
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    port = int(config["smtp_port"])
    if port == 465:
        smtp = smtplib.SMTP_SSL(str(config["smtp_host"]), port, context=context, timeout=30)
    else:
        smtp = smtplib.SMTP(str(config["smtp_host"]), port, timeout=30)
        smtp.starttls(context=context)
    with smtp:
        smtp.login(str(config["username"]), str(config["password"]))
        smtp.send_message(message)
    return {"sent": True, "to": recipient, "subject": subject}


def _open_sftp(config: dict[str, Any]):
    import io
    import paramiko
    key = None
    if config.get("private_key"):
        key = paramiko.RSAKey.from_private_key(io.StringIO(str(config["private_key"])))
    transport = paramiko.Transport((str(config["host"]), int(config.get("port") or 22)))
    transport.connect(
        username=str(config["username"]),
        password=str(config.get("password") or "") or None,
        pkey=key,
    )
    return transport, paramiko.SFTPClient.from_transport(transport)


def _sftp_list(config: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    root = str(parameters.get("path") or config.get("root_path") or ".")
    transport, client = _open_sftp(config)
    try:
        entries = [
            {"name": item.filename, "size": item.st_size, "modified": item.st_mtime, "mode": item.st_mode}
            for item in client.listdir_attr(root)[:500]
        ]
        return {"path": root, "entries": entries}
    finally:
        client.close()
        transport.close()


def _sftp_upload(config: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    import io
    path = str(parameters.get("path") or "").strip()
    if not path:
        raise ValueError("Remote path is required")
    raw = parameters.get("content_base64")
    content = base64.b64decode(str(raw)) if raw else str(parameters.get("text") or "").encode("utf-8")
    transport, client = _open_sftp(config)
    try:
        with client.file(path, "wb") as handle:
            handle.write(content)
        return {"uploaded": True, "path": path, "bytes": len(content)}
    finally:
        client.close()
        transport.close()
