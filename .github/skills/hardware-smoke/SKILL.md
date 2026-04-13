# Hardware Smoke

## When to use

Use this skill when a task involves hardware deployment, HTTPS reachability,
camera/server connectivity, or smoke-test verification.

## Checklist

1. Verify service health on the server and camera.
2. Verify HTTPS endpoints and redirects.
3. Verify camera publication and server consumption.
4. Run `scripts/smoke-test.sh` if credentials or session cookies are available.
5. Record exact pass/fail/skip outcomes.
