"""Real data profiler for Snowflake tables.

Profiles columns directly from Snowflake tables using aggregation queries,
outputting JSON compatible with the existing AI pipeline.
"""

import os
import json
import argparse
from pathlib import Path
from decimal import Decimal
from typing import Dict, List, Any
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Running in SiS - no .env needed

from utils.connection import get_snowflake_connection as _shared_get_connection


def _safe_value_to_str(value) -> str:
    """Safely convert a value to string, handling binary data."""
    if value is None:
        return 'NULL'
    if isinstance(value, (bytes, bytearray)):
        return '<binary data>'
    try:
        s = str(value)
        # Check if result looks like binary garbage (control characters)
        # Scan first 100 chars for control chars (except tab, newline, carriage return)
        if len(s) > 0 and any(0 <= ord(c) < 32 and c not in '\t\n\r' for c in s[:100]):
            return '<binary data>'
        return s
    except Exception:
        return '<unprintable>'


def to_json_safe(val):
    """Convert Decimal or other non-JSON types to standard Python types."""
    if isinstance(val, Decimal):
        # Convert to float if it has decimals, otherwise int
        return float(val) if val % 1 else int(val)
    return val


def get_connection(**kwargs):
    """Create a Snowflake connection using shared utility."""
    return _shared_get_connection(**kwargs)


def list_schemas(conn, database: str = None) -> List[str]:
    """List all schemas in the given database (fully-qualified for SiS)."""
    if database is None:
        database = os.getenv("SNOWFLAKE_DATABASE", "")
    database = database.strip().strip('"')
    if database:
        sql = f'SELECT schema_name FROM "{database}".information_schema.schemata ORDER BY schema_name'
    else:
        sql = "SELECT schema_name FROM information_schema.schemata WHERE catalog_name = CURRENT_DATABASE() ORDER BY schema_name"
    with conn.cursor() as cur:
        cur.execute(sql)
        return [row[0] for row in cur.fetchall()]


def _sq(val: str) -> str:
    """Escape a value for use inside SQL single quotes (prevent SQL injection)."""
    return val.replace("'", "''")


def list_tables(conn, schema: str = None, database: str = None, limit: int = 100) -> List[str]:
    """List tables in a schema. Uses fully-qualified database reference for SiS compatibility."""
    if schema is None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CURRENT_SCHEMA()")
                schema = cur.fetchone()[0]
        except Exception:
            schema = (os.getenv("SNOWFLAKE_SCHEMA") or "PUBLIC")
    if database is None:
        database = os.getenv("SNOWFLAKE_DATABASE", "")

    schema = schema.upper()
    database = database.strip().strip('"')
    safe_schema = _sq(schema)

    if database:
        sql = f"""
            SELECT table_name
            FROM "{database}".information_schema.tables
            WHERE table_schema = '{safe_schema}'
            ORDER BY table_name
        """
    else:
        sql = f"""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = '{safe_schema}'
            ORDER BY table_name
        """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [row[0] for i, row in enumerate(cur.fetchall()) if i < limit]


def get_column_info(conn, table_name: str, schema: str = None, database: str = None) -> List[Dict[str, str]]:
    """Get column names and data types for a table (fully-qualified for SiS)."""
    if schema is None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CURRENT_SCHEMA()")
                schema = cur.fetchone()[0]
        except Exception:
            schema = (os.getenv("SNOWFLAKE_SCHEMA") or "PUBLIC")
    if database is None:
        database = os.getenv("SNOWFLAKE_DATABASE", "")

    schema = schema.upper()
    database = database.strip().strip('"')
    safe_schema = _sq(schema)
    safe_table = _sq(table_name)

    if database:
        sql = f"""
            SELECT column_name, data_type
            FROM "{database}".information_schema.columns
            WHERE table_schema = '{safe_schema}' AND table_name = '{safe_table}'
            ORDER BY ordinal_position
        """
    else:
        sql = f"""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = '{safe_schema}' AND table_name = '{safe_table}'
            ORDER BY ordinal_position
        """
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [{"name": row[0], "datatype": row[1]} for row in cur.fetchall()]
        if not cols:
            safe_table_upper = _sq(table_name.upper())
            sql_upper = sql.replace(f"'{safe_table}'", f"'{safe_table_upper}'")
            cur.execute(sql_upper)
            cols = [{"name": row[0], "datatype": row[1]} for row in cur.fetchall()]
        return cols


def profile_column(conn, table_name: str, column_name: str, datatype: str, database: str = None) -> Dict[str, Any]:
    """Profile a single column using Snowflake aggregation queries."""
    schema = (os.getenv("SNOWFLAKE_SCHEMA") or "PUBLIC").upper()
    database = (database or os.getenv("SNOWFLAKE_DATABASE") or "").strip().strip('"')
    if database:
        full_table = f'"{database}"."{schema}"."{table_name.upper()}"'
    else:
        full_table = f'"{schema}"."{table_name.upper()}"'
    col = f'"{column_name.upper()}"'
    
    # Check if this is a special data type that can't be cast to STRING
    unsupported_types = ("GEOMETRY", "GEOGRAPHY", "VARIANT", "OBJECT", "ARRAY", "BINARY", "VARBINARY")
    is_special_type = any(datatype.upper().startswith(t) for t in unsupported_types)
    
    # For special types, use simpler profiling without string operations or APPROX_COUNT_DISTINCT
    if is_special_type:
        stats_sql = f"""
            SELECT 
                COUNT(*) AS total,
                COUNT({col}) AS physical_valid,
                COUNT({col}) AS robust_valid,
                0 AS distinct_count
            FROM {full_table}
        """
    else:
        # Improved stats query for normal types
        stats_sql = f"""
            SELECT 
                COUNT(*) AS total,
                COUNT({col}) AS physical_valid,
                COUNT(CASE 
                    WHEN {col} IS NULL THEN NULL
                    WHEN TRIM(CAST({col} AS STRING)) = '' THEN NULL
                    WHEN UPPER(TRIM(CAST({col} AS STRING))) IN ('N/A', 'NA', 'NULL', 'NONE', '<NULL>', '.') THEN NULL
                    ELSE 1 
                END) AS robust_valid,
                APPROX_COUNT_DISTINCT({col}) AS distinct_count
            FROM {full_table}
        """
    
    with conn.cursor() as cur:
        cur.execute(stats_sql)
        row = cur.fetchone()
        total, phys_valid, rob_valid, distinct = row[0], row[1], row[2], row[3]
    
    profile = {
        "datatype": datatype,
        "total": total,
        "nulls": total - rob_valid, # Robust nulls (includes placeholders)
        "physical_nulls": total - phys_valid, # Standard database nulls
        "distinct": distinct,
        "top_values": [],
        "sample_values": [],
    }
    
    # Get top values (most frequent) - skip for special types
    if not is_special_type:
        try:
            top_sql = f"""
                SELECT {col}, COUNT(*) AS cnt 
                FROM {full_table} 
                WHERE {col} IS NOT NULL
                GROUP BY {col} 
                ORDER BY cnt DESC 
                LIMIT 3
            """
            with conn.cursor() as cur:
                cur.execute(top_sql)
                # Store as list of dicts for easier charting - safely convert values
                profile["top_values"] = [{"value": _safe_value_to_str(row[0]), "count": int(row[1])} for row in cur.fetchall()]
        except Exception:
            pass  # Some column types may not support GROUP BY
    
    # Get sample values - skip for special types
    if not is_special_type:
        try:
            sample_sql = f"""
                SELECT DISTINCT {col} 
                FROM {full_table} 
                WHERE {col} IS NOT NULL 
                LIMIT 2
            """
            with conn.cursor() as cur:
                cur.execute(sample_sql)
                profile["sample_values"] = [_safe_value_to_str(row[0]) for row in cur.fetchall()]
        except Exception:
            pass
    
    # Get min/max for numeric types
    numeric_types = ("NUMBER", "FLOAT", "INT", "DECIMAL", "DOUBLE", "REAL")
    if datatype.upper().startswith(numeric_types):
        try:
            range_sql = f"""
                SELECT MIN({col}), MAX({col}) 
                FROM {full_table}
            """
            with conn.cursor() as cur:
                cur.execute(range_sql)
                row = cur.fetchone()
                if row[0] is not None:
                    profile["range"] = {
                        "min": float(row[0]),
                        "max": float(row[1])
                    }
        except Exception:
            pass
    
    return profile


def profile_table(conn, table_name: str, target_columns: List[str] = None, sample_pct: int = 100, schema: str = None, database: str = None) -> Dict[str, Dict[str, Any]]:
    """
    Profile a table efficiently by batching basic stats into a single wide query.
    Handles 'Millions of Records' by pushing all work to Snowflake's columnar engine.
    Uses fully-qualified DATABASE.SCHEMA.TABLE references for SiS compatibility.
    """
    if schema is None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CURRENT_SCHEMA()")
                schema = cur.fetchone()[0]
        except Exception:
            schema = (os.getenv("SNOWFLAKE_SCHEMA") or "PUBLIC")
    if database is None:
        database = os.getenv("SNOWFLAKE_DATABASE", "")

    schema = schema.upper()
    database = database.strip().strip('"')
    # Use fully-qualified table reference for SiS compatibility
    if database:
        full_table = f'"{database}"."{schema}"."{table_name}"'
    else:
        full_table = f'"{schema}"."{table_name}"'

    # Get column definitions
    columns_info = get_column_info(conn, table_name, schema=schema, database=database)
    if target_columns:
        target_set = {c.upper() for c in target_columns}
        columns_info = [c for c in columns_info if c["name"].upper() in target_set]

    if not columns_info:
        print(f"Warning: No valid columns found for profiling table {table_name}")
        return {}

    # --- STEP 1: BATCH BASIC STATS ---
    select_clauses = ["COUNT(*) AS TOTAL_ROWS"]
    
    # Define unsupported types that can't be cast to STRING or use with aggregate functions
    unsupported_types = ("GEOMETRY", "GEOGRAPHY", "VARIANT", "OBJECT", "ARRAY")
    
    for col in columns_info:
        c_name = col["name"].upper()
        c_ref = f'"{c_name}"'
        c_dtype = col["datatype"].upper()
        
        # Check if this is a special type
        is_special_type = any(c_dtype.startswith(t) for t in unsupported_types)
        
        if is_special_type:
            # Simpler stats for special types (no string casting, no distinct count)
            select_clauses.append(f'COUNT({c_ref}) AS {c_name}_ROB_VALID')
            select_clauses.append(f'COUNT({c_ref}) AS {c_name}_PHYS_VALID')
            # Note: We skip APPROX_COUNT_DISTINCT for special types as it uses HLL_ACCUMULATE which doesn't support them
        else:
            # Robust valid logic with string operations
            select_clauses.append(f"""
                COUNT(CASE 
                    WHEN {c_ref} IS NULL THEN NULL
                    WHEN TRIM(CAST({c_ref} AS STRING)) = '' THEN NULL
                    WHEN UPPER(TRIM(CAST({c_ref} AS STRING))) IN ('N/A', 'NA', 'NULL', 'NONE', '<NULL>', '.') THEN NULL
                    ELSE 1 
                END) AS {c_name}_ROB_VALID""")
            select_clauses.append(f'COUNT({c_ref}) AS {c_name}_PHYS_VALID')
            select_clauses.append(f'APPROX_COUNT_DISTINCT({c_ref}) AS {c_name}_DISTINCT')
        
        numeric_types = ("NUMBER", "FLOAT", "INT", "DECIMAL", "DOUBLE", "REAL", "DATE", "TIMESTAMP")
        if c_dtype.startswith(numeric_types):
             select_clauses.append(f'MIN({c_ref}) AS {c_name}_MIN')
             select_clauses.append(f'MAX({c_ref}) AS {c_name}_MAX')

        # Add character length profiling for all non-special types
        if not is_special_type:
            select_clauses.append(f'MIN(LENGTH(CAST({c_ref} AS STRING))) AS {c_name}_MIN_LEN')
            select_clauses.append(f'MAX(LENGTH(CAST({c_ref} AS STRING))) AS {c_name}_MAX_LEN')

    sample_clause = f" TABLESAMPLE ({sample_pct})" if sample_pct < 100 else ""
    batch_sql = f"SELECT \n  " + ",\n  ".join(select_clauses) + f"\nFROM {full_table}{sample_clause}"
    
    table_profile = {}
    
    try:
        with conn.cursor() as cur:
            cur.execute(batch_sql)
            row = cur.fetchone()
            col_names = [desc[0] for desc in cur.description]
            # Convert all Snowflake types (like Decimal) to JSON-safe Python types
            stats_map = {name: to_json_safe(val) for name, val in zip(col_names, row)}
            total_rows = stats_map.get("TOTAL_ROWS", 0)
    except Exception as e:
        raise Exception(f"Batch profiling failed for {table_name}: {str(e)}")

    # --- STEP 2: FINALIZE PROFILES ---
    for col in columns_info:
        c_name = col["name"].upper()
        dtype = col["datatype"]
        
        rob_valid = stats_map.get(f"{c_name}_ROB_VALID", 0)
        phys_valid = stats_map.get(f"{c_name}_PHYS_VALID", 0)
        # Distinct count may not exist for special types (GEOMETRY, GEOGRAPHY, etc.)
        distinct = stats_map.get(f"{c_name}_DISTINCT", 0)
        
        profile = {
            "datatype": dtype,
            "total": total_rows,
            "nulls": total_rows - rob_valid,
            "physical_nulls": total_rows - phys_valid,
            "distinct": distinct,
            "top_values": [],
            "sample_values": [],
            "range": {},
            "length_range": {}
        }
        
        if f"{c_name}_MIN" in stats_map:
            profile["range"] = {
                "min": stats_map[f"{c_name}_MIN"],
                "max": stats_map[f"{c_name}_MAX"]
            }

        # Add length range for string columns (min/max character lengths)
        if f"{c_name}_MIN_LEN" in stats_map:
            profile["length_range"] = {
                "min": stats_map[f"{c_name}_MIN_LEN"],
                "max": stats_map[f"{c_name}_MAX_LEN"]
            }
        
        # --- STEP 3: TOP VALUES (Required separate query per column) ---
        # We only do this if specifically needed, as it requires a full group by.
        try:
            top_sql = f"""
                SELECT "{c_name}", COUNT(*) AS cnt 
                FROM {full_table}{sample_clause}
                WHERE "{c_name}" IS NOT NULL
                GROUP BY 1 ORDER BY 2 DESC LIMIT 3
            """
            with conn.cursor() as cur:
                cur.execute(top_sql)
                profile["top_values"] = [{"value": str(r[0]), "count": to_json_safe(r[1])} for r in cur.fetchall()]
        except: pass
        
        table_profile[col["name"]] = profile

    return table_profile


def main():
    parser = argparse.ArgumentParser(description="Profile real Snowflake data")
    parser.add_argument("-t", "--tables", nargs="*", help="Specific table names to profile")
    parser.add_argument("-l", "--limit", type=int, default=10, help="Max tables to profile if not specified")
    parser.add_argument("-o", "--output", default="data/real_run/profile/column_profiles_real.json", help="Output JSON path")
    args = parser.parse_args()
    
    print("[profile_real_data] Connecting to Snowflake...")
    conn = get_connection()
    
    try:
        # Use uppercase schema for Information Schema queries
        schema = (os.getenv("SNOWFLAKE_SCHEMA") or "PUBLIC").upper()
        
        if args.tables:
            tables = args.tables
        else:
            tables = list_tables(conn, limit=args.limit)
        
        print(f"[profile_real_data] Profiling {len(tables)} tables in schema {schema}: {', '.join(tables)}")
        
        profiles = {}
        for table in tables:
            try:
                print(f"  Profiling table: {table}")
                profiles[table] = profile_table(conn, table)
            except Exception as e:
                print(f"  ❌ Error profiling table {table}: {e}")
                continue
    finally:
        conn.close()
    
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(profiles, indent=2), encoding='utf-8')
    print(f"[profile_real_data] Profiles written to: {out_path}")

    # Generate and save compatible metadata.json
    metadata_out = {}
    for table, col_map in profiles.items():
        cols_list = []
        for col_name, stats in col_map.items():
            cols_list.append({
                "name": col_name,
                "datatype": stats.get("datatype", "UNKNOWN")
            })
        metadata_out[table] = {"columns": cols_list}
    
    meta_path = out_path.parent / "real_data_metadata.json"
    meta_path.write_text(json.dumps(metadata_out, indent=2), encoding='utf-8')
    print(f"[profile_real_data] Metadata written to: {meta_path}")


def enrich_profiles_with_deep_analysis(
    profiles: Dict[str, Dict[str, Any]],
    schema: str = None,
    max_columns: int = 50,
    **connection_kwargs
) -> Dict[str, Dict[str, Any]]:
    """
    Enrich existing profiles with Deep AI Analysis (Phase 2).

    Adds semantic_type and detailed_analysis to each column profile.
    This is an OPTIONAL enhancement - existing code continues to work without it.

    Args:
        profiles: Existing profiles from profile_table()
        schema: Schema name
        max_columns: Maximum columns to analyze (to control costs)
        **connection_kwargs: Connection parameters

    Returns:
        Enriched profiles with additional fields:
        - semantic_type: AI-inferred semantic type (e.g., "address", "email", "id")
        - detailed_analysis: Deep AI analysis text
        - quality_score: 0-100 quality score
    """
    try:
        # Lazy import to avoid circular dependency
        from ai.data_quality_companion import DataQualityCompanion

        print(f"[profile_real_data] Starting Deep AI Analysis enrichment...")

        # Create companion instance
        companion = DataQualityCompanion(**connection_kwargs)

        enriched_profiles = {}
        total_columns = sum(len(cols) for cols in profiles.values() if isinstance(cols, dict))
        analyzed_count = 0

        for table_name, columns in profiles.items():
            if not isinstance(columns, dict):
                enriched_profiles[table_name] = columns
                continue

            enriched_columns = {}

            for col_name, profile in columns.items():
                if not isinstance(profile, dict):
                    enriched_columns[col_name] = profile
                    continue

                # Check limit
                if analyzed_count >= max_columns:
                    print(f"[profile_real_data] Reached max_columns limit ({max_columns}), skipping remaining columns")
                    enriched_columns[col_name] = profile
                    continue

                try:
                    print(f"[profile_real_data] Analyzing {table_name}.{col_name}... ({analyzed_count + 1}/{min(total_columns, max_columns)})")

                    # Run Deep AI Analysis
                    analysis_result = companion.analyze_column(
                        table_name=table_name,
                        column_name=col_name,
                        profile=profile,
                        description="",  # Will be inferred by AI
                        business_context="Deep profiling for Bronze-to-Silver mapping",
                        schema_name=schema,
                        database_name=connection_kwargs.get('database')
                    )

                    # Add AI analysis to profile
                    enriched_profile = profile.copy()
                    enriched_profile['semantic_type'] = analysis_result.get('column_type_inference', 'unknown')
                    enriched_profile['detailed_analysis'] = analysis_result.get('analysis_text', '')
                    enriched_profile['quality_score'] = analysis_result.get('quality_score', 0)
                    enriched_profile['quality_issues'] = analysis_result.get('quality_issues', [])
                    enriched_profile['ai_recommendations'] = analysis_result.get('recommendations', [])

                    enriched_columns[col_name] = enriched_profile
                    analyzed_count += 1

                except Exception as e:
                    print(f"[profile_real_data] Warning: Failed to analyze {table_name}.{col_name}: {e}")
                    # Keep original profile on failure
                    enriched_columns[col_name] = profile

            enriched_profiles[table_name] = enriched_columns

        print(f"[profile_real_data] Deep AI Analysis complete: {analyzed_count} columns enriched")
        return enriched_profiles

    except ImportError as e:
        print(f"[profile_real_data] Deep AI Analysis not available (missing dependency): {e}")
        print(f"[profile_real_data] Returning original profiles without enrichment")
        return profiles
    except Exception as e:
        print(f"[profile_real_data] Deep AI Analysis failed: {e}")
        print(f"[profile_real_data] Returning original profiles without enrichment")
        return profiles


if __name__ == "__main__":
    main()