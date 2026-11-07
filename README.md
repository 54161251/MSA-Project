# Makerspace Safety Agent

ENMGT 5405 final project. This prototype will help members find equipment, check training and machine status, and find staff-approved guidance. Staff will manage equipment, review drafted procedures, and handle escalations.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/msa/` | Shared platform and application modules |
| `docs/` | Shared contracts and workflow decisions |
| `fixtures/` | Fictional makerspace data for development |
| `tests/` | Module and integration tests |
| `.github/workflows/ci.yml` | Automated checks on pushes to main and pull requests targeting main |

## Run the starter checks

With Python 3.11 installed, run this from the repository root:

```sh
python -m unittest discover -s tests -v
```

The starter test checks the links between the fictional equipment and floor plan. Add tests for your module as you build it.

## Contribute

Create a branch for each change and open a pull request against `main`. Run the checks before merging. Ask the platform lead and affected module owners to review shared contract changes.
