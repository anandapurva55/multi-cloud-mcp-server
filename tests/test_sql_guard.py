"""Tests for providers/sql_guard.py: the read-only gate in front of bq_query / athena_query."""
import pytest

from providers.sql_guard import assert_read_only_sql

DIALECTS = ["bigquery", "athena"]


def ok(sql, dialect):
    assert_read_only_sql(sql, dialect)


def rejected(sql, dialect):
    with pytest.raises(ValueError):
        assert_read_only_sql(sql, dialect)


# ---------------------------------------------------------------------------
# legitimate read queries must pass
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from t",
    "  \n SELECT a, b FROM t WHERE c = 'x' ORDER BY a LIMIT 10  ",
    "SELECT 1;",                                           # one trailing semicolon is fine
    "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
    "SELECT created_at, updated_at, deleted_flag, insert_count FROM t",   # keywords only as prefixes
    "SELECT COUNT(*) FROM t GROUP BY region HAVING COUNT(*) > 1",
    "SELECT REPLACE(name, 'a', 'b') FROM t",
    "SELECT a FROM t1 JOIN t2 USING (id) UNION ALL SELECT a FROM t3",
    "SELECT 'DROP TABLE x' AS s",                          # keyword inside a string literal
    "SELECT 'it''s; DROP TABLE x' AS s",                   # doubled-quote escape hides a ';'
    'SELECT "delete" FROM t',                              # quoted identifier named like a keyword
    "SELECT 1 -- DROP TABLE x",                            # keyword inside a line comment
    "SELECT /* INSERT INTO x */ 1",                        # keyword inside a block comment
    "SELECT 1 /* ; DROP TABLE x ; */",
    "EXPLAIN SELECT * FROM t",
    "SHOW TABLES",
    "DESCRIBE t",
    "SELECT * FROM UNNEST([1,2,3]) AS n",
    "VALUES (1), (2)",
])
def test_read_only_statements_are_allowed(sql, dialect):
    ok(sql, dialect)


@pytest.mark.parametrize("sql", [
    "SHOW CREATE TABLE sales.orders",    # contains CREATE but cannot write
    "show create view v",
])
def test_show_create_is_allowed_on_athena(sql):
    ok(sql, "athena")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM `proj.ds.my-table`",                    # backtick identifier
    "SELECT r'C:\\path\\to' AS p",                         # raw string with backslashes
    "SELECT '''multi\nline; DROP TABLE x''' AS s",         # triple-quoted string (BigQuery)
    "SELECT 'a\\'b; DROP TABLE x' AS s",                   # backslash-escaped quote (BigQuery)
    "SELECT 1 # DROP TABLE x",                             # '#' comment (BigQuery)
    "SELECT * EXCEPT (a) FROM t",
])
def test_bigquery_specific_syntax_is_allowed(sql):
    ok(sql, "bigquery")


# ---------------------------------------------------------------------------
# writes, DDL, exports, scripting must be rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("sql", [
    "DROP TABLE t",
    "drop table t",
    "DELETE FROM t WHERE TRUE",
    "INSERT INTO t VALUES (1)",
    "INSERT INTO t SELECT * FROM s",
    "UPDATE t SET a = 1 WHERE TRUE",
    "MERGE t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
    "TRUNCATE TABLE t",
    "ALTER TABLE t ADD COLUMN c INT",
    "CREATE TABLE t (a INT)",
    "CREATE OR REPLACE TABLE t AS SELECT 1",
    "CREATE TABLE t AS SELECT * FROM s",             # Athena CTAS writes to S3
    "GRANT SELECT ON t TO x",
    "CALL my_proc()",
    "EXECUTE IMMEDIATE 'DROP TABLE t'",
    "DECLARE x INT64 DEFAULT 1",
    "SET x = 1",
    "BEGIN SELECT 1; END",
    "",
    "   ",
    ";",
    "-- just a comment",
])
def test_writes_and_non_queries_are_rejected(sql, dialect):
    rejected(sql, dialect)


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("sql", [
    "WITH t AS (SELECT 1) INSERT INTO x SELECT * FROM t",         # DML hidden behind a CTE
    "WITH t AS (SELECT 1) DELETE FROM x",
    "SELECT 1; DROP TABLE t",                                     # stacked statements
    "SELECT 1; SELECT 2",                                         # even two reads: one statement per call
    "SELECT 1 ;\n INSERT INTO t VALUES (1)",
    "EXPLAIN ANALYZE INSERT INTO t SELECT 1",                     # Trino EXPLAIN ANALYZE executes the statement
    "SELECT 1 FROM t -- comment\n; DROP TABLE t",
    "SELECT 1 /* c */ ; /* c */ DROP TABLE t",
])
def test_hidden_or_stacked_writes_are_rejected(sql, dialect):
    rejected(sql, dialect)


@pytest.mark.parametrize("sql", [
    "UNLOAD (SELECT * FROM t) TO 's3://bucket/x/' WITH (format='JSON')",
    "SELECT 1; UNLOAD (SELECT 1) TO 's3://b/x'",
    "MSCK REPAIR TABLE t",
    "VACUUM t",
    "OPTIMIZE t REWRITE DATA USING BIN_PACK",
    "ANALYZE t",
])
def test_athena_specific_writes_are_rejected(sql):
    rejected(sql, "athena")


@pytest.mark.parametrize("sql", [
    "EXPORT DATA OPTIONS(uri='gs://b/x*.csv', format='CSV') AS SELECT 1",
    "LOAD DATA INTO ds.t FROM FILES (format='CSV', uris=['gs://b/x'])",
    "ASSERT (SELECT COUNT(*) FROM t) > 0",
    "RAISE USING MESSAGE = 'x'",
])
def test_bigquery_specific_writes_are_rejected(sql):
    rejected(sql, "bigquery")


# ---------------------------------------------------------------------------
# the scanner must not be foolable by quoting tricks (this is the security-critical part)
# ---------------------------------------------------------------------------

def test_backslash_does_not_escape_quotes_in_athena():
    # Trino: the string is 'a\' (backslash is literal), so the quote ENDS it and DROP is real SQL.
    # A scanner that treated backslash as an escape (BigQuery rules) would hide the DROP.
    rejected("SELECT 'a\\'; DROP TABLE t; --'", "athena")


def test_same_text_is_a_harmless_string_in_bigquery():
    # BigQuery: \' escapes the quote, so everything up to the final quote is ONE string literal.
    ok("SELECT 'a\\'; DROP TABLE t; --'", "bigquery")


def test_bigquery_double_backslash_then_quote_ends_the_string():
    # '\\' is an escaped backslash, so the following quote closes the string and DROP is real SQL.
    rejected("SELECT '\\\\'; DROP TABLE t; --'", "bigquery")


def test_bigquery_triple_quote_string_may_contain_single_quotes_and_semicolons():
    ok("SELECT '''it's ; fine''' AS s", "bigquery")


def test_bigquery_triple_quote_does_not_swallow_following_statement():
    rejected("SELECT '''a''' ; DROP TABLE t", "bigquery")


def test_hash_is_a_comment_in_bigquery_but_not_in_athena():
    ok("SELECT 1 # ; DROP TABLE t", "bigquery")
    rejected("SELECT 1 # ; DROP TABLE t", "athena")


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("sql", [
    "SELECT 'unterminated",
    'SELECT "unterminated',
    "SELECT /* unterminated",
])
def test_unterminated_quotes_and_comments_are_rejected(sql, dialect):
    rejected(sql, dialect)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_keywords_are_matched_as_whole_words_case_insensitively(dialect):
    rejected("SeLeCt 1 FROM t WHERE x IN (SELECT 1); dRoP table t", dialect)
    ok("SELECT updated, created_by, dropped FROM t", dialect)


def test_unknown_dialect_is_an_error():
    with pytest.raises(ValueError, match="Unknown SQL dialect"):
        assert_read_only_sql("SELECT 1", "mysql")


def test_error_messages_name_the_problem():
    with pytest.raises(ValueError, match="must start with"):
        assert_read_only_sql("DROP TABLE t", "athena")
    with pytest.raises(ValueError, match="DELETE"):
        assert_read_only_sql("WITH t AS (SELECT 1) DELETE FROM x", "athena")
    with pytest.raises(ValueError, match="single SQL statement"):
        assert_read_only_sql("SELECT 1; SELECT 2", "athena")


@pytest.mark.parametrize("dialect", DIALECTS)
def test_external_query_passthrough_is_rejected_because_its_inner_sql_is_invisible(dialect):
    # the quoted argument is blanked before checking, so a DELETE inside it would go unseen
    rejected("SELECT * FROM EXTERNAL_QUERY('proj.us.conn', 'DELETE FROM t WHERE TRUE')", dialect)
    rejected("SELECT * FROM EXTERNAL_QUERY('proj.us.conn', 'SELECT 1')", dialect)
