# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub. Open the **Security** tab of this repository and choose **Report a vulnerability**, or use [the report form](https://github.com/anandapurva55/multi-cloud-mcp-server/security/advisories/new).

Include what you found, the steps to reproduce it and what an attacker could gain. You can expect a reply within seven days, and a confirmed problem is fixed before it is discussed in public.

## What is in scope

This server runs on your own machine with your own cloud credentials and passes an AI client's tool calls to GCP and AWS. These are the kinds of problem worth reporting:

- a way to make a query tool run a write, DDL or export statement, or to get past the read-only SQL check (`providers/sql_guard.py`)
- a way to get past the BigQuery byte limit or run work the environment's limits should stop
- input that escapes a Cloud Logging or Cloud Build filter, or a SQL identifier
- credentials, keys or tokens appearing in tool output, logs or error messages
- a problem in how `.env` is read, written or protected (it is created with permissions `600`)

## Using it safely

The SQL check is a best-effort guard and is not a substitute for read-only credentials. Run the server with a read-only role, scoped to what you want exposed. The README section [Least-privilege permissions](README.md#least-privilege-permissions) lists the exact GCP roles and AWS actions the tools need. Never commit `.env` or a service account key file.
