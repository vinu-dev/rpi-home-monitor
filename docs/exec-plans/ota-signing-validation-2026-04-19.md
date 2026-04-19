# OTA signing validation — dev-mode rehearsal, 2026-04-19

This run validates end-to-end signature enforcement on a development
build before any production flip, per the per-user key-pair policy.
See ADR-0014 for the design; this doc is a field record of the test
results so the work can be resumed or audited.

## Build path exercised

```
scripts/build.sh server-dev --sign
scripts/build.sh camera-dev --sign
```

These now actually enable signing for dev targets. Earlier revisions
silently fell back to SWUPDATE_SIGNING=0 because the injection wrote
to `config/<machine>/local.conf` *after* `build_image` had copied it
into `build/conf/local.conf`. Fixed so `stage_local_ota_cert` writes
to the build-dir copy directly (commit e91fc9b).

A second fix (commit bef4c8b) added `sha256 = "@@..."` lines to the
sw-description templates and taught `build-swu.sh` to compute the
sha256 of each payload before stamping + signing. SWUpdate built
with `CONFIG_SIGNED_IMAGES` refuses bundles whose images lack a
hash with "Hash not set for rootfs.ext4.gz", even when the manifest
is signed — first install failed on this, now passes.

## Bundles produced

* `server-update-dev-20260419-0151.swu` (179 MB) — signed + hashed
* `camera-update-dev-20260419-0151.swu` (128 MB) — signed + hashed
* Rootfs images contain `/etc/swupdate-public.crt` and
  `/etc/swupdate-enforce` (verified via debugfs on the .ext4.gz
  before copying to Windows).

## Device under test

Server at 192.168.1.245, running the 0039 build (signed, enforcing).
Auto-confirm landed cleanly post-reboot — `upgrade_available=0`,
`boot_count=0`, `swupdate-check.service` active.

## GUI-driven tests through Settings → Updates

### Test 1 — signed + hashed bundle (positive path)

`POST /api/v1/ota/server/upload` with `server-update-dev-20260419-0151.swu`:

```
HTTP 200
{
  "message": "Update image staged and verified",
  "staged_path": "/data/ota/staging/server-update-dev-20260419-0151.swu",
  "target_version": "dev-20260419-0151",
  "filename": "server-update-dev-20260419-0151.swu"
}
```

Key result: `target_version` is returned by the endpoint, so the
admin sees exactly what version they are about to install before
clicking `Install & Reboot`. The UI pill "Current ... / After
install ..." picks this up.

### Test 2 — unsigned bundle (negative path)

Same endpoint with `server-update-dev-20260418-2148.swu` (pre-signing):

```
HTTP 400
{
  "error": "Verification failed: ...
           Image invalid or corrupted. Not installing ...
           SWUpdate *failed* !"
}
```

SWUpdate's `-c` check rejected the bundle at the server layer before
any write to `/data/ota/staging`. Behaves as designed.

### Test 3 — tampered signed bundle (negative path)

Took the signed 0151 bundle and flipped a single byte at offset 2 MB
(inside the rootfs.ext4.gz payload, past the CPIO headers):

```
HTTP 400
{
  "error": "Verification failed: ...
           HASH mismatch :
             ef26aa1588e4f3e6...e7271a898bb4aca85e83f3624366f2f12ad707
             <-->
             fbb5dc01d8223b79...3fe027d9e0e39656c6e9b491e85fa
           Image invalid or corrupted. Not installing ..."
}
```

SWUpdate caught the hash mismatch exactly as the signed design
promises — the signature on sw-description protects the hash field,
and the hash protects the payload.

## Camera-side repeat (1 hour later, same session)

Bootstrapping path — an unsigned camera can't accept a hashed bundle
("hash verification not enabled but hash supplied"). Had to build a
one-off transitional bundle: signed-enforcing rootfs inside, but
packaged without `--sign` so the current (unsigned) camera swupdate
would accept it. Once that installs and the camera reboots onto the
enforce-marked rootfs, further bundles must be signed+hashed.
Logic captured in `build-swu.sh`: `--sign` substitutes real hashes,
the unsigned branch deletes the `sha256` line entirely (commit b25bb0b).

After bootstrap the camera ran with `/etc/swupdate-enforce` +
`/etc/swupdate-public.crt` present, `SWUPDATE_ARGS="-v -k /etc/swupdate-public.crt"`.

### Camera Test 1 — signed + hashed (positive)

`POST https://192.168.1.186/api/ota/upload` with
`camera-update-dev-20260419-0151.swu`:
```
HTTP 200
{"message": "Install triggered", "bundle_bytes": 133333504}
```
Followed by /api/ota/status polling → `state: installing → installed`,
`progress: 100`. End-to-end signed install through the camera GUI
succeeded.

### Camera Test 2 — unsigned (negative)

`POST /api/ota/upload` with the pre-signing `camera-update-dev-20260418-2154.swu`:
```
HTTP 200 (upload accepted — camera-direct is async; sig check runs
         in the root installer after the trigger fires)
```
Followed by status poll → `state: error, error: "Signature verification failed"`.
Rejected.

### Camera Test 3 — tampered signed (negative)

Same byte-flip trick — copy of 0151, one byte flipped at offset 2 MB.
Upload accepted, then:
```
state: verifying (10 %) → error (10 %)
error: "Signature verification failed"
```

Camera returns the generic "Signature verification failed" rather
than the explicit "HASH mismatch" that the server showed, because
SWUpdate on the camera short-circuits on the CMS signature check
before reaching the per-image hash check. Same correctness, less
granular error text — acceptable.

## Summary — 6/6 tests pass

| # | Device | Bundle | Expected | Got |
|---|---|---|---|---|
| 1 | Server | signed + hashed | accept + target_version shown | ✓ |
| 2 | Server | unsigned legacy | rejected | ✓ "Image invalid or corrupted" |
| 3 | Server | tampered signed | rejected | ✓ "HASH mismatch" |
| 4 | Camera | signed + hashed | accept + install | ✓ state=installed |
| 5 | Camera | unsigned legacy | rejected | ✓ error "Signature verification failed" |
| 6 | Camera | tampered signed | rejected | ✓ error "Signature verification failed" |

## Deferred to user handoff

* PR opening / merge to main — branch `feat/ota-production-hardening`
  is up-to-date on origin. Waiting on explicit go-ahead per user
  instruction. No code changes pending.
