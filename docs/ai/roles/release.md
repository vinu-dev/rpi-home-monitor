# Release — project-specific policy for `vinu-dev/rpi-home-monitor`

This file describes WHEN to cut a release in this repo. The HOW (daily
cron, build commands, tag/push, GitHub Release) lives in agentry's bundled
prompt — see `agentry/config.yml`.

## Smoke-test mode (currently ENABLED)

While agentry is being evaluated against this repo, the Release role
should **exit immediately without cutting a release**. No tag, no build,
no GitHub Release.

To leave smoke-test mode: edit this file and remove the
"smoke-test mode" section. Until then, the agent reads this file and
exits 0.

## Release policy (when smoke-test mode is off)

- Releases require an active plan in `docs/history/releases/<release-id>.md`
- The plan must list:
  - target version
  - PRs that must be merged
  - hardware verification steps + sign-off mark
  - build artifacts to produce
- The Release role only cuts the tag and builds when:
  - all listed PRs are merged
  - hardware sign-off is recorded in the plan
  - no `blocked` issues remain in the release scope

## Versioning

Semantic versioning. Bump via the rule:
- breaking change → major
- additive feature → minor
- fix only → patch

Read `VERSION` to see the current version.

## Build commands (Linux build host required)

The orchestrator host on Windows can tag and create the GitHub Release,
but it cannot build Yocto images. If the release plan calls for a Yocto
image, defer to a Linux build host or a CI release workflow — exit 0
with a comment if you're on Windows.

## Changelog

Generate from merged PR titles since last tag:
```bash
git log --pretty=format:"- %s" "v$(cat VERSION)"..HEAD | grep -E "^- \[[0-9]+\]"
```

## Signing

Tag with `git tag -a v<version> -m "Release <version>"`. Signing is not
required at this stage; revisit if/when the project ships signed images.

## After releasing

- Update `VERSION`
- Update `CHANGELOG.md` with the cut date + summary
- Update DHF (`docs/dhf/`) if the release affects hardware behavior
- Comment on each merged PR: "Shipped in v<version>"
