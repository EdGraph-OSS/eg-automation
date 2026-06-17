# NE-DataFlow

Automation scripts for the **NE: automate data flow for districts/ESUs** use case ([#16699](https://example.com/16699)).

## Scripts

| Script | Work Item | Description |
|---|---|---|
| `setup-tenant` | [#16703] | Provisions Ed-Fi instance, vendors, claim sets, and applications for a district tenant |
| `setup-esu` | [#16708] | Provisions Ed-Fi instance, vendor, and claim set for an ESU tenant |
| `sync-from-sea` | [#16704] | Creates Data Sync connections and job to sync from NDE/Adviser into the district's Ed-Fi instance |
| `sync-from-act` | [#16707] | Creates Data Sync connections and job to sync from ACT into the district's Ed-Fi instance |
| `obfuscated-sync-to-esu` | [#16706] | Creates the application in the ESU tenant and Data Sync connections/job for the obfuscated district→ESU sync |

## Execution Order

```
setup-tenant  ──┐
setup-esu     ──┤
                ├──→  sync-from-sea  ──→  sync-from-act  ──→  obfuscated-sync-to-esu
```

## Setup

1. Copy `.env.example` (from repo root) to `.env` and fill in all values.
2. Set required environment variables (see below).
3. Run scripts in the order shown above.

```powershell
# Install dependencies (run from eg-automation/)
uv sync

# Run all steps in order
uv run ne-data-flow

# Run a single step
uv run setup-tenant
uv run setup-esu
uv run sync-from-sea
uv run sync-from-act
uv run obfuscated-sync-to-esu
```

State files are written to the working directory. Run scripts from the directory where you want state files to land.

## Environment Variables

| Variable | Required by | Description |
|---|---|---|
| `EDGRAPH_ENVIRONMENT` | All | `Dev`, `QA`, `Production`, or `Local` |
| `EDGRAPH_CLIENT_ID` | All | OAuth2 client ID |
| `EDGRAPH_CLIENT_SECRET` | All | OAuth2 client secret |
| `TENANT_ID` | All | District tenant ID |
| `SCHOOL_YEAR` | All | School year (e.g. `2026`) |
| `DISTRICT_NAME` | `setup-tenant`, `obfuscated-sync-to-esu` | Full district name (used as vendor name) |
| `ESU_NAME` | `setup-esu`, `obfuscated-sync-to-esu` | ESU name (e.g. `ESU 6`) |
| `ESU_TENANT_ID` | `obfuscated-sync-to-esu` | ESU tenant ID |
| `NDE_EXTERNAL_INSTANCE_ID` | `sync-from-sea` | ID of the external Ed-Fi instance with NDE/Adviser credentials |
| `ACT_EXTERNAL_INSTANCE_ID` | `sync-from-act` | ID of the external Ed-Fi instance with ACT credentials |

## State Files

Each script writes a JSON state file to the working directory:

| File | Written by | Read by |
|---|---|---|
| `tenant-state.json` | `setup-tenant` | `sync-from-sea`, `sync-from-act`, `obfuscated-sync-to-esu` |
| `esu-state.json` | `setup-esu` | `obfuscated-sync-to-esu` |
| `sea-sync-state.json` | `sync-from-sea` | `sync-from-act` |
| `act-sync-state.json` | `sync-from-act` | — |
| `esu-sync-state.json` | `obfuscated-sync-to-esu` | — |

State files allow a script to be re-run safely — resources that were already created are reused.

## TODOs before first run

- [ ] Confirm `DATASYNC_JOB_TYPE_ID` in `ne_data_flow/_constants.py` (lookup from EdGraph documentation or API explorer)
- [ ] Confirm `DATASYNC_PROFILE_ID` in `ne_data_flow/_constants.py` (lookup from the target tenant)
- [ ] Verify the NE Ed-Fi version and tier in `_constants.py` match what is available in your environment
