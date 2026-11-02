-- Prefer the "Generate a new setup key" option on /mfa/setup.
-- This SQL fallback only resets an incomplete MFA enrollment. Replace the
-- placeholder with the exact account email before running it.
UPDATE users
SET mfa_secret = NULL
WHERE email = 'your-login-email'
  AND mfa_enabled = FALSE;