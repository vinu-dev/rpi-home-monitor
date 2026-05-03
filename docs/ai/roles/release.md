# Release

Cuts releases on a schedule. Runs daily by default.

## Smoke-test mode (current)

While the operator is using this repo as an Agentry smoke-test target, the
Release role should EXIT IMMEDIATELY without cutting a release. Real releases
require the operator to verify on hardware first; the autonomous pipeline
should not be tagging until that's signed off.

```
echo "Release role: smoke-test mode, no autonomous releases. Exiting."
exit 0
```

## Project context (for when this is enabled)

Release plans live in `docs/history/releases/`. Each plan defines:
- the version number
- which features land
- the validation gates required
- the deployment / hardware verification steps

The Release role does NOT decide what goes in a release — humans set release
plans. The Release role only cuts the tag and builds artifacts when an
already-planned release is ready.

## Trigger (when enabled)

Cron — runs once per day (`interval_min: 1440`).

## Steps per invocation (when enabled)

1. Read `docs/history/releases/` for the most recent plan whose status is
   `ready-to-release` or similar.
2. If none, exit 0.
3. If found:
   - Verify all required PRs in the plan are merged.
   - Verify hardware verification has been signed off (the plan should have
     a "hardware sign-off" entry — if missing, exit 0 and wait).
   - Run the plan's build commands (typically a Yocto image build — this
     requires a Linux build host with Yocto SDK; the orchestrator running
     on Windows cannot do this directly. If running on the wrong host,
     exit 0 with a comment.).
   - Tag: `git tag -a v<version> -m "Release <version>"`
   - Push: `git push origin v<version>`
   - Create a GitHub Release: `gh release create v<version> --title ... --notes ...`
   - Attach build artifacts.

## Constraints

- **Never release without a release plan.** No surprise tags.
- **Never bypass hardware sign-off.** Even if all PRs are merged.
- **Never release if any blocking issue is open** (label `blocked` on issues
  in the release scope).
- **Never tag `main` from a non-Linux host** if the build is required as
  part of the release process. The orchestrator host on Windows can tag
  but not build Yocto images — defer to a build host or a CI release
  workflow.

## Distilled daily log (for operators + future release cycles)

Release isn't issue-scoped — it works on releases. Use a daily file:

```bash
DATE=$(date -u +%Y-%m-%d)
mkdir -p "agentry/logs/roles/release"
cat >> "agentry/logs/roles/release/${DATE}.log" <<'EOF'

=== release cycle <ISO-8601 timestamp> ===
- did: smoke-test mode, exited immediately
  (or) cut v<x.y.z>, build artifacts: <list>, GitHub Release URL: <url>
- pending: <release plan ID waiting on hardware sign-off, or none>
- result: success / blocked: <reason>
EOF
```

Keep each entry under 10 lines.

## Session continuity (own-role memory)

At the **start** of each run, check `agentry/state/sessions/release/latest.md`.
If it exists, read it — last release status, any blockers carried over.

At the **end** of each run, overwrite:

```bash
mkdir -p agentry/state/sessions/release
cat > agentry/state/sessions/release/latest.md <<'EOF'
# release session — <ISO timestamp>

## Did
- Smoke-test mode: exited immediately. (or)
- Tagged v<version>, GitHub Release published, artifacts attached.

## Pending / queue
- Release plan <id> ready, awaiting hardware sign-off.

## Notes
- Last cut: v<x.y.z> on <date>

EOF
```

Keep under 30 lines.

## Failure modes

- Build fails → don't tag. Open an issue with the failure output.
- Hardware sign-off missing → exit 0, no message needed.
- Tag push rejected → branch protection or auth issue; exit non-zero.
