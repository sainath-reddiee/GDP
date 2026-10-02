import json
import csv
import re
import zipfile
from pathlib import Path
from typing import Optional, List, Dict, Any
from http import HTTPStatus
from threading import Thread
from io import BytesIO

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.schemas.response_models import StandardResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from app.config.settings import settings
from app.utils.cortex_client import cortex_complete
from app.routers.mapping_router import MappingRow, Project, DbtFile, MacroLibrary

Base = declarative_base()

class DbtFileItem(BaseModel):
    """Model for dbt file information"""
    id: int
    file_name: str
    file_content: str
    file_path: str

    class Config:
        from_attributes = True

class DbtFileUpdateRequest(BaseModel):
    """Request model for updating dbt file content"""
    file_content: str

class FinalQueryRequest(BaseModel):
    """Payload for asking AI to write SQL against final dbt models."""
    prompt: str
    max_files: int = 10  # optional guardrail to avoid massive prompts

class DbtFileAIEditRequest(BaseModel):
    prompt: str  # the change request from the user
    max_tokens: int | None = 1024  # optional guardrail


router = APIRouter()

# Create Snowflake engine with support for both password and private key auth
from app.utils.snowflake_connection import get_snowflake_engine

engine = get_snowflake_engine()
SessionLocal = sessionmaker(bind=engine)



def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()




def _safe_name(name: str) -> str:
    """Convert project name to safe directory name"""
    return re.sub(r"[^a-zA-Z0-9_]", "_", str(name)).lower().strip("_") or "project"



def _prepare_mapping_data_for_ai(mapping_rows: List[MappingRow]) -> dict:
    """Prepare complete mapping data structure for AI prompt"""
    # Group by target schema and target table
    by_target: Dict[str, Dict[str, List[dict]]] = {}
    
    # Collect all source tables - MUST collect ALL rows, not just mapped ones
    source_tables: Dict[str, dict] = {}
    
    # Collect ALL target columns (including unmapped ones)
    target_columns: Dict[str, Dict[str, List[dict]]] = {}
    
    # FIRST: Collect ALL source table info (including unmapped columns)
    for row in mapping_rows:
        if row.source_schema and row.source_table_name and row.source_column:
            source_key = f"{row.source_schema}.{row.source_table_name}"
            if source_key not in source_tables:
                source_tables[source_key] = {
                    "schema": row.source_schema,
                    "table": row.source_table_name,
                    "columns": {}
                }
            
            source_tables[source_key]["columns"][row.source_column] = {
                "data_type": row.source_data_type,
                "description": row.source_description or ""
            }
    
    # SECOND: Build target mappings AND collect unmapped target columns
    for row in mapping_rows:
        target_schema = row.target_schema or "silver"
        target_table = row.target_table_name
        
        if not target_table:
            continue  # Skip rows with no target table
            
        # Initialize target_columns structure
        if target_schema not in target_columns:
            target_columns[target_schema] = {}
        if target_table not in target_columns[target_schema]:
            target_columns[target_schema][target_table] = []
        
        # If target column exists (even without source mapping), collect it
        if row.target_column:
            has_source = bool(row.source_column and row.source_table_name)
            target_col_info = {
                "target_column": row.target_column,
                "target_data_type": row.target_data_type or "",
                "target_description": row.target_description or "",
                "has_source_mapping": has_source,
                "transformation_logic": row.transformation_logic or "",
                "cleaning_logic": row.cleaning_logic or "",
                "merge_strategy": row.merge_strategy or "UNION",
                "macros": row.macros or "",
                "source_schema": row.source_schema or "",
                "source_table": row.source_table_name or "",
                "source_column": row.source_column or "",
                "populate_as_null": not has_source and not (row.transformation_logic or "").strip()
            }
            
            # Check if this target column is already recorded
            existing_col = next(
                (col for col in target_columns[target_schema][target_table] 
                 if col["target_column"] == row.target_column),
                None
            )
            if existing_col:
                # Update existing column if it has a transformation logic and existing one doesn't
                if row.transformation_logic and not existing_col.get("transformation_logic"):
                    existing_col["transformation_logic"] = row.transformation_logic
                if row.cleaning_logic and not existing_col.get("cleaning_logic"):
                    existing_col["cleaning_logic"] = row.cleaning_logic
                if row.macros and not existing_col.get("macros"):
                    existing_col["macros"] = row.macros
            else:
                target_columns[target_schema][target_table].append(target_col_info)
        
        # Build target mappings (only for rows with source mappings)
        if row.source_column and row.source_table_name:
            if target_schema not in by_target:
                by_target[target_schema] = {}
            if target_table not in by_target[target_schema]:
                by_target[target_schema][target_table] = []
            
            mapping_entry = {
                "source_schema": row.source_schema,
                "source_table": row.source_table_name,
                "source_column": row.source_column,
                "source_data_type": row.source_data_type,
                "source_description": row.source_description or "",
                "target_column": row.target_column or "",
                "target_data_type": row.target_data_type or "",
                "target_description": row.target_description or "",
                "mapping_similarity": float(row.mapping_similarity) if row.mapping_similarity else None,
                "transformation_logic": (row.transformation_logic or '').strip(),
                "cleaning_logic": (row.cleaning_logic or '').strip(),
                "merge_strategy": (row.merge_strategy or '').strip() or "UNION",  # Default UNION for multi-table
                "macros": (row.macros or '').strip()
            }
            by_target[target_schema][target_table].append(mapping_entry)
    
    return {
        "target_mappings": by_target,
        "target_columns": target_columns,  # NEW: All target columns including unmapped ones
        "source_tables": {k: {
            "schema": v["schema"],
            "table": v["table"],
            "columns": list(v["columns"].keys())
        } for k, v in source_tables.items()},
        "total_mappings": len(mapping_rows)
    }


def _group_multi_source_mappings(mapping_rows: List[MappingRow]) -> Dict[str, List[dict]]:
    """
    Group source columns that map to the same target column.
    Returns a dict keyed by 'target_schema.target_table.target_column' with list of sources.
    Sources are sorted by mapping_similarity (highest first) for COALESCE priority.
    """
    multi_source = {}
    
    for row in mapping_rows:
        if not (row.target_schema and row.target_table_name and row.target_column):
            continue
        if not (row.source_column and row.source_table_name):
            continue
            
        key = f"{row.target_schema}.{row.target_table_name}.{row.target_column}"
        
        if key not in multi_source:
            multi_source[key] = []
        
        # Smart default for merge_strategy: empty/null = COALESCE for multi-source mappings
        merge_strat = (row.merge_strategy or '').strip()
        if not merge_strat or merge_strat.upper() == 'AUTO':
            merge_strat = 'COALESCE'  # Default for multi-source columns
        
        multi_source[key].append({
            "source_schema": row.source_schema,
            "source_table": row.source_table_name,
            "source_column": row.source_column,
            "source_data_type": row.source_data_type,
            "cleaning_logic": (row.cleaning_logic or '').strip(),
            "merge_strategy": merge_strat,
            "mapping_similarity": float(row.mapping_similarity) if row.mapping_similarity else 50.0,
            "transformation_logic": (row.transformation_logic or '').strip(),
            "macros": (row.macros or '').strip()
        })
    
    # Filter to only targets with multiple sources and sort by similarity
    result = {}
    for key, sources in multi_source.items():
        if len(sources) > 1:
            # Sort by mapping_similarity (highest first) - determines COALESCE order
            sources.sort(key=lambda x: x['mapping_similarity'], reverse=True)
            result[key] = sources
    
    return result


def _clean_json_response(text: str) -> dict:
    """Extract JSON from AI response, handling markdown code fences"""
    # Remove markdown code fences
    text = re.sub(r"^```json\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"^```\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    
    # Try to find JSON object
    try:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            json_str = text[start:end+1]
            return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    
    return {}


def _generate_complete_dbt_project_ai(mapping_rows: List[MappingRow], project_name: str) -> Dict[str, str]:
    """Generate complete dbt project using single AI prompt - ALL files in one go"""
    safe_name = _safe_name(project_name)
    mapping_data = _prepare_mapping_data_for_ai(mapping_rows)
    
    # Get multi-source mappings for special handling
    multi_source_mappings = _group_multi_source_mappings(mapping_rows)
    mapping_data["multi_source_mappings"] = multi_source_mappings
    
    # Fetch all macros from the database to provide them as context to the AI
    db = SessionLocal()
    try:
        macro_library = db.query(MacroLibrary).all()
    finally:
        db.close()

    # Format macros for the prompt
    macro_context = ""
    for m in macro_library:
        macro_context += f"- Macro Name: {m.name}\n  Description: {m.description}\n  SQL Definition:\n{m.sql_content}\n\n"

    # Use configured schema as the source name (sanitized)
    source_name = settings.SNOWFLAKE_SCHEMA.replace('"', '').replace("'", "")
    
    system_prompt = f"""You are an expert dbt (data build tool) engineer. Your task is to generate a complete, production-ready dbt project from source-to-target mapping specifications in a SINGLE response.

CRITICAL REQUIREMENTS:
1. Generate ALL files as a JSON object where keys are file paths and values are file contents
2. Use proper dbt syntax: {{{{ source('source_name', 'table_name') }}}} for sources, {{{{ ref('model_name') }}}} for model references
3. All SQL must be syntactically valid dbt SQL
4. All YAML must be valid YAML syntax
5. Source name should be '{source_name}' for all source tables
6. **CRITICAL**: Return ONLY the raw JSON object. Do NOT include any explanations, markdown formatting, or text before/after the JSON. Start your response with {{ and end with }}. No ```json``` code blocks.
7. Use TWO-LAYER ARCHITECTURE: staging models (Bronze) and silver models (Cleaned)

AVAILABLE MACRO LIBRARY (You MUST include the provided SQL definitions in macros/macro_library.sql if used):
{macro_context if macro_context else "No custom macros available. Use standard SQL."}

STANDARD DBT UTILS:
- `{{{{ dbt_utils.star(from=source(...), except=[...]) }}}}`: Use for selecting columns.
- `{{{{ dbt_utils.generate_surrogate_key([...]) }}}}`: Use for creating ID hashes.

METADATA HANDLING:
a) `cleaning_logic`: 
   - Apply these rules in the Silver layer models.
   - Rules like "TRIM", "UPPER", "COALESCE" should be interpreted as SQL expressions.
b) `merge_strategy`:
   - If multiple source tables target the same Silver table and `merge_strategy` is "UNION":
     * Create separate CTEs for each source table.
     * Ensure column counts and types align.
     * Use `UNION ALL` to combine them into the final Silver model.
c) `macros`:
   - If a specific macro is mentioned in the `macros` field, prioritize its usage for that column.

FILES TO GENERATE:
1. dbt_project.yml - Project configuration with name, version, profile, model paths
2. profiles.yml - Snowflake connection profile using environment variables:
   - user: "{{{{ env_var('SNOWFLAKE_USER') }}}}"
   - password: "{{{{ env_var('SNOWFLAKE_PASSWORD') }}}}"
   - account: "{{{{ env_var('SNOWFLAKE_ACCOUNT') }}}}"
   - warehouse: "{{{{ env_var('SNOWFLAKE_WAREHOUSE') }}}}"
   - database: "{{{{ env_var('SNOWFLAKE_DATABASE') }}}}"
   - schema: "{{{{ env_var('SNOWFLAKE_SCHEMA') }}}}"
   - role: "{{{{ env_var('SNOWFLAKE_ROLE') }}}}"
3. packages.yml - dbt packages (include dbt-labs/dbt_utils)
4. .gitignore - Standard dbt gitignore patterns
5. models/sources.yml - Complete source definitions. Use 'database' and 'schema' properties configured via env vars if possible, or default to the provided context. IMPORTANT: The source name MUST be '{source_name}'.
6. models/schema.yml - Documentation for staging and silver models
7. models/staging/stg_*.sql - Bronze/Staging models:
   - Select ALL columns from source
   - Basic type casting ONLY
8. models/silver/*.sql - Silver models:
   - Apply `cleaning_logic` and `transformation_logic`
   - Handle mergers (UNION ALL) if multiple sources exist for this target
   - Use table materialization for silver layer
   - Cast to `target_data_type`
9. macros/macro_library.sql - Implement the custom macros listed in the library

STAGING MODEL GENERATION GUIDELINES:
- CRITICAL: Create EXACTLY ONE staging model for EACH and EVERY unique source table listed in the "source_tables" structure
- You MUST create staging models for ALL source tables, even if they have NO target mappings
- The "source_tables" structure contains ALL source tables that need staging models
- DO NOT skip any source tables - if a table appears in "source_tables", it MUST have a staging model
- Select ALL columns from the source table (include unmapped columns too)
- Apply basic type casting (CAST to appropriate types)
- Handle special characters in column names
- Keep column names similar to source but standardized (snake_case preferred)
- Use view materialization for staging layer
- Example: If "source_tables" contains 5 tables, you MUST generate 5 staging model files

SILVER MODEL GENERATION GUIDELINES:
- CRITICAL: Create EXACTLY ONE silver model per UNIQUE target table name from the mapping data
- DO NOT combine multiple target tables into a single model file
- Each target table name must have its own separate SQL file
- Analyze the target_mappings structure to identify ALL unique target table names
- Use table materialization for silver layer
- CRITICAL: Also check the "target_columns" structure - it contains ALL target columns including those WITHOUT source mappings

HANDLING MULTIPLE SOURCE COLUMNS TO SINGLE TARGET COLUMN:
- CRITICAL: Check the "multi_source_mappings" structure in the mapping data
- This structure contains target columns that have multiple source columns mapped to them
- For each target column with multiple sources (key format: "schema.table.column"):
  1. If merge_strategy is "COALESCE" (default for multi-source, or empty/null):
     * Generate: COALESCE(source1, source2, source3, ...) AS target_column
     * Apply cleaning_logic to EACH source before COALESCE
     * Sources are already sorted by mapping_similarity (highest first) - use this order
     * Example: COALESCE(TRIM(table1.col1), TRIM(table2.col2)) AS target_col
     * This selects the first non-null value from the prioritized sources
  2. If merge_strategy is "CONCAT":
     * Generate: CONCAT(COALESCE(source1, ''), delimiter, COALESCE(source2, '')) AS target_column
     * Example: CONCAT(COALESCE(first_name, ''), ' ', COALESCE(last_name, '')) AS full_name
  3. If transformation_logic is provided in the first source (highest priority):
     * Use the exact transformation_logic as provided - it may reference multiple columns
     * Example: COALESCE(table1.building_sqft, table1.land_sqft) AS gross_area
  4. DO NOT select the same target column multiple times - always merge sources using COALESCE/CONCAT
  5. Add SQL comment documenting the multi-source mapping for maintainability:
     * -- Combined from: table1.col1 (priority 1), table2.col2 (priority 2)

- For EACH unique target table:
  * Create a separate models/silver/<target_table_name>.sql file
  * Analyze which staging models are needed for that specific target table
  * CRITICAL: Select ALL target columns from "target_columns" structure, not just mapped ones
  * CRITICAL: For columns listed in "multi_source_mappings", generate COALESCE logic, DO NOT select them multiple times
  * For target columns WITH source mappings (single source):
    - Map source columns to target columns per the mapping specifications
  * For target columns WITHOUT source mappings (has_source_mapping = false OR populate_as_null = true):
    - CRITICAL: Check the "populate_as_null" flag in target_columns structure
    - If populate_as_null = true (no source and no transformation_logic):
      * Generate NULL with appropriate data type casting based on target_data_type
      * Use this mapping for Snowflake data types:
        - varchar/string/text → CAST(NULL AS VARCHAR)
        - number/numeric/integer/int/bigint/smallint → CAST(NULL AS NUMBER)
        - float/double/decimal → CAST(NULL AS FLOAT)
        - boolean/bool → CAST(NULL AS BOOLEAN)
        - date → CAST(NULL AS DATE)
        - timestamp/datetime → CAST(NULL AS TIMESTAMP)
        - timestamp_ntz → CAST(NULL AS TIMESTAMP_NTZ)
        - timestamp_tz → CAST(NULL AS TIMESTAMP_TZ)
      * Example: CAST(NULL AS VARCHAR) AS column_name
      * Add a comment: -- No source mapping (populated as NULL)
    - If transformation_logic exists and is not empty:
      * Use the transformation_logic EXACTLY as provided - it may reference other columns
      * The transformation_logic should be applied directly in the SELECT statement
  * MERGE STRATEGY (UNION vs JOIN):
    - Analyze the 'merge_strategy' for target mapping rows:
    - If merge_strategy is 'UNION':
      * Generate a UNION ALL between all unique source tables that map to this target table.
      * Standardize column aliases to match the target column names.
    - If merge_strategy is 'JOIN' or not specified:
      * Use the 'JOIN LOGIC' described below to combine tables horizontally.
  * CRITICAL JOIN LOGIC - BUILD LOGICAL TABLE RELATIONSHIPS:
    1. IDENTIFY PRIMARY FACT TABLE:
       - Look for source tables with "FACT" in the name (e.g., FINANCE__FACT_*)
       - If multiple fact tables exist, choose the one with the most mapped columns to the target table
       - If no "FACT" tables exist, choose the source table with the most mapped columns
       - This will be your FROM table (the starting point for all joins)
    2. IDENTIFY DIMENSION TABLES:
       - Look for source tables with "DIM" in the name (e.g., FINANCE__DIM_*)
       - These are typically dimension/lookup tables that should be LEFT JOINed to the fact table
    3. IDENTIFY ADDITIONAL FACT TABLES:
       - Other fact tables (with "FACT" in name) that may need to be joined
       - These should also be LEFT JOINed to the primary fact table
    4. DETERMINE JOIN KEYS LOGICALLY:
       - For each table to join, analyze the mapping data to find join keys:
         a) Look for columns that map to the SAME target column from different source tables
         b) Look for columns with the same or similar names across tables (e.g., FUNCTIONAL_UNIT in fact table and FUNCTION_UNIT in dimension table)
         c) Look for logical foreign key relationships based on column names and descriptions
         d) Consider composite keys when multiple columns together form a unique relationship
    5. BUILD JOIN STRUCTURE:
       - Start FROM the primary fact table (use alias like 'r' for revenue/fact, 'f' for fact, etc.)
       - LEFT JOIN dimension tables first (use descriptive aliases like 'fu' for functional unit, 'mo' for managing office, etc.)
       - LEFT JOIN additional fact tables after dimensions
       - Use proper join conditions with AND clauses for composite keys
       - DO NOT use 1=1 joins - always find logical join keys based on column mappings
       - Format joins clearly with proper indentation
    6. JOIN CONDITION EXAMPLES:
       - Single key join: LEFT JOIN stg_dim_table d ON f.key_column = d.key_column
       - Composite key join: LEFT JOIN stg_dim_table d ON f.key1 = d.key1 AND f.key2 = d.key2
       - When column names differ but map to same target: LEFT JOIN stg_dim_table d ON f.source_col_name = d.dim_col_name
  * COLUMN CLEANING AND TRANSFORMATION LOGIC:
    - For each column, apply logic in this order:
    1. If "cleaning_logic" exists (e.g., TRIM, UPPER, COALESCE): 
       - Wrap the source column: e.g., TRIM(source_column)
    2. If "macros" exist:
       - Wrap the (cleaned) value in the macro call: e.g., {{ macro_name(cleaned_val) }}
    3. If "transformation_logic" exists:
       - If it's a snippet like "CASE WHEN...": Use it as the final SQL for that column.
       - If it's a function: Wrap the resulting expression once more.
  * CRITICAL TRANSFORMATION RULE LOGIC:
    1. Check ALL transformation_logic values for this target table
    2. If ANY transformation_logic contains aggregation keywords (GROUP BY, COUNT, SUM, AVG, MAX, MIN, DISTINCT COUNT):
       - Build the model in TWO PHASES:
         PHASE 1: Create CTEs with ALL joins and ALL target columns (as if no aggregation)
         PHASE 2: Apply aggregation in final SELECT using the transformation_logic
       - The final SELECT should ONLY contain:
         * Columns specified in GROUP BY clause from transformation_logic
         * Aggregated columns (COUNT, SUM, AVG, etc.) from transformation_logic
    3. If transformation_logic contains window keywords (OVER(, ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD, NTILE, etc.):
       - Build CTEs with ALL joins and base columns first
       - Apply the window function(s) ONLY in the final SELECT so that partitions reference the fully joined dataset
       - Include all PARTITION BY / ORDER BY clauses exactly as provided
    4. If transformation_logic contains only column-level functions (UPPER, LOWER, TRIM, etc.):
       - Apply these INLINE during column selection
  * Select ALL target columns from "target_columns" for that target table (mapped AND unmapped)
  * Map source columns to target columns per the mapping specifications
  * Cast columns to target data types when needed
  * Include column-level comments where descriptions are available
- Make SQL readable and maintainable
- IMPORTANT: When multiple source tables map to the same target table, include ALL their mapped columns in the SELECT statement, not just columns from the primary table

Return the complete project as JSON:
{{
  "dbt_project.yml": "...",
  "profiles.yml": "...",
  "packages.yml": "...",
  ".gitignore": "...",
  "models/sources.yml": "...",
  "models/schema.yml": "...",
  "models/staging/stg_<source_table_1>.sql": "...",
  "models/staging/stg_<source_table_2>.sql": "...",
  "models/silver/<target_table_1>.sql": "...",
  "macros/<macro_file>.sql": "..."
}}"""

    user_prompt = f"""Generate a complete dbt project for: '{safe_name}'

MAPPING DATA (Complete source-to-target mappings):
{json.dumps(mapping_data, indent=2)}

IMPORTANT: The mapping data includes a "multi_source_mappings" structure that identifies target columns 
with multiple source columns mapped to them. For these columns, use COALESCE to select the first non-null 
value from the sources (already ordered by priority/similarity). DO NOT select the same target column multiple times.

Instructions:
1. STAGING LAYER: Create one staging model (models/staging/stg_*.sql) per unique source table
   - CRITICAL: Look at the "source_tables" structure in the mapping data - it contains ALL source tables
   - You MUST create a staging model for EVERY table listed in "source_tables", regardless of whether it has 
   - Each staging model should select ALL columns from the source table
   - Apply basic data type casting and column name standardization
   - Use {{ source('{source_name}', 'table_name') }} for source references
   
2. SILVER LAYER: Create one silver model (models/silver/*.sql) per unique target table
   - CRITICAL: Create SEPARATE silver model files for EACH unique target table name
   - DO NOT combine multiple target tables into one model file
   - First, identify ALL unique target table names from the target_mappings structure AND target_columns structure
   - For EACH target table, create its own SQL file: models/silver/<target_table_name>.sql
   
   CONSOLIDATION & CLEANING LOGIC (Silver Layer):
   a) MULTI-SOURCE COLUMN CONSOLIDATION (CRITICAL):
      - Check the "multi_source_mappings" structure for target columns with multiple sources
      - For these columns, generate COALESCE logic to combine sources (merge_strategy will be COALESCE or empty):
        * Example: COALESCE(TRIM(source1.col1), TRIM(source2.col2)) AS target_column
        * Sources are already sorted by mapping_similarity (highest priority first)
        * Apply cleaning_logic to each source ONLY if it's specified and not empty
        * If cleaning_logic is empty/null, use the column as-is
        * Add a SQL comment documenting which sources are being combined
      - DO NOT select the same target column multiple times
   b) UNMAPPED TARGET COLUMNS (NULL POPULATION):
      - For each target column where populate_as_null = true:
        * Generate: CAST(NULL AS <data_type>) AS target_column
        * Add comment: -- No source mapping (populated as NULL)
        * This ensures the target table has all expected columns even without source data
        * These columns can be updated later through transformations or used for future mappings
   b) MULTI-TABLE CONSOLIDATION:
      - If multiple source tables map to the same target table:
        * Create a CTE for each source table (e.g., `cte_source1`, `cte_source2`)
        * In each CTE, select the mapped columns and apply `cleaning_logic` (TRIM, UPPER, etc.)
        * Use `UNION ALL` to combine these CTEs in the final SELECT
        * Ensure all unioned columns align in name, count, and data type
   c) CLEANING RULES:
      - Apply the `cleaning_logic` ONLY if it is specified and not empty.
      - If `cleaning_logic` is empty/null, use the column as-is without any cleaning.
      - If `cleaning_logic` is "TRIM", apply `TRIM(column_name)`.
      - If `cleaning_logic` is "UPPER", apply `UPPER(column_name)`.
      - If `cleaning_logic` is "UPPER", apply `UPPER(column_name)`.
      - If `cleaning_logic` contains a '?' (e.g., `COALESCE(?, 0)`), replace the '?' with the actual source column name.
      - If `cleaning_logic` is any other SQL snippet, use it as the expression for that column.
   c) STANDARD TRANSFORMATIONS:
      - Apply `transformation_logic` and cast to `target_data_type`.
      - Use provided `macros` where specified (e.g., `{{ standardize_phone(col) }}`).
   
   - Each silver model MUST select ALL target columns specified in the target_columns structure for that table.
   - For columns with populate_as_null = true, generate: CAST(NULL AS <data_type>) AS column_name -- No source mapping
   - Map source columns to target columns per the mapping specifications.
   - Use {{ ref('stg_*') }} to reference the staging (Bronze) models.
   
   EXAMPLE for handling unmapped columns:
   ```sql
   SELECT
       source_mapped_col AS target_col1,
       COALESCE(src1.col2, src2.col2) AS target_col2,
       CAST(NULL AS VARCHAR) AS target_col3, -- No source mapping (populated as NULL)
       CAST(NULL AS NUMBER) AS target_col4, -- No source mapping (populated as NULL)
       CAST(NULL AS TIMESTAMP) AS target_col5 -- No source mapping (populated as NULL)
   FROM {{ ref('stg_source_table') }}
   ```

3. MACROS: Implement the requested macros in `macros/cleaning_helpers.sql`.
4. DOCUMENTATION & DATA TESTS: Generate a comprehensive `models/schema.yml`.
   - For ALL staging and silver models, include column descriptions from the mapping data.
   - INTEGRATE DATA QUALITY TESTS:
     * Add `unique` and `not_null` tests for columns that appear to be primary keys (e.g., ID, *_ID, *_CODE, or columns described as identifiers).
     * Add `relationships` tests for foreign key columns where a clear parent-child relationship exists in the mapping (e.g., CUSTOMER_ID in a fact table referencing CUSTOMER_ID in a dim table).
     * Ensure test coverage is high but logical - avoid redundant tests on derived columns unless they are critical.
5. SOURCE DEFINITIONS: Generate `models/sources.yml` for all unique sources ensuring the name is '{source_name}'.

Generate the entire project now as a JSON object."""

    try:
        # Call Snowflake Cortex
        full_prompt = f"""{system_prompt}

{user_prompt}"""
        
        ai_content = cortex_complete(full_prompt)
        
        if not ai_content:
            raise ValueError("Empty response from AI")
        
        # Log response for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Cortex response length: {len(ai_content)} chars")
        logger.info(f"Cortex response preview (first 500 chars): {ai_content[:500]}")
        logger.info(f"Cortex response preview (last 200 chars): {ai_content[-200:]}")
        
        # Parse JSON response
        def _ensure_dict(parsed_value: Any) -> Dict[str, str] | None:
            """Normalize AI output into a JSON object."""
            if isinstance(parsed_value, dict):
                return parsed_value
            if isinstance(parsed_value, str):
                try:
                    nested = json.loads(parsed_value)
                    if isinstance(nested, dict):
                        return nested
                except json.JSONDecodeError:
                    return None
            return None

        try:
            files = json.loads(ai_content)
            files = _ensure_dict(files)
            if files:
                logger.info(f"Successfully parsed JSON directly. Keys: {list(files.keys())[:5]}")
        except json.JSONDecodeError as e:
            logger.warning(f"Direct JSON parse failed: {e}. Attempting to clean response...")
            files = _clean_json_response(ai_content)
            files = _ensure_dict(files)
            if files:
                logger.info(f"Successfully cleaned JSON. Keys: {list(files.keys())[:5]}")
        
        if not files or not isinstance(files, dict):
            logger.error(f"Invalid response format. Type: {type(files)}, Has content: {bool(files)}")
            logger.error(f"Full response:\n{ai_content}")
            raise ValueError("Invalid response format from AI - not a JSON object")
        
        # Validate required files exist
        required_files = ["dbt_project.yml", "profiles.yml", "packages.yml", ".gitignore"]
        missing = [f for f in required_files if f not in files]
        if missing:
            raise ValueError(f"Missing required files in AI response: {missing}")
        
        # Post-process: Ensure source references use correct format and config blocks
        for file_path, content in files.items():
            if file_path.endswith('.sql'):
                # Normalize source calls to use correct source_name
                # Pattern: {{ source('any_name', 'table_name') }} -> {{ source('source_name', 'table_name') }}
                def fix_source_ref(match):
                    try:
                        table_name = match.group(1).lower()
                        return "{{ source('" + source_name + "', '" + table_name + "') }}"
                    except (IndexError, AttributeError):
                        return match.group(0)
                
                files[file_path] = re.sub(
                    r"\{\{\s*source\s*\(\s*'[^']+'\s*,\s*'([^']+)'\s*\)\s*\}\}",
                    fix_source_ref,
                    content,
                    flags=re.IGNORECASE
                )
                
                # Ensure config block exists with appropriate materialization
                if "{{ config(" not in content and "config(" not in content:
                    # Staging models should be views, final models should be tables
                    if file_path.startswith("models/staging/"):
                        files[file_path] = "{{ config(materialized='view') }}\n\n" + files[file_path]
                    elif file_path.startswith("models/silver/"):
                        files[file_path] = "{{ config(materialized='table') }}\n\n" + files[file_path]
                    else:
                        # Default to table for backward compatibility
                        files[file_path] = "{{ config(materialized='table') }}\n\n" + files[file_path]
        
        return files
        
    except Exception as e:
        error_msg = f"AI generation failed: {str(e)}"
        print(error_msg)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=error_msg
        )


def _generate_project_files(mapping_rows: List[MappingRow], project_name: str) -> Dict[str, str]:
    """Generate all dbt project files using AI - single prompt approach"""
    if not mapping_rows:
        raise ValueError("No mapping rows provided")
    
    return _generate_complete_dbt_project_ai(mapping_rows, project_name)


def _background_generate_dbt(project_id: int):
    """Background job to generate dbt code"""
    db = SessionLocal()
    try:
        # Update status to GENERATING
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            print(f"Project {project_id} not found in background job")
            return
        
        print(f"Starting background DBT generation for project {project_id} ({project.project_name})")
        project.dbt_status = "GENERATING"
        db.commit()
        
        # Get mapping rows
        mapping_rows = db.query(MappingRow).filter(
            MappingRow.project_id == project_id
        ).all()
        
        if not mapping_rows:
            project.dbt_status = "FAILED"
            db.commit()
            return
        
        # Delete old dbt files if any
        db.query(DbtFile).filter(DbtFile.project_id == project_id).delete()
        
        # Generate dbt files using AI
        files = _generate_project_files(mapping_rows, project.project_name)
        
        # Store files in database
        for file_path, file_content in files.items():
            dbt_file = DbtFile(
                project_id=project_id,
                file_path=file_path,
                file_content=file_content
            )
            db.add(dbt_file)
        
        # Update status to COMPLETED
        project.dbt_status = "COMPLETED"
        db.commit()
        print(f"Project {project_id} generation COMPLETED successfully.")
        
    except Exception as exc:
        db.rollback()
        import traceback
        traceback.print_exc()
        
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.dbt_status = "FAILED"
            db.commit()
        print(f"Error generating dbt project: {str(exc)}")
    finally:
        db.close()


# ... existing upload_csv, get_all_projects, get_project_data, delete_project endpoints remain the same ...


@router.post(
    "/csv/projects/{project_id}/generate-dbt",
    response_model=StandardResponse[dict],
    summary="Generate dbt code from CSV project data",
    status_code=HTTPStatus.ACCEPTED
)
def generate_dbt_from_project(project_id: int):
    """
    Start generating dbt code from CSV mapping data stored for a project.
    This runs as a background thread. Check status using GET /csv/projects/{project_id}/dbt/status
    """
    db = next(get_db())
    try:
        # Check if project exists
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )
        
        # Check if generation is already in progress
        if project.dbt_status == "GENERATING":
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="DBT generation is already in progress for this project"
            )
        
        # Start background thread
        Thread(
            target=_background_generate_dbt,
            args=(project_id,),
            daemon=True
        ).start()
        
        return StandardResponse(
            success=True,
            payload={
                "message": "DBT generation started",
                "project_id": project_id,
                "status": "GENERATING"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Error starting dbt generation: {str(e)}"
        )
    finally:
        db.close()


@router.get(
    "/csv/projects/{project_id}/dbt/status",
    response_model=StandardResponse[dict],
    summary="Get dbt generation status for a project",
    status_code=HTTPStatus.OK
)
def get_dbt_status(project_id: int):
    """Get dbt generation status and file count"""
    db = next(get_db())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )
        
        files_count = db.query(DbtFile).filter(DbtFile.project_id == project_id).count()
        
        return StandardResponse(
            success=True,
            payload={
                "project_id": project_id,
                "status": project.dbt_status or "NOT_GENERATED",
                "files_count": files_count
            }
        )
    finally:
        db.close()


@router.get(
    "/csv/projects/{project_id}/dbt/download",
    summary="Download dbt project as zip file",
    status_code=HTTPStatus.OK
)
def download_dbt_project(project_id: int):
    """Download generated dbt project as a zip file"""
    db = next(get_db())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )
        
        if project.dbt_status != "COMPLETED":
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"DBT generation is not completed. Current status: {project.dbt_status}"
            )
        
        dbt_files = db.query(DbtFile).filter(DbtFile.project_id == project_id).all()
        
        if not dbt_files:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="No dbt files found for this project"
            )
        
        # Get safe project name for folder
        safe_name = _safe_name(project.project_name)
        
        # Create zip file in memory with project folder
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for dbt_file in dbt_files:
                # Prepend project folder name to file path
                zip_path = f"{safe_name}/{dbt_file.file_path}"
                zip_file.writestr(zip_path, dbt_file.file_content)
        
        zip_buffer.seek(0)
        
        filename = f"{safe_name}_dbt_project.zip"
        
        return StreamingResponse(
            BytesIO(zip_buffer.read()),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    finally:
        db.close()

@router.get(
    "/csv/projects/{project_id}/dbt/files/download",
    summary="Download a specific dbt file",
    status_code=HTTPStatus.OK
)
def download_dbt_file(project_id: int, file_path: str):
    """Download a specific dbt file by file path"""
    db = next(get_db())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )
        
        if project.dbt_status != "COMPLETED":
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"DBT generation is not completed. Current status: {project.dbt_status}"
            )
        
        # Find the specific file by file_path
        dbt_file = db.query(DbtFile).filter(
            DbtFile.project_id == project_id,
            DbtFile.file_path == file_path
        ).first()
        
        if not dbt_file:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"File with path '{file_path}' not found for this project"
            )
        
        # Determine content type based on file extension
        file_extension = Path(file_path).suffix.lower()
        content_types = {
            '.sql': 'text/plain',
            '.yml': 'text/yaml',
            '.yaml': 'text/yaml',
            '.md': 'text/markdown',
            '.txt': 'text/plain',
            '.json': 'application/json'
        }
        content_type = content_types.get(file_extension, 'application/octet-stream')
        
        # Get filename from path
        filename = Path(file_path).name
        
        return StreamingResponse(
            BytesIO(dbt_file.file_content.encode('utf-8')),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    finally:
        db.close()


@router.get(
    "/csv/projects/{project_id}/dbt/files",
    response_model=StandardResponse[List[DbtFileItem]],
    summary="Get all dbt files with content for a project",
    status_code=HTTPStatus.OK
)
def get_dbt_files(project_id: int):
    """Get all dbt files and their contents for a project"""
    db = next(get_db())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )
        
        if project.dbt_status != "COMPLETED":
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"DBT generation is not completed. Current status: {project.dbt_status}"
            )
        
        dbt_files = db.query(DbtFile).filter(DbtFile.project_id == project_id).all()
        
        if not dbt_files:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="No dbt files found for this project"
            )
        
        # Build list of file items with id, file_name, file_content, and file_path
        files_list = []
        for dbt_file in dbt_files:
            # Extract file_name from file_path
            file_name = Path(dbt_file.file_path).name
            files_list.append(DbtFileItem(
                id=dbt_file.id,
                file_name=file_name,
                file_content=dbt_file.file_content,
                file_path=dbt_file.file_path
            ))
        
        return StandardResponse(
            success=True,
            payload=files_list
        )
    finally:
        db.close()



@router.put(
    "/csv/projects/{project_id}/dbt/files/{file_id}",
    response_model=StandardResponse[DbtFileItem],
    summary="Update a dbt file content",
    status_code=HTTPStatus.OK
)
def update_dbt_file(project_id: int, file_id: int, payload: DbtFileUpdateRequest):
    """Update the content of a specific dbt file"""
    db = next(get_db())
    try:
        # Verify project exists
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )
        
        if project.dbt_status != "COMPLETED":
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"DBT generation is not completed. Current status: {project.dbt_status}"
            )
        
        # Find the specific file by file_id and project_id
        dbt_file = db.query(DbtFile).filter(
            DbtFile.id == file_id,
            DbtFile.project_id == project_id
        ).first()
        
        if not dbt_file:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"DBT file with ID {file_id} not found for project {project_id}"
            )
        
        # Update file content
        dbt_file.file_content = payload.file_content
        db.commit()
        db.refresh(dbt_file)
        
        # Build response
        file_name = Path(dbt_file.file_path).name
        updated_file = DbtFileItem(
            id=dbt_file.id,
            file_name=file_name,
            file_content=dbt_file.file_content,
            file_path=dbt_file.file_path
        )
        
        return StandardResponse(
            success=True,
            payload=updated_file
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Error updating dbt file: {str(e)}"
        )
    finally:
        db.close()

@router.get(
    "/csv/projects/{project_id}/dbt/final-files",
    response_model=StandardResponse[List[DbtFileItem]],
    summary="Get generated final-layer dbt models",
    status_code=HTTPStatus.OK
)
def get_final_dbt_files(project_id: int):
    """Return only the dbt files under models/final for a project."""
    db = next(get_db())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )

        if project.dbt_status != "COMPLETED":
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"DBT generation is not completed. Current status: {project.dbt_status}"
            )

        final_files = (
            db.query(DbtFile)
            .filter(
                DbtFile.project_id == project_id,
                DbtFile.file_path.like("models/final/%")
            )
            .all()
        )

        if not final_files:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="No final-layer dbt files found for this project"
            )

        payload = [
            DbtFileItem(
                file_name=Path(dbt_file.file_path).name,
                file_content=dbt_file.file_content,
                file_path=dbt_file.file_path
            )
            for dbt_file in final_files
        ]

        return StandardResponse(success=True, payload=payload)
    finally:
        db.close()


@router.post(
    "/csv/projects/{project_id}/dbt/files/{file_id}/ai-edit",
    response_model=StandardResponse[DbtFileItem],
    summary="AI-assisted edit of a dbt file",
    status_code=HTTPStatus.OK,
)
def edit_dbt_file_with_ai(project_id: int, file_id: int, request: DbtFileAIEditRequest):
    db = next(get_db())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(HTTPStatus.NOT_FOUND, f"Project {project_id} not found")
        if project.dbt_status != "COMPLETED":
            raise HTTPException(
                HTTPStatus.BAD_REQUEST,
                f"DBT generation is not completed. Current status: {project.dbt_status}",
            )

        dbt_file = (
            db.query(DbtFile)
            .filter(DbtFile.project_id == project_id, DbtFile.id == file_id)
            .first()
        )
        if not dbt_file:
            raise HTTPException(
                HTTPStatus.NOT_FOUND,
                f"DBT file with ID {file_id} not found for project {project_id}",
            )

        system_prompt = (
            "You are a senior dbt engineer. Update the provided dbt file according to the user request. "
            "Return ONLY the new file content (no explanations, no markdown)."
        )
        user_prompt = (
            f"File path: {dbt_file.file_path}\n"
            f"Current content:\n{dbt_file.file_content}\n\n"
            f"Change request:\n{request.prompt}"
        )

        # Call Snowflake Cortex
        full_prompt = f"""{system_prompt}

{user_prompt}"""
        
        new_content = cortex_complete(full_prompt).strip()
        if not new_content:
            raise HTTPException(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "AI returned empty content for edit request",
            )

        # Do NOT update dbt_file/file_content in the database.
        # Just send the AI output back to the caller.
        payload = DbtFileItem(
            id=dbt_file.id,
            file_name=Path(dbt_file.file_path).name,
            file_path=dbt_file.file_path,
            file_content=new_content,
        )
        return StandardResponse(success=True, payload=payload)

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"AI edit failed: {exc}",
        )
    finally:
        db.close()