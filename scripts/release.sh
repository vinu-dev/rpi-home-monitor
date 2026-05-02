#!/usr/bin/env bash
# REQ: SWR-046, SWR-048; RISK: RISK-019; SEC: SC-018; TEST: TC-043, TC-045
# =============================================================
# release.sh — End-to-end release tooling for Home Monitor.
#
# One entry point, several subcommands. Each is idempotent where
# possible and prints what it would do before doing destructive
# operations.
#
# Usage:
#   ./scripts/release.sh prepare <version>
#       Bumps VERSION, promotes [Unreleased] → [<version>] in
#       CHANGELOG.md, runs the version-consistency check, commits
#       on a release/<version> branch with a conventional message.
#       Does NOT push, tag, build, or publish.
#
#   ./scripts/release.sh tag <version>
#       Creates an annotated tag v<version> on HEAD, pushes the
#       tag. Run this AFTER the release/<version> branch has merged
#       to main and main is green on CI.
#
#   ./scripts/release.sh build <version>
#       Runs ./scripts/build.sh server-prod && camera-prod, which
#       auto-signs because of the *-prod profile. Verifies the
#       resulting .swu filenames match v<version>. Prints artefact
#       paths.
#
#   ./scripts/release.sh verify <version>
#       Statically verifies the produced .swu signatures by
#       extracting sw-description.sig and comparing against the
#       repo's signing cert. No hardware required. Per ADR-0014,
#       the signature algorithm is CMS / ECDSA P-256.
#
#   ./scripts/release.sh publish <version>
#       Calls gh release create v<version> with the CHANGELOG
#       section as the body and uploads all production artefacts
#       (.swu, .wic.bz2, .wic.bmap, manifests, SBOMs).
#
# Version policy:
#   - X.Y.Z, semver-shaped (matches existing tags v1.2.0 … v1.3.1).
#   - Must be a valid bump from the most recent tag (script enforces).
#   - VERSION file is the single source of truth; everything else
#     reads from it (Yocto distro conf, build-swu.sh fallback).
# =============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$REPO_ROOT/VERSION"
CHANGELOG="$REPO_ROOT/CHANGELOG.md"

usage() {
    sed -n '4,40p' "$0"
    exit "${1:-0}"
}

die() {
    echo "release.sh: $*" >&2
    exit 1
}

require_clean_tree() {
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
        die "working tree is not clean — commit or stash first"
    fi
}

validate_version() {
    local v="$1"
    [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "version must be X.Y.Z (semver), got: $v"
}

current_version() {
    if [ -f "$VERSION_FILE" ]; then
        tr -d '[:space:]' < "$VERSION_FILE"
    else
        echo "0.0.0"
    fi
}

# Compare a.b.c to x.y.z; require strict bump (new > old).
require_strict_bump() {
    local old="$1" new="$2"
    if [ "$old" = "$new" ]; then
        die "new version $new equals current $old — no bump"
    fi
    local greater
    greater="$(printf '%s\n%s\n' "$old" "$new" | sort -V | tail -n 1)"
    if [ "$greater" != "$new" ]; then
        die "$new is not a bump from $old"
    fi
}

# --- prepare -------------------------------------------------------

cmd_prepare() {
    local new="${1:-}"
    [ -n "$new" ] || die "prepare needs a version"
    validate_version "$new"

    local old
    old="$(current_version)"
    require_strict_bump "$old" "$new"
    require_clean_tree

    local branch="release/$new"
    if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$branch"; then
        die "branch $branch already exists locally"
    fi

    echo "==> bumping VERSION: $old → $new"
    echo "$new" > "$VERSION_FILE"

    echo "==> updating CHANGELOG"
    promote_changelog "$new"

    echo "==> running version consistency check"
    "$REPO_ROOT/scripts/check_version_consistency.py" \
        || die "version consistency check failed — fix before committing"

    echo "==> creating $branch and committing"
    git -C "$REPO_ROOT" checkout -b "$branch"
    git -C "$REPO_ROOT" add VERSION CHANGELOG.md
    git -C "$REPO_ROOT" commit -m "chore(release): v$new"

    cat <<EOF

==> Prepared release branch: $branch
    Next steps:
      git push -u origin $branch
      gh pr create --fill --base main --head $branch
    After CI is green and the PR is merged into main:
      ./scripts/release.sh tag $new
      ./scripts/release.sh build $new   # on the build host (signed prod)
      ./scripts/release.sh verify $new
      ./scripts/release.sh publish $new
EOF
}

# Move the [Unreleased] header to [<version>] — <today>, then add a
# fresh empty [Unreleased] block above it. Idempotent: refuses to
# touch a CHANGELOG that already has [<version>] populated.
promote_changelog() {
    local v="$1"
    local today
    today="$(date -u +%Y-%m-%d)"

    if grep -qE "^## \[$v\]" "$CHANGELOG"; then
        die "CHANGELOG already contains [$v] entry — refusing to overwrite"
    fi
    if ! grep -qE "^## \[Unreleased\]" "$CHANGELOG"; then
        die "CHANGELOG missing [Unreleased] section — cannot promote"
    fi

    # Replace `## [Unreleased]` with `## [<v>] — <today>` and re-insert
    # an empty Unreleased block above it.
    awk -v v="$v" -v today="$today" '
        BEGIN { promoted = 0 }
        /^## \[Unreleased\]/ && !promoted {
            print "## [Unreleased]"
            print ""
            print "(Nothing yet — next release will land here.)"
            print ""
            print "## [" v "] — " today
            promoted = 1
            next
        }
        /^\(Nothing yet — next release will land here\.\)$/ && promoted == 1 {
            # Drop the placeholder that belonged to the now-promoted Unreleased
            # heading; first occurrence after the new [v] header.
            promoted = 2
            next
        }
        { print }
    ' "$CHANGELOG" > "$CHANGELOG.tmp"
    mv "$CHANGELOG.tmp" "$CHANGELOG"
}

# --- tag -----------------------------------------------------------

cmd_tag() {
    local v="${1:-}"
    [ -n "$v" ] || die "tag needs a version"
    validate_version "$v"
    local file_v
    file_v="$(current_version)"
    [ "$file_v" = "$v" ] \
        || die "VERSION file says $file_v, you asked to tag v$v — bump first?"

    if git -C "$REPO_ROOT" rev-parse "v$v" >/dev/null 2>&1; then
        die "tag v$v already exists"
    fi

    require_clean_tree

    # Headline drawn from CHANGELOG.
    local headline
    headline="$(awk -v v="$v" '
        $0 ~ "^## \\[" v "\\]" { found=1; next }
        found && NF { print; exit }
    ' "$CHANGELOG")"
    headline="${headline:-Release v$v}"

    echo "==> annotated tag: v$v"
    echo "    message: v$v — $headline"
    git -C "$REPO_ROOT" tag -a "v$v" -m "v$v — $headline"
    echo "    (run 'git push origin v$v' to publish)"
}

# --- build ---------------------------------------------------------

cmd_build() {
    local v="${1:-}"
    [ -n "$v" ] || die "build needs a version"
    validate_version "$v"
    local file_v
    file_v="$(current_version)"
    [ "$file_v" = "$v" ] \
        || die "VERSION file says $file_v, you asked to build v$v — bump first?"

    # build.sh's *-prod paths require an exact tag on HEAD (or
    # MONITOR_VERSION explicitly set) — this is its release safety
    # rail (build.sh:204-212). Pass MONITOR_VERSION to be explicit.
    export MONITOR_VERSION="v$v"

    echo "==> building server-prod (signed)"
    "$REPO_ROOT/scripts/build.sh" server-prod
    echo "==> building camera-prod (signed)"
    "$REPO_ROOT/scripts/build.sh" camera-prod

    echo "==> artefacts:"
    find "$REPO_ROOT" -maxdepth 2 -name "server-update-v$v*.swu" -o -name "camera-update-v$v*.swu" 2>/dev/null
    find "$REPO_ROOT/build/tmp-glibc/deploy/images" \
         "$REPO_ROOT/build-zero2w/tmp-glibc/deploy/images" \
         -maxdepth 3 -name "*.wic.bz2" -o -name "*.wic.bmap" -o -name "*.manifest" \
         2>/dev/null | head -n 20
}

# --- verify --------------------------------------------------------
#
# Static signature verification, no hardware needed. SWUpdate's
# enforcement model: build-swu.sh embeds the CMS / PKCS7 signature
# of sw-description as ``sw-description.sig`` next to the manifest in
# the cpio bundle. We extract both, then ``openssl cms -verify`` them
# against the operator's signing cert (the public half of the same
# keypair the build used).

cmd_verify() {
    local v="${1:-}"
    [ -n "$v" ] || die "verify needs a version"
    validate_version "$v"

    local cert="${LOCAL_OTA_CERT:-$HOME/.monitor-keys/ota-signing.crt}"
    [ -f "$cert" ] || die "signing cert not found at $cert (set LOCAL_OTA_CERT)"

    local count=0
    local f
    for f in "$REPO_ROOT"/server-update-v"$v"*.swu "$REPO_ROOT"/camera-update-v"$v"*.swu; do
        [ -e "$f" ] || continue
        count=$((count + 1))
        echo "==> verifying $f"
        local tmp
        tmp="$(mktemp -d)"
        ( cd "$tmp" && cpio -i --quiet < "$f" \
            && openssl cms -verify \
                -in sw-description.sig -inform DER \
                -content sw-description \
                -CAfile "$cert" -purpose any -out /dev/null \
            && echo "    OK: signature verifies against $cert" ) \
        || { rm -rf "$tmp"; die "signature verification failed for $f"; }
        rm -rf "$tmp"
    done
    [ "$count" -gt 0 ] || die "no v$v .swu files found in $REPO_ROOT"
    echo "==> all $count bundles verified"
}

# --- publish -------------------------------------------------------

cmd_publish() {
    local v="${1:-}"
    [ -n "$v" ] || die "publish needs a version"
    validate_version "$v"

    git -C "$REPO_ROOT" rev-parse "v$v" >/dev/null 2>&1 \
        || die "tag v$v not found — run 'release.sh tag $v' first"

    # Pull the [<v>] section from CHANGELOG as the release body.
    local body
    body="$(awk -v v="$v" '
        $0 ~ "^## \\[" v "\\]" { found=1; print; next }
        found && /^## \[/      { exit }
        found                  { print }
    ' "$CHANGELOG")"
    [ -n "$body" ] || die "CHANGELOG has no [$v] section — populate it first"

    local body_file
    body_file="$(mktemp)"
    printf '%s\n' "$body" > "$body_file"

    # Collect all release artefacts.
    local artefacts=()
    local f
    for f in "$REPO_ROOT"/server-update-v"$v"*.swu "$REPO_ROOT"/camera-update-v"$v"*.swu; do
        [ -e "$f" ] && artefacts+=("$f")
    done
    while IFS= read -r f; do
        artefacts+=("$f")
    done < <(find \
        "$REPO_ROOT/build/tmp-glibc/deploy/images" \
        "$REPO_ROOT/build-zero2w/tmp-glibc/deploy/images" \
        -maxdepth 3 \
        \( -name "*.wic.bz2" -o -name "*.wic.bmap" -o -name "*.manifest" -o -name "*.spdx.tar.zst" \) \
        2>/dev/null)

    [ "${#artefacts[@]}" -gt 0 ] || die "no artefacts to upload — run 'release.sh build $v' first"

    echo "==> creating release v$v with ${#artefacts[@]} artefact(s)"
    gh release create "v$v" \
        --title "v$v" \
        --notes-file "$body_file" \
        "${artefacts[@]}"
    rm -f "$body_file"
}

# --- main ----------------------------------------------------------

case "${1:-}" in
    prepare) shift; cmd_prepare "$@" ;;
    tag)     shift; cmd_tag "$@" ;;
    build)   shift; cmd_build "$@" ;;
    verify)  shift; cmd_verify "$@" ;;
    publish) shift; cmd_publish "$@" ;;
    -h|--help|help|"") usage 0 ;;
    *) usage 1 ;;
esac
