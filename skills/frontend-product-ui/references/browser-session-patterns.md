# Browser Session Patterns

Use this reference when UI work depends on an authenticated browser, SSO, cookies, extensions, DevTools MCP, Playwright, CDP, or a user-owned Chrome profile.

## Browser Session Contract

Before acting, identify and record:

- target app URL or route
- browser/tool used for access
- profile or session owner
- authentication source
- whether re-authentication is safe or fragile
- whether extensions are enabled or disabled
- whether cookies are present in the automation profile
- whether the page list shows the target page or only an isolated tab such as `about:blank`

Do not create or switch profiles when the user says the existing authenticated session matters. First find the session that already has access, verify the target page, and document any automation isolation.

## Auth Verification

A visible page list is not proof of access. Verify auth against the actual target app:

- load or select the target page
- check for the expected signed-in view, workspace, tenant, or account marker
- confirm the route can perform the needed read or write action
- capture a screenshot or structured page state that proves the session is usable
- note when DevTools, Playwright, or CDP can see only an isolated profile

If auth cannot be transferred, report the boundary clearly and continue only with paths that preserve the user's session.

## Cookie And Extension Paths

Treat cookie import and extension loading as explicit implementation paths, not background assumptions.

For cookie import:

- identify source and destination profiles
- avoid printing secrets or raw cookie values
- verify domain, expiry, and secure flags when possible
- reload the target page and verify auth state after import

For extension loading:

- check whether the browser was launched with extensions disabled
- verify the unpacked extension path exists
- verify the extension appears in the target profile
- test the target workflow after loading, not just extension presence

When extensions are disabled in the automation profile, prefer a cookie/session path or ask the user to interact in the authenticated browser rather than starting over in a clean profile.

## Reporting Boundaries

When browser access is blocked or isolated, include:

- which browser/tool was checked
- what pages were visible
- what auth probe was attempted
- why access could not be reused
- the safest next action that preserves authentication

Do not imply the app is unauthenticated or broken when only the automation profile lacks the user's cookies.