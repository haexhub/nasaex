# ADR-0003: Go backend and Nuxt frontend

Status: accepted · Date: 2026-10-15

## Context

A click-driven UI is required from the start. The backend mostly starts processes, writes
files and streams logs. Contributors should find the code easy to read.

## Decision

- **Backend**: Go. It is a single static binary with good libraries for systemd, Docker and
  libvirt.
- **Frontend**: Nuxt with shadcn-vue, using the `@haex-space/ui` Nuxt layer for components.
  Nuxt is generated statically (`nuxt generate`, no SSR) and embedded into the backend with
  `go:embed`, so no Node process runs on the NAS.

## Alternatives

- **Rust or TypeScript backend**: Rust adds development time for little gain here;
  TypeScript adds runtime dependencies on the server.

## Consequences

- The UI cannot use server-side rendering or Nuxt server routes.
- The frontend depends on `@haex-space/ui` and on Nuxt 4.
