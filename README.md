# staging-env-scaler

Single HTTP-triggered Azure Function that scales the staging environment up or down,
combining the former `staging-DE-scaler` and `staging-web-scaler` apps.

## Endpoint

`GET|POST /api/ScaleEnvironment`

| Param   | Values          | Meaning                                    |
|---------|-----------------|--------------------------------------------|
| `scale` | `up` / `down`   | Required. Direction.                       |
| `de`    | `true` / `false`| Scale the DE ASPs (`shared_code/de_scaler.py`). Missing = skipped. |
| `web`   | `true` / `false`| Scale the web ASPs + SQL DBs (`shared_code/web_runner.py`). Missing = skipped. |

Params can be passed in the query string or a JSON body.

```
/api/ScaleEnvironment?scale=down&de=true&web=true
/api/ScaleEnvironment?scale=up&web=true          # DE skipped
```

Response: `{"scale": ..., "de": [...] | "skipped", "web": {...} | "skipped"}`.
Status 200 = all requested sides ran, 207 = one side raised, 500 = all raised, 400 = bad params.
Per-ASP / per-DB failures are reported inside the result rather than failing the whole call.

## Login & audit log

Every call needs a username/password (HTTP Basic Auth). Opening the link in a browser shows a
login popup; from a script:

```
curl -u hamza "https://<app>.azurewebsites.net/api/ScaleEnvironment?scale=down&de=true&web=true"
```

Users live in `dbo.ScalerUsers` and every call (including failed logins) is written to
`dbo.ScalerAuditLog` in the `AUDIT_SQL_DATABASE` database. If that database is unreachable the
call is refused with 503 and nothing is scaled.

One-time setup: run `sql/schema.sql` against the database, then add users:

```
python tools/add_user.py hamza              # add / reset password (prompts)
python tools/add_user.py hamza --disable    # block
```

Who did what:

```sql
SELECT TOP 50 invoked_at_utc, username, auth_ok, client_ip, scale, de, web, http_status, duration_ms
FROM dbo.ScalerAuditLog ORDER BY invoked_at_utc DESC;
```

## Configuration

Web targets come from app settings (`ASP_NAME`, `ASP_NAME_2/3`, `SQL_SERVER_*`, `SQL_DATABASE_*`,
`ALARM_FA_VNET_SUBNET_ID`, ...). Scale-up restores all three ASPs (`ASP_NAME`, `ASP_NAME_2`, `ASP_NAME_3`)
to PremiumV3 P0v3 with Always On + 64-bit; an ASP whose name/resource-group env var is empty is left alone.
SQL scale-up still restores only SQL #1 (100 DTU).
DE ASPs are the hard-coded `ASPS` list in `shared_code/de_scaler.py`.
Login/audit database: `AUDIT_SQL_SERVER`, `AUDIT_SQL_DATABASE`, `AUDIT_SQL_USER`, `AUDIT_SQL_PASSWORD`
(optional `AUDIT_SQL_DRIVER`, default `ODBC Driver 17 for SQL Server`).
Azure auth uses `DefaultAzureCredential` (`AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_TENANT_ID`).
