        <!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
        ---
        applyTo: "meta-home-monitor/**,config/**,scripts/build*.sh"
        ---

        # Yocto Instructions

        Keep permanent product policy out of developer `local.conf`.
Extend layers cleanly and document the impact. Build on the VM.
Run at least `bitbake -p` and document any build or deploy
implications in the PR.
