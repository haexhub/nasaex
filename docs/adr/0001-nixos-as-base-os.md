# ADR-0001: NixOS as base OS

Status: accepted · Date: 2026-10-15

## Context

The system is an appliance that holds user data and is updated rarely. A failed update
must be recoverable without a reinstall.

## Decision

Use NixOS. Each change produces a new system generation, so a bad change is undone by
booting the previous one. The desired state is declarative and reproducible.

## Alternatives

- **Debian stable**: stable and widely known, but update safety would have to be added
  with snapshots or an A/B scheme.
- **Arch**: rolling releases are a poor fit for a data-holding appliance.
- **Alpine**: small, but musl causes friction with Docker, Samba and drivers.

## Consequences

- Configuration is generated as Nix; the backend must work with that model (ADR-0005).
- Fewer contributors know Nix than Debian.
