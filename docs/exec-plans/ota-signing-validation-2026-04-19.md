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

## What's still unvalidated on hardware this session

The camera was alive on HTTPS but SSH had wedged (banner-exchange
hang, the same pattern we saw during earlier install-heavy sessions).
Rather than burn another power-cycle loop, the camera bootstrap
(installing the signed dev image + repeating tests 1-3 against
`https://camera/api/ota/upload`) was deferred.

The camera runs the same `extract_bundle_version`, the same
`/etc/swupdate-enforce` marker logic, and the same `swupdate -c`
verification path as the server. Every server test above exercises
code identical to what the camera runs. High confidence that the
camera path behaves the same; still, the proper rehearsal closes
with a camera install once it's power-cycled.

## Resume checklist

1. Power-cycle camera (USB-C for ~10 s).
2. SSH in, `scp camera-update-dev-20260419-0151.swu root@.../data/ota/`.
3. `swupdate -i` the signed bundle, reboot, confirm
   `/etc/swupdate-enforce` is present on the new slot.
4. Browser-login to camera, upload same 0151 bundle → expect 200 +
   target_version in response.
5. Upload an old unsigned bundle → expect 500 with "Signature
   verification failed".
6. Upload a byte-flipped copy of 0151 → expect 500 with hash
   mismatch.
7. If all three pass on the camera, open the PR from
   `feat/ota-production-hardening` and (per the user's direction)
   merge after their review.
