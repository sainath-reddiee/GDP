"""Generate long descriptions for columns using profiling metadata.

Uses Snowflake AI_COMPLETE function for LLM-powered descriptions.
Falls back to template-based descriptions if Snowflake connection fails.
"""

import json
import argparse
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Running in SiS - no .env needed

from utils.connection import get_snowflake_connection, get_llm_model

# LLM model to use with AI_COMPLETE
LLM_MODEL = get_llm_model()

# Simple semantic hints based on column name patterns
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


def _stringify_values(values, limit=None):
    """Convert profiler value payloads (which may be dicts) into plain strings."""
    stringify = []
    for item in values or []:
        value = item
        if isinstance(item, dict):
            if 'value' in item:
                value = item['value']
            elif 'sample' in item:
                value = item['sample']
            elif 'text' in item:
                value = item['text']
            else:
                # Fallback to the first primitive field, otherwise json dump
                for _, candidate in item.items():
                    if isinstance(candidate, (str, int, float)):
                        value = candidate
                        break
                else:
                    value = json.dumps(item, ensure_ascii=False)
        stringify.append(str(value))
        if limit is not None and len(stringify) >= limit:
            break
    return stringify


def infer_semantic(col_name: str) -> str:
    """Infer semantic meaning from column name patterns."""
    upper = col_name.upper()
    for predicate, desc in SEMANTIC_HINTS:
        if predicate(upper):
            return desc
    return 'column in Silver layer entity; business meaning requires SME confirmation'


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def build_template_description(table: str, col: str, meta_datatype: str, prof: dict) -> str:
    """Build a template-based description without LLM."""
    parts = []
    parts.append(f"Table '{table}' column '{col}' ({meta_datatype})")
    parts.append(f"Total rows: {prof.get('total')} | Nulls: {prof.get('nulls')} ({round((prof.get('nulls')/prof.get('total'))*100,2) if prof.get('total') else 0}%). Distinct values: {prof.get('distinct')}.")
    top_vals = _stringify_values(prof.get('top_values'), limit=3)
    if top_vals:
        parts.append("Top examples: " + ", ".join(top_vals))
    samples = _stringify_values(prof.get('sample_values'))
    if samples:
        parts.append("Sample values: " + ", ".join(samples))
    rng = prof.get('range')
    if rng:
        parts.append(f"Observed numeric range: min={rng['min']}, max={rng['max']}")
    parts.append(f"Semantic hint: {infer_semantic(col)}")
    return " \n".join(parts)


def build_llm_prompt(table: str, col: str, meta_datatype: str, prof: dict) -> str:
    """Build the prompt for AI_COMPLETE."""
    return f"""You are a data modeling assistant. Craft a precise, business-friendly long description for the column below.
Focus: business analytics context; clarify metric vs dimension; mention granularity; avoid repetition.
Include: role, semantics, data type nuances, typical cardinality, and potential transformations for Silver layer.

Table: {table}
Column: {col}
Data Type: {meta_datatype}
Profile: {json.dumps(prof)}
Semantic Hint: {infer_semantic(col)}

Output: Single paragraph (<500 chars)."""


def call_snowflake_llm(prompt: str, conn) -> tuple:
    """Call Snowflake AI_COMPLETE function."""
    try:
        # Escape single quotes in prompt
        escaped_prompt = prompt.replace("'", "''")
        sql = f"SELECT AI_COMPLETE('{LLM_MODEL}', '{escaped_prompt}') AS response"

        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            if row and row[0]:
                return row[0], None
            return None, "Empty response from AI_COMPLETE"
    except Exception as e:
        return None, str(e)


def generate_descriptions(mode: str, metadata_path, profile_path, out_path,
                          metadata_dict=None, profiles_dict=None):
    """Generate descriptions for all columns.

    Supports two calling conventions:
      1. File-based (local dev): pass Path objects for metadata_path, profile_path, out_path.
      2. In-memory (SiS): pass metadata_dict and profiles_dict directly; out_path is ignored
         and the function returns the payload dict.
    """
    # Load data from files or use in-memory dicts
    if metadata_dict is not None and profiles_dict is not None:
        metadata = metadata_dict
        profiles = profiles_dict
    else:
        metadata = load_json(metadata_path)
        profiles = load_json(profile_path)

    results = {}
    failures = []

    # Establish Snowflake connection for LLM mode
    conn = None
    if mode == 'llm':
        try:
            conn = get_snowflake_connection()
            print(f"[generate_long_descriptions] Connected to Snowflake, using AI_COMPLETE with model '{LLM_MODEL}'")
        except Exception as e:
            print(f"[generate_long_descriptions] Snowflake connection failed: {e}")
            print("[generate_long_descriptions] Falling back to template mode")
            mode = 'template'

    for table, meta in metadata.items():
        table_short = table.split('.')[-1]
        prof_table = profiles.get(table_short) or profiles.get(table_short.upper())
        if not prof_table:
            continue
        table_result = {}

        for col in meta.get('columns', []):
            col_name = col['name']
            prof = prof_table.get(col_name)
            if not prof:
                continue

            if mode == 'template':
                desc = build_template_description(table_short, col_name, col['datatype'], prof)
                table_result[col_name] = desc
            elif mode == 'llm':
                prompt = build_llm_prompt(table_short, col_name, col['datatype'], prof)
                text, err = call_snowflake_llm(prompt, conn)
                if err or not text:
                    failures.append((col_name, err))
                    desc = build_template_description(table_short, col_name, col['datatype'], prof) + f" \nLLM fallback: {err}" if err else ''
                    table_result[col_name] = desc
                else:
                    table_result[col_name] = text.strip()
                    print(f"  Generated LLM description for {table_short}.{col_name}")
            else:
                raise ValueError("Mode must be 'template' or 'llm'")
        results[table_short] = table_result

    if conn:
        conn.close()

    payload = {
        'generated_at_utc': datetime.utcnow().isoformat() + 'Z',
        'mode': mode,
        'llm_model': LLM_MODEL if mode == 'llm' else None,
        'tables': results,
        'failures': failures,
    }

    # If in-memory mode (SiS), return payload directly instead of writing to disk
    if metadata_dict is not None:
        return payload

    # File-based mode: write to disk
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(f"[generate_long_descriptions] Descriptions ({mode}) written to: {out_path}")
    if failures:
        print(f"[generate_long_descriptions] LLM failures/fallback count: {len(failures)}")
    return payload


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description='Generate long descriptions for columns using profiling metadata.')
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parents[1]  # ascend from src/profiling to project root
    data_real = project_root / 'data' / 'real_run' / 'profile'
    default_meta = data_real / 'real_data_metadata.json'
    default_profile = data_real / 'column_profiles_real.json'
    default_out_template = data_real / 'column_long_descriptions_template.json'
    default_out_llm = data_real / 'column_long_descriptions_llm.json'

    parser.add_argument('-m', '--metadata', default=str(default_meta))
    parser.add_argument('-p', '--profile', default=str(default_profile))
    parser.add_argument('-o', '--output')
    parser.add_argument('--mode', choices=['template', 'llm'], required=True)
    args = parser.parse_args()

    if not args.output:
        args.output = str(default_out_template if args.mode == 'template' else default_out_llm)

    generate_descriptions(args.mode, Path(args.metadata), Path(args.profile), Path(args.output))


if __name__ == '__main__':
    main()
