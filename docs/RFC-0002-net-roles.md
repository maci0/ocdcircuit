# RFC-0002 — Net roles: power/signal classes as compile errors

Status: Accepted (ships this round)

## Problem
atopile rejects illegal connections at compile time (`~`/`~>` wiring with
typed interfaces). We have no pin directions on any footprint, so full
port typing is a data project, not a code change. The buildable subset:
net *roles* — a signal net shorted to a power net is always a bug, and
ERC can prove it from constraints alone.

## Decision
`class` definitions take `role=power|signal` (default: untyped, no check):

```ocd
class highvolt role=power
HV class=highvolt :: J1.3 <--> U1.2
```

ERC errors when a signal-role net shares a pin with a power-role net
(`role-clash HV/SIG at U1.2`). Same-pin-twice and power-short checks
already exist; this covers the cross-role case they miss.

## Non-goals
- Pin directions / port typing (`~` wiring): needs direction data on all
  footprints + symbols. Tracked, not this RFC.
- Current-capacity roles (power nets sized by `power` widths already).
- Role inference (a net named VCC is not declared power until constrained).

## Why not warn
A role clash is never intentional — warnings scroll past, errors block
fab. Matches `power-short` precedent (error, not warning).
