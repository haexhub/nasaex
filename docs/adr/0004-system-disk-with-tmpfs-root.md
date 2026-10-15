# ADR-0004: System disk with tmpfs root

Status: accepted · Date: 2026-10-15

## Context

Unraid boots from a USB stick into RAM. The configuration model of ADR-0005 needs a
persistent `/nix/store` for new system generations. In pure RAM operation those would be
lost on reboot or exhaust memory, and generations and rollback would be weakened.

## Decision

Install on a dedicated system disk (SSD). The root filesystem is a tmpfs, so every boot
starts clean. Only `/nix` and a small persistent area for configuration and state are kept.
USB boot and RAM boot are not supported.

## Alternatives

- **RAM boot from USB**: low stick wear and a clean state, but a permanent RAM cost, slow
  boot, image-based updates and a conflict with rebuilds.
- **Plain persistent root**: simplest, but state drifts between boots.

## Consequences

- A system disk is required. USB sticks are not a supported target.
- State that must survive a reboot has to be declared as persistent.
