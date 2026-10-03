# Object Extraction Patterns

This file describes how to enumerate and extract DDL for each Snowflake object type when cloning a schema.

## Inventory Queries

```sql
-- Tables (BASE TABLE, EXTERNAL TABLE, ICEBERG, etc.)
SELECT TABLE_NAME, TABLE_TYPE, IS_ICEBERG
FROM <DB>.INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = '<SCHEMA>'
ORDER BY TABLE_NAME;

-- Views
SELECT TABLE_NAME
FROM <DB>.INFORMATION_SCHEMA.VIEWS
WHERE TABLE_SCHEMA = '<SCHEMA>';

-- Sequences
SHOW SEQUENCES IN SCHEMA <DB>.<SCHEMA>;

-- Procedures (capture argument signature for GET_DDL)
SHOW PROCEDURES IN SCHEMA <DB>.<SCHEMA>;
SELECT "name", "arguments" FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));

-- Functions
SHOW USER FUNCTIONS IN SCHEMA <DB>.<SCHEMA>;

-- Streams
SHOW STREAMS IN SCHEMA <DB>.<SCHEMA>;

-- Tasks
SHOW TASKS IN SCHEMA <DB>.<SCHEMA>;

-- Dynamic Tables
SHOW DYNAMIC TABLES IN SCHEMA <DB>.<SCHEMA>;

-- Materialized Views
SHOW MATERIALIZED VIEWS IN SCHEMA <DB>.<SCHEMA>;

-- File Formats
SHOW FILE FORMATS IN SCHEMA <DB>.<SCHEMA>;

-- Stages
SHOW STAGES IN SCHEMA <DB>.<SCHEMA>;
```

## DDL Extraction

| Object Type   | GET_DDL call                                                                 |
|---------------|------------------------------------------------------------------------------|
| TABLE         | `GET_DDL('TABLE', '<DB>.<SCHEMA>.<NAME>', TRUE)`                             |
| VIEW          | `GET_DDL('VIEW', '<DB>.<SCHEMA>.<NAME>')`                                    |
| MATERIALIZED VIEW | `GET_DDL('VIEW', '<DB>.<SCHEMA>.<NAME>')`                                |
| DYNAMIC TABLE | `GET_DDL('DYNAMIC_TABLE', '<DB>.<SCHEMA>.<NAME>')`                           |
| SEQUENCE      | `GET_DDL('SEQUENCE', '<DB>.<SCHEMA>.<NAME>')`                                |
| PROCEDURE     | `GET_DDL('PROCEDURE', '<DB>.<SCHEMA>.<NAME>(<ARG_TYPES>)')`                  |
| FUNCTION      | `GET_DDL('FUNCTION', '<DB>.<SCHEMA>.<NAME>(<ARG_TYPES>)')`                   |
| STREAM        | `GET_DDL('STREAM', '<DB>.<SCHEMA>.<NAME>')`                                  |
| TASK          | `GET_DDL('TASK', '<DB>.<SCHEMA>.<NAME>')`                                    |
| FILE FORMAT   | `GET_DDL('FILE_FORMAT', '<DB>.<SCHEMA>.<NAME>')`                             |
| STAGE         | `GET_DDL('STAGE', '<DB>.<SCHEMA>.<NAME>')`                                   |

For **ICEBERG tables**, `GET_DDL('TABLE', ..., TRUE)` returns the ICEBERG footer; preserve `EXTERNAL_VOLUME`, `CATALOG`, and `BASE_LOCATION` but **rewrite BASE_LOCATION** to point at the target schema's path if it embeds the schema name.

## Constraint Extraction

```sql
SHOW PRIMARY KEYS  IN TABLE <DB>.<SCHEMA>.<NAME>;
SHOW IMPORTED KEYS IN TABLE <DB>.<SCHEMA>.<NAME>;
SHOW UNIQUE KEYS   IN TABLE <DB>.<SCHEMA>.<NAME>;
```

Use these results to validate that the regenerated DDL contains every original PK / UK / FK, plus any newly-added `_SKEY` constraints from this skill.

## Notes

- `GET_DDL('SCHEMA', ...)` is **not** preferred — it dumps a single block that's hard to align per-object.
- Always test with `only_compile=true` after rewriting before final delivery.
