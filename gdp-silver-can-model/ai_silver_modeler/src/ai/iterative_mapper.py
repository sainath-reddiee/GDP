"""Iterative Bronze-to-Silver Column Mapper with Structured JSON Output.

Uses Snowflake Cortex AI_COMPLETE with JSON schema for reliable structured output.
Implements iterative batch processing for scalable large-schema mapping.
"""

import json
import traceback
from copy import deepcopy
from typing import List, Dict, Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Running in SiS - no .env needed

from utils.connection import get_snowflake_connection, get_llm_model, get_embed_model

# Configuration
LLM_MODEL = get_llm_model()
EMBED_MODEL = get_embed_model()



# Batch sizes
TARGET_BATCH_SIZE = 5   # Number of target columns per batch
SOURCE_CHUNK_SIZE = 10  # Reduced from 15 to prevent truncation


# --- JSON SCHEMA FOR STRUCTURED OUTPUT ---

MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "mapped_columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_column_name": {"type": "string"},
                    "source_column_name": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_column_name": {"type": "string"},
                                "source_table_name": {"type": "string"},
                                "source_schema_name": {"type": "string"},
                                "justification": {"type": "string"}
                            }
                        }
                    },
                    "final_transformation_logic": {"type": "string"},
                    "work_notes": {"type": "string"}
                },
                "required": ["target_column_name", "source_column_name", "final_transformation_logic", "work_notes"]
            }
        }
    },
    "required": ["mapped_columns"]
}


# --- DESCRIPTION GENERATION ---

# --- SEMANTIC HINTS (from generate_long_descriptions.py) ---
SEMANTIC_HINTS = [
    (lambda n: n.endswith('_DT') or 'DATE' in n or 'TIMESTAMP' in n, 'timestamp of record ingestion or event occurrence'),
    (lambda n: 'YEAR' in n, 'calendar year for the financial measure or dimension context'),
    (lambda n: 'MONTH' in n, 'calendar month name (English) representing period granularity'),
    (lambda n: 'COUNTRY' in n, 'ISO country code identifying reporting geography'),
    (lambda n: 'CURRENCY' in n, 'three-letter currency code (ISO 4217)'),
    (lambda n: 'VALUE' in n and 'HASH' not in n, 'monetary amount; numeric financial measure'),
    (lambda n: 'MD5_HASH' in n or 'HASH' in n, 'MD5 hash fingerprint used for change detection and deduplication'),
    (lambda n: 'FUNCTION' in n, 'organizational functional unit classification'),
    (lambda n: 'MANAGING_OFFICE' in n, 'managing office location identifier'),
    (lambda n: 'LOB' in n or 'DIVISION' in n, 'line of business / hierarchical reporting segment'),
    (lambda n: 'CLIENT' in n, 'client identifier at given aggregation level'),
]

def infer_semantic(col_name: str) -> str:
    """Infer semantic meaning from column name patterns."""
    upper = col_name.upper()
    for predicate, desc in SEMANTIC_HINTS:
        if predicate(upper):
            return desc
    return 'business meaning requires SME confirmation'


# --- DESCRIPTION GENERATION ---

def generate_column_descriptions(profiles: Dict[str, Dict[str, Any]], schema_name: str, conn=None, **kwargs) -> Dict[str, Dict[str, str]]:
    """
    Generate semantic descriptions for all columns using LLM.
    
    Returns: {table: {column: "semantic description"}}
    """
    close_conn = False
    if conn is None:
        conn = get_snowflake_connection(**kwargs)
        close_conn = True
    
    descriptions = {}
    
    try:
        cursor = conn.cursor()
        
        for table, columns in profiles.items():
            if not isinstance(columns, dict):
                continue
            
            descriptions[table] = {}
            
            # Use small batches for descriptions to avoid context window / parsing issues
            col_names = list(columns.keys())
            batch_size = 10  # Smaller batch for more reliable parsing
            
            for i in range(0, len(col_names), batch_size):
                batch_cols = col_names[i:i + batch_size]
                col_info_list = []
                
                for col in batch_cols:
                    profile = columns.get(col, {})
                    if not isinstance(profile, dict):
                        profile = {}
                    
                    dtype = profile.get("datatype", "UNKNOWN")
                    
                    # Get sample values safely
                    sample = ""
                    top_vals = profile.get("top_values", [])[:3]
                    if top_vals:
                        samples = []
                        for v in top_vals:
                            val = v.get("value", v) if isinstance(v, dict) else v
                            # Clean and truncate sample value
                            clean_val = str(val).replace("'", "").replace('"', '').replace('\n', ' ')[:25]
                            samples.append(clean_val)
                        sample = ", ".join(samples)
                    
                    hint = infer_semantic(col)
                    col_info_list.append(f"  - {col} ({dtype}): hint='{hint}', samples=[{sample}]")
                
                # Use a clear multi-line prompt with explicit instructions
                prompt = f"""Generate a SHORT business description (under 60 chars) for each column.

Table: {schema_name}.{table}
Columns:
{chr(10).join(col_info_list)}

Return ONLY valid JSON with column names as keys and descriptions as values.
Example: {{"COLUMN_A": "Primary customer ID", "COLUMN_B": "Transaction date"}}

JSON:"""

                
                try:
                    escaped = prompt.replace("'", "''")
                    query = f"SELECT AI_COMPLETE('{LLM_MODEL}', '{escaped}') AS response"
                    cursor.execute(query)
                    raw = cursor.fetchone()[0]
                    
                    # Robust JSON extraction
                    def extract_and_parse_json(text):
                        if not text:
                            return None
                        
                        # Try parsing as-is first
                        try:
                            data = json.loads(text.strip())
                            if isinstance(data, dict):
                                return data
                            if isinstance(data, str):
                                # Recursive call for double-stringified
                                return extract_and_parse_json(data)
                        except:
                            pass
                            
                        # If that failed, try to find { ... }
                        try:
                            start = text.find('{')
                            end = text.rfind('}')
                            if start != -1 and end != -1:
                                cleaned = text[start:end+1]
                                data = json.loads(cleaned)
                                if isinstance(data, dict):
                                    return data
                                if isinstance(data, str):
                                    return extract_and_parse_json(data)
                        except:
                            pass
                        return None

                    batch_results = extract_and_parse_json(raw)
                    
                    if not batch_results or not isinstance(batch_results, dict):
                        print(f"[iterative_mapper] Warning: Could not parse JSON dict from response for {table}")
                        print(f"[iterative_mapper] Raw response snippet: {raw[:200]}")
                        batch_results = {}

                    
                    # Match results back to uppercase column names
                    col_map = {c.upper(): c for c in batch_cols}
                    matched_count = 0
                    for res_col, res_desc in batch_results.items():

                        upper_res = res_col.upper()
                        if upper_res in col_map:
                            descriptions[table][col_map[upper_res]] = res_desc
                            matched_count += 1
                        else:
                            # Try fuzzy match if key differs slightly
                            for actual_upper, actual_col in col_map.items():
                                if actual_upper in upper_res or upper_res in actual_upper:
                                    descriptions[table][actual_col] = res_desc
                                    matched_count += 1
                                    break
                    
                    # If no matches from LLM, use fallback for this batch
                    if matched_count == 0:
                        print(f"[iterative_mapper] No matches from LLM for {table} batch [{i}-{i+batch_size}], using fallback")
                        for col in batch_cols:
                            col_words = col.replace("_", " ").title()
                            descriptions[table][col] = col_words
                    
                except Exception as e:
                    print(f"[iterative_mapper] Description batch failed for {table} [{i}-{i+batch_size}]: {e}")
                    # Fallback for this batch
                    for col in batch_cols:
                        col_words = col.replace("_", " ").title()
                        descriptions[table][col] = col_words
            
            print(f"[iterative_mapper] Generated descriptions for {table}: {len(descriptions.get(table, {}))} columns")
            
        cursor.close()
        
    finally:
        if close_conn and conn:
            conn.close()
    
    return descriptions


def compute_embedding_similarity(source_descriptions: Dict, target_descriptions: Dict, conn=None, **kwargs) -> Dict[str, List[tuple]]:
    """
    Compute embedding similarity between source and target columns.
    
    Returns: {target_table.target_col: [(source_col, source_table, similarity_score), ...]}
    """
    close_conn = False
    if conn is None:
        conn = get_snowflake_connection(**kwargs)
        close_conn = True
    
    try:
        cursor = conn.cursor()
        
        # Build text lists
        source_texts = []  # (table, col, text)
        target_texts = []  # (table, col, text)
        
        for table, cols in source_descriptions.items():
            for col, desc in cols.items():
                # Use table name in embedding for context
                source_texts.append((table, col, f"Table {table}: Column {col} represents {desc}"))
        
        for table, cols in target_descriptions.items():
            for col, desc in cols.items():
                target_texts.append((table, col, f"Table {table}: Column {col} represents {desc}"))
        
        if not source_texts or not target_texts:
            return {}
        
        # Get embeddings using AI_EMBED (use project standard model)
        EMBED_MODEL = "e5-base-v2"
        
        def get_embeddings(texts):
            embeddings = []
            # Texts is a list of (table, col, text)
            full_texts = [t[2] for t in texts]
            
            for i in range(0, len(full_texts), 20):
                batch = full_texts[i:i+20]
                queries = []
                for idx, text in enumerate(batch):
                    escaped = text.replace("'", "''")[:1000] # Limit length
                    queries.append(f"SELECT {idx} AS idx, AI_EMBED('{EMBED_MODEL}', '{escaped}') AS emb")
                sql = " UNION ALL ".join(queries) + " ORDER BY idx"
                cursor.execute(sql)
                for row in cursor.fetchall():
                    emb = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                    embeddings.append(emb)
            return embeddings
        
        print(f"[iterative_mapper] Generating embeddings for {len(source_texts)} source, {len(target_texts)} target columns...")
        
        source_embeds = get_embeddings(source_texts)
        target_embeds = get_embeddings(target_texts)
        
        # Compute cosine similarity
        import numpy as np
        source_arr = np.array(source_embeds)
        target_arr = np.array(target_embeds)
        
        # Normalize
        source_norm = source_arr / np.linalg.norm(source_arr, axis=1, keepdims=True)
        target_norm = target_arr / np.linalg.norm(target_arr, axis=1, keepdims=True)
        
        # Similarity matrix: target x source
        similarity = np.dot(target_norm, source_norm.T)
        
        # For each target, get top 15 similar sources
        results = {}
        for t_idx, (t_table, t_col, _) in enumerate(target_texts):
            key = f"{t_table}.{t_col}"
            scores = similarity[t_idx]
            top_indices = np.argsort(scores)[::-1][:15]
            
            results[key] = []
            for s_idx in top_indices:
                s_table, s_col, _ = source_texts[s_idx]
                score = float(scores[s_idx])
                results[key].append((s_col, s_table, score))
        
        print(f"[iterative_mapper] Computed similarity matrix: {len(results)} targets")
        cursor.close()
        return results

        
    except Exception as e:
        print(f"[iterative_mapper] Embedding similarity failed: {e}")
        traceback.print_exc()
        return {}
    finally:
        if close_conn and conn:
            conn.close()


def convert_profiles_to_schema(profiles: Dict[str, Dict[str, Any]], schema_name: str, descriptions: Dict[str, Dict[str, str]] = None) -> List[Dict]:
    """
    Convert profile dict format to new schema JSON format.
    
    Input:  { table: { col: profile_dict } }
    Output: [{ table_name, schema_name, columns: [...] }]
    
    Args:
        profiles: Profile data
        schema_name: Name of the schema
        descriptions: Optional pre-generated semantic descriptions {table: {col: "description"}}
    """
    result = []
    for table, columns in profiles.items():
        if not isinstance(columns, dict):
            continue
        
        table_entry = {
            "table_name": table,
            "schema_name": schema_name,
            "description": "",
            "columns": []
        }

        
        for col, profile in columns.items():
            if not isinstance(profile, dict):
                # If profile is not a dict, create minimal entry
                table_entry["columns"].append({
                    "column_name": col,
                    "column_type": "UNKNOWN",
                    "sample_data": "",
                    "nullable": True,
                    "primary_key": False,
                    "description": f"Column {col}"
                })
                continue
            
            # Extract sample data from top_values
            sample_parts = []
            top_vals = profile.get("top_values", [])
            if isinstance(top_vals, list):
                for v in top_vals[:3]:
                    if isinstance(v, dict):
                        sample_parts.append(str(v.get("value", ""))[:20])
                    else:
                        sample_parts.append(str(v)[:20])
            sample_data = ", ".join(sample_parts)
            
            # Check if nullable
            nulls = profile.get("nulls", 0)
            nullable = nulls > 0
            
            # Detect potential primary key from naming
            is_pk = any(pk in col.upper() for pk in ["_ID", "_KEY", "_PK", "ID_"])
            
            # Get data type
            dtype = profile.get("datatype", "UNKNOWN")
            
            # Use pre-generated semantic description if available
            col_description = ""
            if descriptions and table in descriptions and col in descriptions[table]:
                col_description = descriptions[table][col]

            # Fallback to simple description from column name
            if not col_description:
                col_words = col.replace("_", " ").replace("-", " ").title()
                col_description = f"{col_words}"

            # PHASE 2: Include Deep AI Analysis fields if available
            col_entry = {
                "column_name": col,
                "column_type": dtype,
                "sample_data": sample_data,
                "nullable": nullable,
                "primary_key": is_pk,
                "description": col_description
            }

            # Add semantic_type and detailed_analysis from Deep AI Analysis (Phase 2)
            if 'semantic_type' in profile:
                col_entry['semantic_type'] = profile.get('semantic_type', '')
            if 'detailed_analysis' in profile:
                col_entry['detailed_analysis'] = profile.get('detailed_analysis', '')
            if 'quality_score' in profile:
                col_entry['quality_score'] = profile.get('quality_score', 0)

            table_entry["columns"].append(col_entry)


        
        result.append(table_entry)
    
    return result



def check_type_compatibility(source_type: str, target_type: str) -> bool:
    """
    Check if source and target data types are compatible for mapping.

    Returns:
        True if types are compatible, False otherwise
    """
    # Normalize types
    source_upper = source_type.upper()
    target_upper = target_type.upper()

    # Exact match
    if source_upper == target_upper:
        return True

    # Numeric type compatibility
    numeric_types = {"NUMBER", "FLOAT", "INT", "INTEGER", "DECIMAL", "DOUBLE", "REAL", "NUMERIC"}
    if any(source_upper.startswith(t) for t in numeric_types) and any(target_upper.startswith(t) for t in numeric_types):
        return True

    # String type compatibility
    string_types = {"VARCHAR", "CHAR", "STRING", "TEXT"}
    if any(source_upper.startswith(t) for t in string_types) and any(target_upper.startswith(t) for t in string_types):
        return True

    # Date/Time compatibility
    date_types = {"DATE", "TIMESTAMP", "DATETIME", "TIME"}
    if any(source_upper.startswith(t) for t in date_types) and any(target_upper.startswith(t) for t in date_types):
        return True

    # Boolean compatibility
    boolean_types = {"BOOLEAN", "BOOL"}
    if any(source_upper.startswith(t) for t in boolean_types) and any(target_upper.startswith(t) for t in boolean_types):
        return True

    # String can be converted to most types
    if any(source_upper.startswith(t) for t in string_types):
        return True  # Strings can be cast to anything

    return False


def filter_compatible_sources(target_col: Dict, source_cols: List[Dict], similarity_hints: Dict = None) -> List[Dict]:
    """
    Filter source columns to only include compatible candidates for a target column.

    Uses:
    - Data type compatibility
    - Embedding similarity scores (if available)
    - Semantic type matching

    Returns:
        List of compatible source columns sorted by relevance
    """
    target_name = target_col.get('column_name', '')
    target_type = target_col.get('column_type', '')
    target_semantic = target_col.get('semantic_type', '')
    target_table = target_col.get('target_table_name', '')

    candidates = []

    for source_col in source_cols:
        source_name = source_col.get('column_name', '')
        source_type = source_col.get('column_type', '')
        source_semantic = source_col.get('semantic_type', '')
        source_table = source_col.get('source_table_name', '')

        # Type compatibility check
        if not check_type_compatibility(source_type, target_type):
            continue  # Skip incompatible types

        # Calculate relevance score
        score = 0.0

        # 1. Semantic type match (highest priority)
        if source_semantic and target_semantic and source_semantic == target_semantic:
            score += 50.0

        # 2. Embedding similarity (if available)
        if similarity_hints:
            target_key = f"{target_table}.{target_name}"
            if target_key in similarity_hints:
                for s_col, s_table, sim_score in similarity_hints[target_key]:
                    if s_col == source_name and s_table == source_table:
                        score += sim_score * 30.0  # Similarity contributes up to 30 points
                        break

        # 3. Name similarity (simple fuzzy match)
        target_clean = target_name.upper().replace('_', '').replace('-', '')
        source_clean = source_name.upper().replace('_', '').replace('-', '')

        # Exact match after cleaning
        if target_clean == source_clean:
            score += 15.0
        # Contains match
        elif source_clean in target_clean or target_clean in source_clean:
            score += 10.0
        # Partial match (common words)
        else:
            target_words = set(target_name.upper().split('_'))
            source_words = set(source_name.upper().split('_'))
            common_words = target_words & source_words
            if common_words:
                score += 5.0 * len(common_words)

        # 4. Quality score bonus (if available)
        source_quality = source_col.get('quality_score', 0)
        if source_quality > 80:
            score += 5.0

        candidates.append({
            'column': source_col,
            'relevance_score': score
        })

    # Sort by relevance score (highest first)
    candidates.sort(key=lambda x: x['relevance_score'], reverse=True)

    # Return top 10 candidates
    return [c['column'] for c in candidates[:10]]


def _merge_source_entries(existing_sources: List[Dict[str, Any]], new_sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge two source mapping lists, preferring higher-confidence entries."""
    merged: Dict[tuple, Dict[str, Any]] = {}

    for src in existing_sources or []:
        key = (src.get("source_table_name"), src.get("source_column_name"))
        merged[key] = deepcopy(src)

    for src in new_sources or []:
        key = (src.get("source_table_name"), src.get("source_column_name"))
        if key in merged:
            existing = merged[key]
            existing_score = existing.get("mapping_score", 0)
            incoming_score = src.get("mapping_score", 0)

            # Prefer fields from the higher-confidence entry but keep context from both
            if incoming_score >= existing_score:
                for field, value in src.items():
                    if value not in (None, "", []):
                        existing[field] = value
        else:
            merged[key] = deepcopy(src)

    return list(merged.values())


def merge_mapping_states(existing_state: Optional[Dict[str, Any]], new_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Combine mapping states so later chunks cannot erase prior mappings."""
    if not existing_state:
        return deepcopy(new_state) if new_state else {"mapped_columns": []}

    if not new_state or "mapped_columns" not in new_state:
        return deepcopy(existing_state)

    existing_columns = existing_state.get("mapped_columns", []) or []
    merged_lookup: Dict[str, Dict[str, Any]] = {}

    for entry in existing_columns:
        target = entry.get("target_column_name")
        if not target:
            continue
        merged_lookup[target] = deepcopy(entry)

    for entry in new_state.get("mapped_columns", []) or []:
        target = entry.get("target_column_name")
        if not target:
            continue

        if target not in merged_lookup:
            merged_lookup[target] = deepcopy(entry)
            continue

        current = merged_lookup[target]
        current["source_column_name"] = _merge_source_entries(
            current.get("source_column_name", []),
            entry.get("source_column_name", [])
        )

        for field in ("final_transformation_logic", "work_notes", "target_description"):
            incoming_value = entry.get(field)
            if incoming_value:
                current[field] = incoming_value

    ordered_result = []
    seen = set()

    for entry in existing_columns:
        target = entry.get("target_column_name")
        if target and target in merged_lookup and target not in seen:
            ordered_result.append(merged_lookup[target])
            seen.add(target)

    for entry in new_state.get("mapped_columns", []) or []:
        target = entry.get("target_column_name")
        if target and target in merged_lookup and target not in seen:
            ordered_result.append(merged_lookup[target])
            seen.add(target)

    # Include any stragglers (should be none but keeps function safe)
    for target, entry in merged_lookup.items():
        if target not in seen:
            ordered_result.append(entry)

    return {"mapped_columns": ordered_result}


def generate_mapping_prompt(target_batch: List[Dict], source_chunk: List[Dict],
                           current_state: Optional[Dict], business_context: str = "",
                           similarity_hints: Dict = None) -> str:
    """Generate the LLM prompt for mapping (Phase 2: Target-Aware with Deep AI Analysis)."""

    # ENHANCEMENT: Pre-filter source columns for each target to reduce noise
    # Create a focused source chunk with only compatible candidates
    focused_source = []
    target_to_candidates = {}

    for target_col in target_batch:
        compatible = filter_compatible_sources(target_col, source_chunk, similarity_hints)
        target_name = target_col.get('column_name', '')
        target_to_candidates[target_name] = compatible
        focused_source.extend(compatible)

    # Deduplicate focused_source
    seen = set()
    unique_focused = []
    for col in focused_source:
        col_key = f"{col.get('source_table_name')}.{col.get('column_name')}"
        if col_key not in seen:
            seen.add(col_key)
            unique_focused.append(col)

    # If filtering reduced too much, fall back to original
    if len(unique_focused) < 3:
        unique_focused = source_chunk

    return f"""### ROLE
You are a Senior Data Engineer performing TARGET-DRIVEN MAPPING from Source (Bronze) to Target (Silver).
Your goal is to produce the BEST-FIT transformation that reconciles differences between source and target.

### PHASE 2 ENHANCEMENT: Deep AI Analysis Integration
The column metadata now includes SEMANTIC UNDERSTANDING from Deep AI Analysis:
- 'semantic_type': AI-inferred type (e.g., "physical_address", "email", "id", "date", "name")
- 'detailed_analysis': Deep analysis of data patterns, formats, and quality
- 'quality_score': Quality score (0-100) indicating data reliability

USE THIS SEMANTIC INFORMATION to make INTELLIGENT mappings beyond simple name matching.

### INPUT DATA
- TARGET BATCH (What we need to build): {json.dumps(target_batch, indent=2)}
- SOURCE CANDIDATES (Pre-filtered compatible sources): {json.dumps(unique_focused, indent=2)}
- PREVIOUS PROGRESS: {json.dumps(current_state, indent=2) if current_state else "Start of mapping."}
- BUSINESS CONTEXT: {business_context if business_context else "No specific context provided."}

**NOTE**: Source candidates have been PRE-FILTERED for type compatibility and semantic relevance. Focus on these high-confidence matches.

### MAPPING STRATEGY (PHASE 2):
1. **Semantic Matching First**: Match by semantic_type and detailed_analysis, NOT just column names
   - Example: "CUST_ADDR" (semantic_type: "physical_address") maps to "CUSTOMER_ADDRESS" (semantic_type: "physical_address")
   - Example: "EMAIL_ADDR" (semantic_type: "email") maps to "EMAIL_ADDRESS" (semantic_type: "email")

2. **Consider Data Quality**: Use quality_score to choose best source when multiple candidates exist
   - Prefer higher quality_score sources (>80) over lower quality ones

3. **Reconcile Differences**: Design transformation SQL that handles format differences
   - If source is "MM/DD/YYYY" and target is DATE type: Use TO_DATE() conversion
   - If source is "lowercase" and target is "UPPERCASE": Use UPPER() function
   - If source has "null placeholders" and target needs clean data: Use CASE/COALESCE

4. **Generate Best-Fit SQL**: Create Snowflake SQL that produces the exact target format
   - Handle type conversions, format standardization, unit conversion, key generation
   - Example: "TO_DATE(source_col, 'MM/DD/YYYY')" or "TRIM(UPPER(source_col))"

### CONCRETE MAPPING EXAMPLES:

**Example 1: Direct Match**
- Target: CUSTOMER_ID (NUMBER, semantic_type: "id", description: "Unique customer identifier")
- Source: CUST_ID (NUMBER, semantic_type: "id", description: "Customer ID from CRM")
- Mapping Score: 95 (Perfect semantic match, same type)
- Justification: "Both are unique customer identifiers with matching semantic types and compatible NUMBER types"
- Transformation: "CUST_ID" (direct mapping, no transformation needed)

**Example 2: Format Conversion**
- Target: ORDER_DATE (DATE, semantic_type: "date", description: "Order placement date")
- Source: ORD_DT (VARCHAR, semantic_type: "date", sample: "12/31/2023", description: "Order date as string")
- Mapping Score: 85 (Good semantic match, needs format conversion)
- Justification: "Both represent order dates; source is VARCHAR in MM/DD/YYYY format, target needs DATE type"
- Transformation: "TO_DATE(ORD_DT, 'MM/DD/YYYY')"

**Example 3: Standardization**
- Target: EMAIL_ADDRESS (VARCHAR, semantic_type: "email", description: "Customer email")
- Source: EMAIL (VARCHAR, semantic_type: "email", sample: "john@example.com", description: "Email address")
- Mapping Score: 90 (Perfect semantic match, needs standardization)
- Justification: "Both are email addresses; source may have inconsistent casing, target needs lowercase"
- Transformation: "LOWER(TRIM(EMAIL))"

**Example 4: Composite Mapping**
- Target: FULL_NAME (VARCHAR, semantic_type: "name", description: "Customer full name")
- Source 1: FIRST_NAME (VARCHAR, semantic_type: "name", description: "First name")
- Source 2: LAST_NAME (VARCHAR, semantic_type: "name", description: "Last name")
- Mapping Score: 80 (Requires combining two sources)
- Justification: "Target requires full name; can be composed from FIRST_NAME and LAST_NAME"
- Transformation: "TRIM(FIRST_NAME) || ' ' || TRIM(LAST_NAME)"
- Work Notes: "Composite key requires both FIRST_NAME and LAST_NAME from same table"

**Example 5: Type Casting**
- Target: TOTAL_AMOUNT (NUMBER(10,2), semantic_type: "numeric", description: "Total order amount")
- Source: AMOUNT (VARCHAR, semantic_type: "numeric", sample: "123.45", description: "Amount as string")
- Mapping Score: 75 (Semantic match but needs type conversion)
- Justification: "Both represent monetary amounts; source stored as VARCHAR, needs casting to NUMBER"
- Transformation: "CAST(AMOUNT AS NUMBER(10,2))"

### OUTPUT FIELD DEFINITIONS (How to fill the JSON):
1. 'target_column_name': The name of the target column currently being processed.
2. 'target_description': A SHORT semantic description of what this target column represents (e.g., "Primary key for customer dimension", "Customer's full legal name").
3. 'source_column_name' (Array): An array of ALL matching source columns from ANY source table. Each entry contains:
   - 'source_column_name': The exact name of the matching column in the source.
   - 'source_table_name': The table where the source column resides.
   - 'source_schema_name': The schema where the source table resides.
   - 'source_description': A SHORT semantic description of what this source column represents (e.g., "Unique customer identifier from CRM system").
   - 'mapping_score': A CONFIDENCE SCORE from 0-100 indicating mapping quality:
     * 90-100: Perfect semantic and format match
     * 70-89: Good semantic match, minor transformation needed
     * 50-69: Partial match, significant transformation needed
     * 0-49: Weak/uncertain match
   - 'justification': A brief technical reason explaining WHY this is a good match (mention semantic_type, format compatibility, quality_score if relevant).
4. 'final_transformation_logic':
   - Provide a COMPLETE Snowflake SQL expression that transforms source to target format
   - If a direct match with no transformation: "SOURCE_COLUMN"
   - If format conversion needed: e.g., "TO_DATE(SOURCE_COL, 'MM/DD/YYYY')"
   - If standardization needed: e.g., "TRIM(UPPER(FIRST_NAME)) || ' ' || TRIM(UPPER(LAST_NAME))"
   - If type casting needed: e.g., "CAST(SOURCE_COL AS NUMBER(10,2))"
   - If no match found yet: Return an empty string "".
5. 'work_notes':
   - Acts as a memory scratchpad.
   - Describe partial matches (e.g., "Found 1 of 2 columns for composite key, need LAST_NAME").
   - Note any format differences that need reconciliation (e.g., "Source is MM/DD/YYYY, target needs DATE type")
   - List missing pieces required to finalize 'final_transformation_logic'.

### LOGIC RULES:
- CRITICAL: Match by SEMANTIC MEANING (semantic_type, detailed_analysis), not just column names!
- If you find a matching source column in THIS CHUNK, ADD IT to the 'source_column_name' array.
- KEEP ALL EXISTING SOURCES from 'PREVIOUS PROGRESS' - do NOT remove sources from other tables.
- A target may have MULTIPLE valid source mappings from DIFFERENT tables. Include ALL of them.
- Only replace a source mapping if the new one is from the SAME table but is a better match.
- When multiple sources match, prefer the one with HIGHER quality_score.
- Return a valid JSON object matching the schema.
- IMPORTANT: Always provide source_description and mapping_score for each mapping.
- TRANSFORMATION SQL must be valid Snowflake syntax and handle ALL format differences.
"""





def call_cortex_structured(prompt: str, conn=None) -> Optional[Dict]:
    """Call Snowflake Cortex and parse JSON response."""
    close_conn = False
    if conn is None:
        conn = get_snowflake_connection()
        close_conn = True
    
    try:
        cursor = conn.cursor()
        try:
            # Append JSON instruction to prompt
            full_prompt = prompt + """

IMPORTANT: Return ONLY a valid JSON object with no additional text, markdown, or code blocks.
The JSON must have this exact structure:
{
  "mapped_columns": [
    {
      "target_column_name": "string",
      "source_column_name": [
        {"source_column_name": "string", "source_table_name": "string", "source_schema_name": "string", "justification": "string"}
      ],
      "final_transformation_logic": "string",
      "work_notes": "string"
    }
  ]
}
"""
            
            # Escape single quotes for SQL
            escaped_prompt = full_prompt.replace("'", "''")
            
            # Use simple AI_COMPLETE call
            query = f"SELECT AI_COMPLETE('{LLM_MODEL}', '{escaped_prompt}') AS response"
            cursor.execute(query)
            raw_response = cursor.fetchone()[0]
            
            print(f"[iterative_mapper] Cortex returned {len(raw_response)} chars")
            
            # Extract JSON block robustly
            content = raw_response.strip()
            
            # Remove BOM if present
            if content.startswith('\ufeff'):
                content = content[1:]
            
            # Remove markdown code blocks if present
            if content.startswith('```'):
                content = content.split('```', 2)[1]
                if content.startswith('json'):
                    content = content[4:]
                content = content.strip()
            
            # Find JSON boundaries
            start = content.find('{')
            end = content.rfind('}')
            
            if start != -1 and end != -1:
                content = content[start:end+1]
            
            # CRITICAL FIX: Handle escaped characters from Cortex
            # Cortex sometimes returns JSON with escaped quotes and newlines
            content = content.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
            # MOST IMPORTANT: Unescape quotes - Cortex returns \" instead of "
            content = content.replace('\\"', '"')
            
            # Try to parse JSON - handle multiple formats
            result = None
            try:
                result = json.loads(content)
                
                # Handle double-stringified JSON
                parse_attempts = 0
                while isinstance(result, str) and parse_attempts < 3:
                    result = json.loads(result)
                    parse_attempts += 1
                    
            except json.JSONDecodeError as e:
                print(f"[iterative_mapper] JSON parse error: {e}")
                # Try one more time with aggressive cleaning
                try:
                    # Remove all control characters except newlines, tabs, carriage returns
                    import re
                    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', content)
                    result = json.loads(cleaned)
                    print(f"[iterative_mapper] Successfully parsed after cleaning control characters")
                except:
                    # Log a larger snippet for debugging if it fails
                    print(f"[iterative_mapper] Content length: {len(content)}, First 300 chars: {content[:300]}")
                    print(f"[iterative_mapper] Last 200 chars: {content[-200:] if len(content) > 200 else 'N/A'}")
                    # Show hex of first few characters for debugging
                    hex_start = ' '.join(f'{ord(c):02x}' for c in content[:20])
                    print(f"[iterative_mapper] First 20 chars hex: {hex_start}")
                    return None
            
            # Validate structure
            if isinstance(result, dict) and "mapped_columns" in result:
                return result
            else:
                print(f"[iterative_mapper] Invalid response structure: {type(result)}")
                return None

            
        except Exception as e:
            print(f"[iterative_mapper] Cortex call error: {e}")
            traceback.print_exc()
            return None
        finally:
            cursor.close()
    finally:
        if close_conn and conn:
            conn.close()


def run_iterative_mapping(
    source_profiles: Dict[str, Dict[str, Any]],
    target_profiles: Dict[str, Dict[str, Any]],
    source_schema_name: str = "BRONZE",
    target_schema_name: str = "SILVER",
    business_context: str = "",
    progress_callback=None,
    source_descriptions: Dict[str, Dict[str, str]] = None,
    target_descriptions: Dict[str, Dict[str, str]] = None,
    similarity_hints: Dict[str, List[tuple]] = None,
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Run iterative batch-based mapping from source to target.
    
    Args:
        source_profiles: Bronze profiles {table: {col: profile}}
        target_profiles: Silver profiles {table: {col: profile}}
        source_schema_name: Name of the source schema
        target_schema_name: Name of the target schema
        business_context: User-provided mapping hints
        progress_callback: Optional callback for progress updates (current, total, message)
        source_descriptions: Pre-generated semantic descriptions for source columns
        target_descriptions: Pre-generated semantic descriptions for target columns
        similarity_hints: Embedding similarity results {target: [(source, table, score), ...]}

    
    Returns:
        List of mapping dictionaries
    """
    print(f"[iterative_mapper] === STARTING ITERATIVE MAPPING ===")
    
    # Convert to new schema format - use pre-generated descriptions if available
    source_data = convert_profiles_to_schema(source_profiles, source_schema_name, source_descriptions)
    target_data = convert_profiles_to_schema(target_profiles, target_schema_name, target_descriptions)

    
    print(f"[iterative_mapper] Source: {len(source_data)} tables")
    print(f"[iterative_mapper] Target: {len(target_data)} tables")
    
    all_results = []
    total_target_cols = sum(len(t.get("columns", [])) for t in target_data)
    processed_cols = 0
    
    # Get connection once for all calls
    conn = kwargs.get("connection") or get_snowflake_connection(**kwargs)
    
    try:
        for target_table in target_data:
            t_table_name = target_table.get("table_name", "UNKNOWN")
            t_cols = target_table.get("columns", [])
            
            print(f"[iterative_mapper] Processing target table: {t_table_name} ({len(t_cols)} columns)")
            
            # Process target columns in batches
            for i in range(0, len(t_cols), TARGET_BATCH_SIZE):
                target_batch = t_cols[i:i + TARGET_BATCH_SIZE]
                
                # Add target table context to each column
                for col in target_batch:
                    col["target_table_name"] = t_table_name
                    col["target_schema_name"] = target_schema_name
                
                # Initialize state for this batch
                current_state = {
                    "mapped_columns": [
                        {
                            "target_column_name": c["column_name"],
                            "source_column_name": [],
                            "final_transformation_logic": "",
                            "work_notes": "Awaiting initial scan."
                        } for c in target_batch
                    ]
                }
                
                # Scan all source tables
                for source_table in source_data:
                    s_table_name = source_table.get("table_name", "UNKNOWN")
                    s_cols = source_table.get("columns", [])
                    
                    # Process source columns in chunks
                    for j in range(0, len(s_cols), SOURCE_CHUNK_SIZE):
                        source_chunk = s_cols[j:j + SOURCE_CHUNK_SIZE]
                        
                        # Add source table context to each column
                        for col in source_chunk:
                            col["source_table_name"] = s_table_name
                            col["source_schema_name"] = source_schema_name
                        
                        # Generate prompt and call LLM (with similarity hints for better accuracy)
                        prompt = generate_mapping_prompt(
                            target_batch,
                            source_chunk,
                            current_state,
                            business_context,
                            similarity_hints=similarity_hints
                        )
                        
                        new_state = call_cortex_structured(prompt, conn)
                        
                        if new_state and "mapped_columns" in new_state:
                            current_state = merge_mapping_states(current_state, new_state)
                            print(f"[iterative_mapper] Merged state from {s_table_name}")
                        else:
                            print(f"[iterative_mapper] No valid response for {s_table_name} chunk")
                
                # Add completed mappings to results
                for mapping in current_state.get("mapped_columns", []):
                    # Get target column description from target_batch
                    target_col_name = mapping.get("target_column_name", "")
                    target_desc = ""
                    target_dtype = ""
                    for tc in target_batch:
                        if tc.get("column_name") == target_col_name:
                            target_desc = tc.get("description", "")
                            target_dtype = tc.get("column_type", "")
                            break
                    
                    # Convert to flat output format for CSV
                    # Get target description from LLM response (semantic), fallback to profile
                    target_desc_llm = mapping.get("target_description", "")
                    
                    base_mapping = {
                        "TargetTable": t_table_name,
                        "TargetSchema": target_schema_name,
                        "TargetColumn": target_col_name,
                        "TargetDataType": target_dtype,
                        "TargetDescription": target_desc_llm if target_desc_llm else target_desc,
                        "TransformationLogic": mapping.get("final_transformation_logic", ""),
                        "WorkNotes": mapping.get("work_notes", "")
                    }
                    
                    sources = mapping.get("source_column_name", [])
                    if sources:
                        # Deduplicate sources based on table and column name
                        seen_sources = set()
                        for src in sources:
                            src_table = src.get("source_table_name", "UNKNOWN")
                            src_col = src.get("source_column_name", "UNKNOWN")
                            source_key = f"{src_table}.{src_col}"
                            
                            if source_key in seen_sources or src_col == "UNMAPPED":
                                continue
                            seen_sources.add(source_key)
                            
                            # Get LLM-generated semantic description and score
                            source_desc_llm = src.get("source_description", "")
                            mapping_score = src.get("mapping_score", 0)
                            
                            # Fallback: Lookup source data type from source_data
                            source_dtype = ""
                            for s_tbl in source_data:
                                if s_tbl.get("table_name") == src_table:
                                    for s_col in s_tbl.get("columns", []):
                                        if s_col.get("column_name") == src_col:
                                            source_dtype = s_col.get("column_type", "")
                                            break
                                    break
                            
                            all_results.append({
                                **base_mapping,
                                "SourceTable": src_table,
                                "SourceSchema": src.get("source_schema_name", ""),
                                "SourceColumn": src_col,
                                "SourceDataType": source_dtype,
                                "SourceDescription": source_desc_llm,
                                "MappingScore": mapping_score,
                                "Justification": src.get("justification", "")
                            })
                    
                    # If after deduplication we have no valid sources, add one N/A row
                    if not any(r["TargetColumn"] == target_col_name and r["SourceTable"] != "N/A" for r in all_results if r["TargetColumn"] == target_col_name):
                        # check if we already added a row for this target_col_name
                        added_for_this = [r for r in all_results if r["TargetColumn"] == target_col_name and r["TargetTable"] == t_table_name]
                        if not added_for_this:
                            all_results.append({
                                **base_mapping,
                                "SourceTable": "N/A",
                                "SourceSchema": "N/A",
                                "SourceColumn": "N/A",
                                "SourceDataType": "N/A",
                                "SourceDescription": "No suitable source column found in current scan.",
                                "MappingScore": 0,
                                "Justification": "No matching source column found"
                            })



                
                processed_cols += len(target_batch)
                
                if progress_callback:
                    progress_callback(processed_cols, total_target_cols, f"Mapped {processed_cols}/{total_target_cols} columns")
    
    except Exception as e:
        print(f"[iterative_mapper] Error: {e}")
        traceback.print_exc()
    finally:
        conn.close()
    
    print(f"[iterative_mapper] === COMPLETED: {len(all_results)} mappings ===")

    # STEP 4: Apply learned patterns from user corrections
    try:
        from ai.learning_engine import get_learned_patterns, apply_learned_patterns

        learned_patterns = get_learned_patterns()
        if learned_patterns:
            print(f"[iterative_mapper] Applying {len(learned_patterns)} learned patterns")
            all_results = apply_learned_patterns(all_results, learned_patterns)
        else:
            print(f"[iterative_mapper] No learned patterns available yet")
    except Exception as e:
        print(f"[iterative_mapper] Learning engine failed (non-critical): {e}")
        # Learning failure should not break the mapping workflow

    return all_results


if __name__ == "__main__":
    print("Iterative Mapper module loaded successfully.")
    print(f"Config: TARGET_BATCH={TARGET_BATCH_SIZE}, SOURCE_CHUNK={SOURCE_CHUNK_SIZE}, MODEL={LLM_MODEL}")
