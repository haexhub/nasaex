# ADR-0005: Declarative configuration applied by rebuild

Status: proposed, pending spike S5 · Date: 2026-10-15

## Context

The UI must change the system, and the system should stay reproducible and reversible.

## Decision

The desired state is one JSON file inside a git-tracked flake directory on the system disk.
The NixOS module reads it with `builtins.fromJSON`; the file must live in the flake
directory because a pure flake evaluation cannot read outside it. The backend writes the
file, commits it and runs `nixos-rebuild switch`. A failed build or activation does not
mean that a Git revert rolled back the running system: the failed configuration commit is
kept for diagnosis, the failure is reported, and the previous known-good NixOS generation
is restored when activation changed the running system. Actions that are not declarative,
such as formatting a disk, starting a sync or rolling back, are performed directly by the
backend.

## Alternatives

- **Backend writes configuration files directly**: fast, but loses generations, history and
  reproducibility.

## Consequences

- Every applied change costs a rebuild; its duration must be measured (spike S5).
- Every change has a git history in addition to system generations.
- Git history and the active NixOS generation are separate recovery concerns; failed
  rebuilds must remain observable and must not be represented by Git history alone.
