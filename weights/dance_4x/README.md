# dance_4x

The full five-file package is distributed in the `Q1_4x_20261117.zip` release asset, not as new large git blobs. Current publication status and SHA256: [Q1 release notes](../../docs/q1-release-20261117.md).

Files: deploy.yaml (tracked), policy.onnx, motion.npz, deploy_motion.npz, scene.mjb (release-only). Extract the complete deployment ZIP to a new directory and verify its manifest first; do not mix arbitrary old runtime/teleop files. Full bundle includes dual_v3b, getup dependencies and robot assets. Pass `--dance weights/dance_4x` explicitly; the native CLI default is a different package name.

Software simulation tested; not hardware/ARM/ROS acceptance. Never treat the reference-only walking assets or the research velocity checkpoint as replacements for dual_v3b.
