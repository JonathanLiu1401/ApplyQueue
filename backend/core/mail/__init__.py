"""M4: linked mailboxes (Gmail via Google OAuth, Outlook via Microsoft Graph).

ApplyQueue reads mail through the provider APIs with a client the user registers themselves.
Tokens live only in <data_dir>/vault/email_accounts.json (0600) and never leave the device.
"""
