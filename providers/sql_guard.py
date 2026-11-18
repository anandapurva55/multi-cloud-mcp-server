"""Read-only SQL guard for the query tools.

The query tools take SQL written by an LLM, and the data they read (log lines, table
cells) is untrusted text that can contain prompt injection. So the server refuses
anything that is not a single read-only statement. This is a best-effort lexical
check; it is defence in depth, NOT a substitute for giving the credentials a
read-only IAM role (see README, "Least-privilege permissions").

How it works: comments and quoted text (string literals, quoted identifiers) are
blanked out using a small scanner that follows each dialect's quoting rules, then the
remaining statement must (1) be exactly one statement, (2) start with a read-only
keyword, and (3) contain no write/DDL/export keyword anywhere. The dialect matters: BigQuery
treats backslash as an escape inside strings and Trino/Athena does not, so a scanner
that guessed wrong could be fooled into hiding a second statement inside a "string".
"""
import re

ALLOWED_FIRST_WORDS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "VALUES"}

# EXPLAIN is deliberately not here: Trino's EXPLAIN ANALYZE executes the inner statement.
METADATA_ONLY_FIRST_WORDS = {"SHOW", "DESCRIBE", "DESC"}

BLOCKED_WORDS = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "UNLOAD", "COPY", "EXPORT", "LOAD", "GRANT", "REVOKE", "CALL", "EXECUTE",
    "ANALYZE", "VACUUM", "OPTIMIZE", "MSCK", "REPAIR", "DECLARE", "SET", "BEGIN",
    "COMMIT", "ROLLBACK", "ASSERT", "RAISE", "ATTACH",
    # Pass-through: runs a SQL *string* on another database. String contents are blanked out
    # before checking, so the guard cannot see (and so cannot vouch for) what it would run.
    "EXTERNAL_QUERY",
}

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _blank_quoted_and_comments(sql: str, dialect: str) -> str:
    bigquery = dialect == "bigquery"
    out = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        two = sql[i:i + 2]
        if two == "--" or (bigquery and c == "#"):
            while i < n and sql[i] != "\n":
                i += 1
            out.append(" ")
        elif two == "/*":
            end = sql.find("*/", i + 2)
            if end == -1:
                raise ValueError("Unterminated /* comment in SQL.")
            i = end + 2
            out.append(" ")
        elif c in "'\"`":
            i = _skip_quoted(sql, i, c, bigquery)
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _skip_quoted(sql: str, i: int, quote: str, bigquery: bool) -> int:
    """Return the index just past the quoted section that starts at sql[i]."""
    n = len(sql)
    triple = bigquery and sql.startswith(quote * 3, i)
    delim = quote * 3 if triple else quote
    j = i + len(delim)
    while j < n:
        if bigquery and sql[j] == "\\":
            j += 2
            continue
        if sql.startswith(delim, j):
            # a doubled quote inside a single-quoted section is an escaped quote
            if not triple and sql.startswith(quote * 2, j):
                j += 2
                continue
            return j + len(delim)
        j += 1
    raise ValueError("Unterminated quoted string or identifier in SQL.")


def assert_read_only_sql(sql: str, dialect: str) -> None:
    """Raise ValueError unless `sql` is a single read-only statement.
    dialect: 'bigquery' or 'athena'."""
    if dialect not in ("bigquery", "athena"):
        raise ValueError(f"Unknown SQL dialect '{dialect}'.")
    cleaned = _blank_quoted_and_comments(sql, dialect)
    statements = [s for s in cleaned.split(";") if s.strip()]
    if not statements:
        raise ValueError("SQL is empty.")
    if len(statements) > 1:
        raise ValueError("Only a single SQL statement is allowed per call.")
    words = [w.upper() for w in _WORD.findall(statements[0])]
    if not words or words[0] not in ALLOWED_FIRST_WORDS:
        raise ValueError(
            "Only read-only queries are allowed (statement must start with "
            f"{', '.join(sorted(ALLOWED_FIRST_WORDS))}); got '{words[0] if words else ''}'."
        )
    if words[0] in METADATA_ONLY_FIRST_WORDS:
        return  # SHOW CREATE TABLE etc. contain write keywords but cannot write
    blocked = sorted(set(words) & BLOCKED_WORDS)
    if blocked:
        raise ValueError(
            f"Query rejected: contains write/DDL keyword(s) {', '.join(blocked)}. "
            "This server only runs read-only queries."
        )
