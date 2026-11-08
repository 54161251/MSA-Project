# Shared contracts

Use stable IDs to connect equipment records, map markers, training records, and inspection events. The starter fixture shows how an equipment ID links a floor plan marker to its equipment record.

Agree on field names and allowed values with affected module owners before adding a shared contract. Staff approval controls access and publication of safety guidance. Machine status and clearance checks use stored records.

## Where the contracts live in code

| Contract | File |
| --- | --- |
| Agent results, citations, safety statements and tool verdicts (`AgentResult`, `Citation`, `Statement`, `ToolVerdict`) | `src/msa/core/contracts.py` |
| Machine status values, training states and approval kinds | `src/msa/core/contracts.py`, `src/msa/core/training.py` |
| Escalation codes E01–E15 | `src/msa/core/escalations.py` |
| Database tables, including the append-only logs | `src/msa/core/models.py`, `src/msa/core/db.py` |
| How a module plugs in (routes, approval handlers, events, seed data, daily jobs) | `src/msa/core/registry.py` |

Equipment IDs (`laser-01`, `fdm-01` and so on) are the stable key across `data/makerspace/equipment.yaml`, the floor plan, training records and logs, as in the starter fixture.
