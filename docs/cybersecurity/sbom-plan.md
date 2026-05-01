# SBOM Plan

Status: Draft prepared to support expert regulatory review.

## Current Inputs

- Python dependency manifests under `app/server/` and `app/camera/`.
- Yocto recipes and image composition under `meta-home-monitor/` and `config/`.
- Existing `sbom/` directory for generated or retained SBOM evidence.
- GitHub Actions workflows for CI and release validation.

## Plan

1. Generate SBOM artifacts for server Python dependencies.
2. Generate SBOM artifacts for camera Python dependencies.
3. Generate or collect Yocto image package manifests for server and camera
   images.
4. Store release SBOM artifacts under a release-specific evidence location.
5. Link SBOM artifacts from release notes and vulnerability review records.
6. Review SBOM deltas for each dependency or image change.

## Traceability

- Security asset: SEC-007.
- Security control: SC-007.
- Threat: THREAT-007.
- Requirement: SWR-019.
- Verification: TC-020.

## Open Questions

- OPEN QUESTION: Choose the official SBOM format for releases, such as SPDX or
  CycloneDX.
- OPEN QUESTION: Decide retention period and storage location for release SBOM
  evidence.
