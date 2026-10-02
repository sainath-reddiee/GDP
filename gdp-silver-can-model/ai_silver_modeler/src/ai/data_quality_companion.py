"""AI Data Quality Companion for Deep Column Profiling.

Uses Snowflake Cortex AI_COMPLETE to provide semantic analysis, quality scoring,
and executable Python code generation for advanced profiling operations.
"""

import os
import json
import re
import tempfile
import traceback
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Running in SiS - no .env needed

from utils.connection import get_snowflake_connection as _shared_get_connection, get_llm_model

# LLM Model configuration
LLM_MODEL = get_llm_model()


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
    (lambda n: 'ADDRESS' in n or 'STREET' in n, 'physical or mailing address information'),
    (lambda n: 'EMAIL' in n, 'email address contact information'),
    (lambda n: 'PHONE' in n or 'TEL' in n, 'telephone or phone number'),
    (lambda n: 'NAME' in n, 'entity name or identifier'),
    (lambda n: '_ID' in n or 'KEY' in n, 'unique identifier or foreign key'),
]


def infer_semantic(col_name: str) -> str:
    """Infer semantic meaning from column name using pattern matching."""
    col_upper = col_name.upper()
    for check, hint in SEMANTIC_HINTS:
        if check(col_upper):
            return hint
    return "business data field"


def get_snowflake_connection(**kwargs):
    """Create a Snowflake connection using shared utility."""
    return _shared_get_connection(**kwargs)


class DataQualityCompanion:
    """AI-powered data quality analyst companion."""

    def __init__(self, connection=None, **kwargs):
        """
        Initialize the companion.

        Args:
            connection: Optional Snowflake connection
            **kwargs: Explicit connection parameters if connection is not provided
        """
        # Log connection attempt (safely)
        account = kwargs.get("account") or os.getenv("SNOWFLAKE_ACCOUNT")
        user = kwargs.get("user") or os.getenv("SNOWFLAKE_USER")
        print(f"[ai_companion] Initializing with account={account}, user={user}")
        
        try:
            self.conn = connection or get_snowflake_connection(**kwargs)
            self.owns_connection = connection is None
            self.llm_model = LLM_MODEL
            print(f"[ai_companion] Connection established successfully")
        except Exception as e:
            print(f"[ai_companion] FAILED to establish connection: {e}")
            raise e

    def __del__(self):
        """Clean up connection if we own it."""
        if self.owns_connection and self.conn:
            try:
                self.conn.close()
            except:
                pass

    def analyze_column(
        self,
        table_name: str,
        column_name: str,
        profile: Dict[str, Any],
        description: str = "",
        business_context: str = "",
        schema_name: str = None,
        database_name: str = None
    ) -> Dict[str, Any]:
        """
        Perform deep AI analysis of a single column.

        Args:
            table_name: Name of the table
            column_name: Name of the column
            profile: Profile dict from profile_real_data.py
            description: Optional pre-generated description
            business_context: Optional business context from user
            schema_name: Optional schema name (defaults to env var)

        Returns:
            Analysis result dictionary with keys:
            - analysis_text: Markdown analysis
            - column_type_inference: Semantic type (address, email, id, etc.)
            - quality_issues: List of {issue, severity, affected_pct}
            - quality_score: 0-100 score
            - recommendations: List of actionable recommendations
            - generated_code: Executable Python profiling code
            - histogram_path: Path to generated histogram image (empty if not generated)
        """
        print(f"[ai_companion] Analyzing {table_name}.{column_name}...")

        # Fetch sample data
        sample_data = self._fetch_sample_data(table_name, column_name, schema_name, database_name, limit=10)

        # Build enriched context
        context = self._build_analysis_context(
            table_name, column_name, profile, description,
            business_context, sample_data
        )

        # Call AI_COMPLETE for analysis (V4)
        ai_response = self._call_ai_complete_for_analysis_v4(context)

        if not ai_response:
            # Fallback to rule-based analysis (still generates professional histograms)
            result = self._fallback_analysis(table_name, column_name, profile)
            professional_code = self._get_professional_histogram_code()
            result["generated_code"] = professional_code
            histogram_path = self._generate_histogram(
                table_name, column_name, professional_code,
                sample_data, schema_name, database_name
            )
            result["histogram_path"] = histogram_path
            return result

        # Compute quality score
        quality_score = self._compute_quality_score(profile, ai_response)

        # Prepare result
        result = {
            "analysis_text": ai_response.get("analysis", ""),
            "column_type_inference": ai_response.get("semantic_type", "unknown"),
            "quality_issues": ai_response.get("issues", []),
            "quality_score": quality_score,
            "recommendations": ai_response.get("recommendations", []),
            "transformation_logic": ai_response.get("transformation_logic", ""),
            "remediation_sql": ai_response.get("remediation_sql", ""),
            "generated_code": "",
            "histogram_path": ""
        }

        # Always use our pre-built professional histogram template
        # (LLM-generated code was unreliable: truncation, f-strings, syntax errors)
        professional_code = self._get_professional_histogram_code()
        result["generated_code"] = professional_code

        histogram_path = self._generate_histogram(
            table_name, column_name, professional_code,
            sample_data, schema_name, database_name
        )
        result["histogram_path"] = histogram_path

        return result

    def _fetch_sample_data(
        self,
        table_name: str,
        column_name: str,
        schema_name: str = None,
        database_name: str = None,
        limit: int = 10
    ) -> List[Any]:
        """Fetch sample data from the column."""
        schema = schema_name or (os.getenv("SNOWFLAKE_SCHEMA") or "PUBLIC").upper()
        database = database_name or (os.getenv("SNOWFLAKE_DATABASE") or "").upper()
        
        if database:
            full_table = f'"{database}"."{schema}"."{table_name.upper()}"'
        else:
            full_table = f'"{schema}"."{table_name.upper()}"'
            
        col_ref = f'"{column_name.upper()}"'

        sql = f"""
            SELECT {col_ref}
            FROM {full_table}
            WHERE {col_ref} IS NOT NULL
            LIMIT {limit}
        """

        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                return [row[0] for row in cur.fetchall()]
        except Exception as e:
            print(f"[ai_companion] Error fetching sample data from {full_table}: {e}")
            return []

    def _build_analysis_context(
        self,
        table_name: str,
        column_name: str,
        profile: Dict[str, Any],
        description: str,
        business_context: str,
        sample_data: List[Any]
    ) -> str:
        """Build enriched context for AI analysis."""

        # Extract profile metrics
        dtype = profile.get("datatype", "UNKNOWN")
        total = profile.get("total", 0)
        nulls = profile.get("nulls", 0)
        distinct = profile.get("distinct", 0)
        top_values = profile.get("top_values", [])
        length_range = profile.get("length_range", {})

        # Calculate percentages
        null_pct = (nulls / total * 100) if total > 0 else 0
        completeness = 100 - null_pct

        # Format top values as markdown table
        top_values_md = self._format_top_values_markdown(top_values)

        # Format sample data as markdown table
        sample_data_md = self._format_sample_data_markdown(column_name, sample_data)

        # Build length info
        length_info = ""
        if length_range and 'min' in length_range:
            length_info = f"\n- Length Range: {length_range['min']}-{length_range['max']} characters"

        # Semantic hint
        semantic_hint = infer_semantic(column_name)

        # Build context prompt (histogram code is handled separately by _get_professional_histogram_code)
        context = f"""You are a senior data quality analyst evaluating column quality for a data warehouse.

**Column:** {table_name}.{column_name}
**Data Type:** {dtype}
**Description:** {description or semantic_hint}
**Business Context:** {business_context or "Not provided"}

**Statistics:**
- Total Records: {total:,}
- Nulls: {nulls:,} ({null_pct:.1f}%)
- Completeness: {completeness:.1f}%
- Distinct Values: {distinct:,}{length_info}

**Top Values (by frequency):**
{top_values_md}

**Sample Data (10 rows):**
{sample_data_md}

**Your Tasks:**
1. **Semantic Understanding**: What does this column represent? What is its purpose? (1-2 sentences)
2. **Data Quality Issues**: Identify format inconsistencies, patterns, anomalies. For each issue provide:
   - Issue description (be specific)
   - Severity: "high" (critical), "medium" (moderate impact), or "low" (minor)
   - Estimated percentage of records affected

   Examples of quality issues to look for:
   - Inconsistent capitalization (ALL CAPS, lowercase, Title Case)
   - Missing components (e.g., address missing street number)
   - Invalid formats (e.g., dates in wrong format, malformed emails)
   - Special characters or encoding issues
   - Outliers or unexpected values
   - Pattern inconsistencies

3. **Remediation & Transformation**: Provide actionable logic for the Silver layer:
   - **Transformation Logic**: Narratively explain the best-fit transformation strategy (reasoning).
   - **Remediation SQL**: Provide PRODUCTION-READY, EXECUTABLE Snowflake SQL (not placeholders or TODOs):

     **Requirements:**
     * Use proper error handling (TRY_CAST, TRY_TO_DATE, TRY_TO_NUMBER)
     * Handle NULL values explicitly (COALESCE, IFNULL, CASE WHEN)
     * Apply appropriate data cleaning (TRIM, UPPER/LOWER, REGEXP_REPLACE)
     * Validate data formats and patterns
     * Include meaningful defaults for edge cases
     * Write efficient, optimized queries

     **Common Patterns by Data Type:**
     * **Dates**: TRY_TO_DATE(col, 'YYYY-MM-DD'), COALESCE(TRY_TO_TIMESTAMP(col, 'MM/DD/YYYY HH24:MI:SS'), CURRENT_TIMESTAMP)
     * **Emails**: LOWER(TRIM(REGEXP_REPLACE(col, '[^a-zA-Z0-9@._-]', '')))
     * **Phone**: REGEXP_REPLACE(col, '[^0-9]', '')
     * **Text/Names**: INITCAP(TRIM(REGEXP_REPLACE(col, '\\\\s+', ' ')))
     * **Addresses**: Clean with TRIM(UPPER(REGEXP_REPLACE(col, '[^A-Za-z0-9\\\\s,.-]', '')))
     * **IDs/Keys**: Validate length, check patterns, ensure uniqueness
     * **Amounts**: TRY_TO_NUMBER(REGEXP_REPLACE(col, '[^0-9.-]', ''), 10, 2)

**Output Format (STRICT JSON ONLY, NO MARKDOWN OR PRE-TEXT):**
{{
  "semantic_type": "physical_address|email|id|date|name|numeric|text|...",
  "analysis": "This column contains... [Detailed 2-3 sentences about patterns and business intent]",
  "issues": [
    {{"issue": "Specific issue name", "severity": "high|medium|low", "affected_pct": 10.5}}
  ],
  "recommendations": [
    "Transformation recommendation 1",
    "Standardization step 2"
  ],
  "transformation_logic": "Explain the transformation strategy here.",
  "remediation_sql": "SELECT CASE WHEN TRIM(col) IS NULL THEN 'UNKNOWN' ELSE TRIM(col) END as cleaned_col FROM source_table"
}}
"""

        return context

    def _format_top_values_markdown(self, top_values: List[Dict]) -> str:
        """Format top values as markdown table."""
        if not top_values:
            return "*(No top values available)*"

        lines = ["| Value | Count |", "|-------|-------|"]
        for item in top_values[:10]:
            value = str(item.get('value', ''))
            # Truncate long values
            if len(value) > 50:
                value = value[:47] + "..."
            # Escape pipe characters
            value = value.replace('|', '\\|')
            count = item.get('count', 0)
            lines.append(f"| {value} | {count:,} |")

        return "\n".join(lines)

    def _format_sample_data_markdown(self, column_name: str, sample_data: List[Any]) -> str:
        """Format sample data as markdown table."""
        if not sample_data:
            return "*(No sample data available)*"

        lines = [f"| {column_name} |", "|--------|"]
        for value in sample_data[:10]:
            value_str = str(value)
            # Truncate long values
            if len(value_str) > 100:
                value_str = value_str[:97] + "..."
            # Escape pipe characters
            value_str = value_str.replace('|', '\\|')
            lines.append(f"| {value_str} |")

        return "\n".join(lines)

    def _call_ai_complete_for_analysis_v4(self, context: str) -> Optional[Dict[str, Any]]:
        """Call Snowflake AI_COMPLETE for analysis (V4)."""

        # Escape single quotes in the prompt
        escaped_prompt = context.replace("'", "''")

        # AI_COMPLETE call
        try:
            with self.conn.cursor() as cur:
                sql = f"SELECT AI_COMPLETE('{self.llm_model}', '{escaped_prompt}') AS response"
                print(f"[ai_companion] [V4] Calling Cortex LLM ({self.llm_model})...")
                cur.execute(sql)
                raw_response = cur.fetchone()[0]
                print(f"[ai_companion] [V4] Received response ({len(raw_response) if raw_response else 0} chars)")

                if not raw_response:
                    print(f"[ai_companion] [V4] Empty response")
                    return None

                # Parse JSON response
                return self._parse_json_response_v4(raw_response)

        except Exception as e:
            print(f"[ai_companion] [V4] Error calling AI_COMPLETE: {e}")
            traceback.print_exc()
            return None

    def _parse_json_response_v4(self, raw_response: str) -> Optional[Dict[str, Any]]:
        """Parse JSON response with definitive V4 sanitization and repair."""
        print(f"[ai_companion] [V4] Parsing response...")
        
        # 1. ALWAYS log raw response for debugging
        try:
            debug_dir = Path(tempfile.gettempdir()) / "ai_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            log_file = debug_dir / f"json_debug_{timestamp}.txt"
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"RAW_RESPONSE:\n{raw_response}\n\nREPR_RAW:\n{repr(raw_response)}")
            print(f"[ai_companion] Debug log created: {log_file.name}")
        except Exception as e:
            print(f"[ai_companion] Failed to write debug log: {e}")

        content = raw_response.strip()
        print(f"[ai_companion] [STEP 1: Original] repr(content)[:100]: {repr(content)[:100]}")

        # Remove BOM if present
        if content.startswith('\ufeff'):
            content = content[1:]

        # 2. Multi-stage Unescaping
        # Check if content is escaped (contains literal \" and \n)
        if '\\"' in content or '\\n' in content:
            print(f"[ai_companion] [STEP 2: Unescaping] Detected likely escapes. repr(start): {repr(content[:20])}")
            
            # Attempt 2a: Standard JSON-string unescape
            try:
                # If content is "{\n \"key\": \"value\"}", wrapping in quotes makes it a valid JSON string
                # BUT we must handle the case where it's already quote-wrapped
                unescape_target = content
                if not unescape_target.startswith('"'):
                    unescape_target = f'"{unescape_target}"'
                
                content = json.loads(unescape_target)
                print(f"[ai_companion] [STEP 2a] json.loads unescape successful. repr(new_content)[:100]: {repr(content)[:100]}")
            except Exception as ue:
                print(f"[ai_companion] [STEP 2a] json.loads unescape failed: {ue}")
                
                # Attempt 2b: Manual decode literal escapers if it's really messy
                try:
                    # bytes.decode('unicode_escape') is powerful but dangerous with existing non-escaped chars
                    # We'll stick to a safer regex-based unescape for now or simple replacement
                    if content.startswith('{\\n') or content.startswith('{\\"'):
                         content = content.encode('utf-16', 'surrogatepass').decode('utf-16').encode('utf-8').decode('unicode_escape')
                         print(f"[ai_companion] [STEP 2b] manual decode successful. repr(new_content)[:100]: {repr(content)[:100]}")
                except:
                    pass

        # 3. Markdown Cleaning
        if '```' in content:
            parts = content.split('```')
            if len(parts) >= 3:
                content = parts[1]
                if content.startswith('json'): content = content[4:]
                content = content.strip()
            else:
                content = content.replace('```', '').replace('json', '').strip()
            print(f"[ai_companion] [STEP 3: Markdown Removed] repr(content)[:100]: {repr(content)[:100]}")

        # 4. Boundary Finding
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end != -1:
            content = content[start:end+1]
            print(f"[ai_companion] [STEP 4: Boundaries Found] repr(content)[:100]: {repr(content)[:100]}")
        else:
            print(f"[ai_companion] [STEP 4 WARNING] No boundaries found. repr(content[:50]): {repr(content[:50])}")

        # 4.5 Fix invalid escapes (e.g. \d in python code)
        print(f"[ai_companion] [STEP 4.5: Sanitizing Backslashes]...")
        content = self._fix_invalid_escapes(content)

        # 5. Parsing Pipeline
        result = None
        
        # Attempt A: Standard JSON
        try:
            result = json.loads(content, strict=False)
            print(f"[ai_companion] [STEP 5A] Standard JSON Success")
        except json.JSONDecodeError as je:
            print(f"[ai_companion] [STEP 5A] Standard JSON Fail: {je}")
            
            # Attempt B: Repairing + JSON
            print(f"[ai_companion] [STEP 5B] Trying Repair...")
            repaired = self._repair_truncated_json(content)
            if repaired != content:
                try:
                    result = json.loads(repaired, strict=False)
                    print(f"[ai_companion] [STEP 5B] Repair + JSON Success")
                except:
                    print(f"[ai_companion] [STEP 5B] Repair + JSON Fail")
            
            # Attempt C: ast.literal_eval (The "Final Boss" of single quotes)
            if result is None:
                print(f"[ai_companion] [STEP 5C] Trying ast.literal_eval...")
                try:
                    import ast
                    # Transform common JSON literals to Python literals for ast
                    python_friendly = content.replace('true', 'True').replace('false', 'False').replace('null', 'None')
                    result = ast.literal_eval(python_friendly)
                    print(f"[ai_companion] [STEP 5C] ast.literal_eval Success")
                except Exception as ae:
                    print(f"[ai_companion] [STEP 5C] ast.literal_eval Fail: {ae}")

        # Attempt D: Aggressive cleaning + Re-parse
        if result is None:
            print(f"[ai_companion] [STEP 5D] Trying Aggressive Cleaning...")
            try:
                cleaned = re.sub(r',\s*([\]}])', r'\1', content)
                cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', cleaned)
                try:
                    result = json.loads(cleaned, strict=False)
                    print(f"[ai_companion] [STEP 5D] Cleaned + JSON Success")
                except:
                    try:
                        import ast
                        python_friendly = cleaned.replace('true', 'True').replace('false', 'False').replace('null', 'None')
                        result = ast.literal_eval(python_friendly)
                        print(f"[ai_companion] [STEP 5D] Cleaned + ast Success")
                    except:
                        print(f"[ai_companion] [STEP 5D] All Pipeline stages failed.")
            except Exception as e:
                print(f"[ai_companion] [STEP 5D] Cleaning pre-process failed: {e}")

        # FINAL RESOLUTION
        if result is not None:
            # Handle recursive stringification
            attempts = 0
            while isinstance(result, str) and attempts < 3:
                print(f"[ai_companion] [RECURSIVE DETECTED] Re-parsing...")
                try: result = json.loads(result, strict=False)
                except:
                    try:
                        import ast
                        result = ast.literal_eval(result)
                    except: break
                attempts += 1

        if isinstance(result, dict):
            print(f"[ai_companion] [V4] ✅ SUCCESS: Parsed into dictionary")
            return result
        else:
            print(f"[ai_companion] [V4] ❌ FAILURE: Result is {type(result)}")
            return None

    def _get_professional_histogram_code(self) -> str:
        """
        Return pre-built professional histogram code.
        This is used INSTEAD of LLM-generated code to avoid truncation and syntax issues.
        The code uses only simple strings (no f-strings) and handles both numeric and categorical data.
        Variables available at runtime: df, column_name, histogram_path, data
        """
        return r"""import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
try:
    plt.style.use('seaborn-v0_8-whitegrid')
except OSError:
    try:
        plt.style.use('seaborn-whitegrid')
    except OSError:
        pass
fig = plt.figure(figsize=(16, 10), facecolor='white')
gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)
total = len(df)
nulls = df[column_name].isna().sum()
completeness = ((total - nulls) / total * 100) if total > 0 else 0
uniques = df[column_name].nunique()
df_clean = df[df[column_name].notna()].copy()
ax1 = fig.add_subplot(gs[0:2, 0:2])
if pd.api.types.is_numeric_dtype(df[column_name]) and len(df_clean) > 0:
    n, bins, patches = ax1.hist(df_clean[column_name], bins=30, alpha=0.7, color='steelblue', edgecolor='black')
    mean_val = df_clean[column_name].mean()
    median_val = df_clean[column_name].median()
    ax1.axvline(mean_val, color='red', linestyle='--', linewidth=2, label='Mean', alpha=0.8)
    ax1.axvline(median_val, color='green', linestyle='--', linewidth=2, label='Median', alpha=0.8)
    ax1.legend(fontsize=11)
    ax1.set_xlabel('Value Range', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Frequency', fontsize=12, fontweight='bold')
else:
    top10 = df_clean[column_name].value_counts().head(10)
    colors = plt.cm.Blues_r(np.linspace(0.3, 0.8, len(top10)))
    bars = ax1.barh(range(len(top10)), top10.values, color=colors, edgecolor='black')
    ax1.set_yticks(range(len(top10)))
    ax1.set_yticklabels([str(v)[:30] for v in top10.index], fontsize=10)
    ax1.set_xlabel('Count', fontsize=12, fontweight='bold')
    ax1.invert_yaxis()
ax1.set_title('Distribution Analysis', fontsize=14, fontweight='bold', pad=15)
ax1.grid(True, alpha=0.3)
ax2 = fig.add_subplot(gs[0, 2])
ax2.axis('off')
metrics = 'QUALITY METRICS\n' + '='*20 + '\n\nTotal: ' + str(total) + '\nValid: ' + str(total-nulls) + '\nMissing: ' + str(nulls) + '\n\nComplete: ' + str(round(completeness, 1)) + '%\nUnique: ' + str(uniques)
ax2.text(0.05, 0.5, metrics, fontsize=11, family='monospace', va='center', bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.3))
ax3 = fig.add_subplot(gs[1, 2])
ax3.set_xlim(-1.2, 1.2)
ax3.set_ylim(-1.2, 1.2)
ax3.axis('off')
if completeness >= 90:
    color = 'green'
elif completeness >= 75:
    color = 'orange'
else:
    color = 'red'
circle = plt.Circle((0, 0), 1, color=color, alpha=0.6)
ax3.add_patch(circle)
ax3.text(0, 0, str(int(completeness)) + '%', ha='center', va='center', fontsize=28, fontweight='bold', color='white')
ax3.set_title('Completeness', fontsize=12, fontweight='bold')
ax4 = fig.add_subplot(gs[2, :])
ax4.axis('off')
valid_pct = (total - nulls) / total if total > 0 else 0
miss_pct = nulls / total if total > 0 else 0
rect1 = Rectangle((0, 0.3), valid_pct, 0.4, facecolor='green', edgecolor='black', linewidth=2)
rect2 = Rectangle((valid_pct, 0.3), miss_pct, 0.4, facecolor='red', edgecolor='black', linewidth=2)
ax4.add_patch(rect1)
ax4.add_patch(rect2)
if valid_pct > 0.1:
    ax4.text(valid_pct/2, 0.5, 'VALID: ' + str(total-nulls), ha='center', va='center', fontsize=12, fontweight='bold', color='white')
if miss_pct > 0.05:
    ax4.text(valid_pct + miss_pct/2, 0.5, 'MISSING: ' + str(nulls), ha='center', va='center', fontsize=12, fontweight='bold', color='white')
ax4.set_xlim(0, 1)
ax4.set_ylim(0, 1)
ax4.set_title('Data Completeness Breakdown', fontsize=13, fontweight='bold')
plt.suptitle('Professional Data Quality Dashboard', fontsize=16, fontweight='bold', y=0.98)
plt.savefig(histogram_path, dpi=150, bbox_inches='tight', facecolor='white')
plt.close()"""

    def _sanitize_python_code(self, code: str) -> str:
        """
        Sanitize LLM-generated Python code to fix common issues.
        Removes f-strings, fixes quotes, simplifies complex patterns.
        """
        import re

        print("[ai_companion] Sanitizing generated Python code...")

        # Remove f-string prefix (f'...' -> '...' and f"..." -> "...")
        # This is aggressive but necessary since LLMs keep using f-strings
        code = re.sub(r'\bf(["\'])', r'\1', code)

        # Remove .format() calls - replace with plain strings
        # "text {}".format(var) -> "text"
        code = re.sub(r'(["\'])([^"\']*)\{\}([^"\']*)\1\.format\([^)]+\)', r'\1\2\3\1', code)

        # Log sanitization
        print(f"[ai_companion] Code sanitized - removed f-strings and .format() calls")

        return code

    def _fix_invalid_escapes(self, json_str: str) -> str:
        """
        Fix common invalid escape sequences in JSON strings (V4).
        Doubles backslashes that are NOT part of valid JSON escapes.
        """
        import re
        
        # Valid JSON escapes are: \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
        # We find any \ that is NOT followed by one of these valid characters.
        # pattern matches \ followed by a char NOT in " \ / b f n r t u
        pattern = r'\\(?![\\\"\/bfnrtu])'
        
        # also handle \u that is not followed by 4 hex digits
        pattern_u = r'\\u(?![0-9a-fA-F]{4})'
        
        fixed = re.sub(pattern, r'\\\\', json_str)
        fixed = re.sub(pattern_u, r'\\\\u', fixed)
        
        if fixed != json_str:
            print(f"[ai_companion] [V4] [Sanitizer] Fixed invalid escapes (likely regex or paths)")
        return fixed

    def _repair_truncated_json(self, json_str: str) -> str:
        """Attempt to repair truncated JSON by closing open structures."""
        json_str = json_str.strip()
        if not json_str.startswith('{'):
            return json_str
            
        stack = []
        in_string = False
        escape = False
        
        for char in json_str:
            if escape:
                escape = False
                continue
            if char == '\\':
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == '{':
                stack.append('}')
            elif char == '[':
                stack.append(']')
            elif char == '}':
                if stack and stack[-1] == '}':
                    stack.pop()
            elif char == ']':
                if stack and stack[-1] == ']':
                    stack.pop()
                    
        if in_string:
            json_str += '"'
            
        while stack:
            json_str += stack.pop()
            
        return json_str

    def _compute_quality_score(
        self,
        profile: Dict[str, Any],
        ai_analysis: Dict[str, Any]
    ) -> int:
        """
        Compute quality score (0-100) based on profile metrics and AI analysis.

        Scoring algorithm:
        - Start at 100
        - Deduct for low completeness
        - Deduct for issues found by AI (severity-weighted)
        - Cap at 0-100 range
        """
        score = 100

        # Completeness penalty
        total = profile.get("total", 0)
        nulls = profile.get("nulls", 0)
        completeness = ((total - nulls) / total * 100) if total > 0 else 0

        if completeness < 50:
            score -= 30
        elif completeness < 75:
            score -= 20
        elif completeness < 90:
            score -= 10

        # Issue severity penalties
        issues = ai_analysis.get("issues", [])
        for issue in issues:
            severity = issue.get("severity", "medium").lower()
            affected_pct = issue.get("affected_pct", 0)

            if severity == "high":
                score -= min(25, affected_pct * 0.5)
            elif severity == "medium":
                score -= min(15, affected_pct * 0.3)
            elif severity == "low":
                score -= min(5, affected_pct * 0.1)

        return max(0, min(100, int(score)))

    def _generate_histogram(
        self,
        table_name: str,
        column_name: str,
        code: str,
        sample_data: List[Any],
        schema_name: str = None,
        database_name: str = None
    ) -> str:
        """
        Generate histogram by executing validated AI-generated code.

        Args:
            table_name: Table name
            column_name: Column name
            code: Python code to execute
            sample_data: Sample data for plotting
            schema_name: Optional schema name

        Returns:
            Path to generated histogram image (empty string if failed)
        """

        # Sanitize code first (fix common LLM mistakes)
        code = self._sanitize_python_code(code)

        # Validate code
        from ai.code_validator import validate_code, get_safe_namespace

        is_safe, error = validate_code(code)
        if not is_safe:
            print(f"[ai_companion] Code validation failed: {error}")
            print(f"[ai_companion] Problematic code: {code[:500]}")
            return ""

        # Create output directory in temp (SiS-safe: /tmp is writable)
        output_dir = Path(tempfile.gettempdir()) / "ai_profiles" / datetime.now().strftime("%Y%m%d_%H%M%S") / table_name
        output_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize filename to avoid OS path issues (Snowflake/SIS + Windows)
        safe_colname = re.sub(r"[^A-Za-z0-9_.-]+", "_", column_name).strip("_") or "column"
        histogram_path = output_dir / f"{safe_colname}_histogram.png"

        # Fetch more data for histogram (up to 1000 rows)
        schema = schema_name or (os.getenv("SNOWFLAKE_SCHEMA") or "PUBLIC").upper()
        database = database_name or (os.getenv("SNOWFLAKE_DATABASE") or "").upper()
        
        if database:
            full_table = f'"{database}"."{schema}"."{table_name.upper()}"'
        else:
            full_table = f'"{schema}"."{table_name.upper()}"'
            
        col_ref = f'"{column_name.upper()}"'

        sql = f"""
            SELECT {col_ref}
            FROM {full_table}
            WHERE {col_ref} IS NOT NULL
            LIMIT 1000
        """

        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                data = [row[0] for row in cur.fetchall()]
        except Exception as e:
            print(f"[ai_companion] Error fetching data for histogram: {e}")
            data = sample_data

        # Execute code in safe namespace
        try:
            import pandas as pd
            import numpy as np
            import matplotlib
            matplotlib.use('Agg')  # Non-interactive backend
            import matplotlib.pyplot as plt

            # Prepare safe namespace
            namespace = get_safe_namespace()
            namespace['data'] = data
            namespace['histogram_path'] = str(histogram_path)

            # Create DataFrame for the AI-generated code
            # AI code often expects 'df' with column named after the column_name
            df = pd.DataFrame({column_name: data})
            namespace['df'] = df
            namespace['column_name'] = column_name

            # Execute code
            exec(code, namespace)

            # Ensure plot is saved
            if not histogram_path.exists() and plt.get_fignums():
                plt.savefig(histogram_path, bbox_inches='tight', dpi=100)
                plt.close('all')

            if histogram_path.exists():
                print(f"[ai_companion] Histogram saved: {histogram_path}")
                return str(histogram_path)
            else:
                print(f"[ai_companion] Histogram not generated")
                return ""

        except Exception as e:
            print(f"[ai_companion] Error executing histogram code: {e}")
            traceback.print_exc()
            print(f"[ai_companion] Histogram Error ({table_name}.{column_name}) at {datetime.now()}")
            print(traceback.format_exc())
            return ""

    def _fallback_analysis(
        self,
        table_name: str,
        column_name: str,
        profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Provide rule-based fallback analysis when AI fails."""

        dtype = profile.get("datatype", "UNKNOWN")
        total = profile.get("total", 0)
        nulls = profile.get("nulls", 0)
        distinct = profile.get("distinct", 0)

        completeness = ((total - nulls) / total * 100) if total > 0 else 0
        cardinality_pct = (distinct / total * 100) if total > 0 else 0

        # Infer semantic type
        semantic_type = "unknown"
        col_upper = column_name.upper()
        if "ADDRESS" in col_upper or "STREET" in col_upper:
            semantic_type = "address"
        elif "EMAIL" in col_upper:
            semantic_type = "email"
        elif "PHONE" in col_upper or "TEL" in col_upper:
            semantic_type = "phone"
        elif "_ID" in col_upper or "KEY" in col_upper:
            semantic_type = "id"
        elif "DATE" in col_upper or "TIMESTAMP" in col_upper or dtype.startswith("DATE") or dtype.startswith("TIMESTAMP"):
            semantic_type = "date"
        elif "NAME" in col_upper:
            semantic_type = "name"
        elif dtype.startswith(("NUMBER", "FLOAT", "INT", "DECIMAL")):
            semantic_type = "numeric"
        else:
            semantic_type = "text"

        # Detect quality issues
        issues = []
        if completeness < 80:
            issues.append({
                "issue": f"Low completeness ({completeness:.1f}%)",
                "severity": "high" if completeness < 50 else "medium",
                "affected_pct": 100 - completeness
            })

        if cardinality_pct > 95 and total > 100:
            issues.append({
                "issue": "Very high cardinality (potential unique ID or free text)",
                "severity": "low",
                "affected_pct": 0
            })

        cardinality_desc = f"very high cardinality ({distinct:,} distinct values)" if cardinality_pct > 50 else f"{distinct:,} distinct values"

        # Define completeness description
        if completeness >= 90:
            completeness_desc = f"{completeness:.1f}% complete (excellent)"
        elif completeness >= 75:
            completeness_desc = f"{completeness:.1f}% complete (good)"
        elif completeness >= 50:
            completeness_desc = f"{completeness:.1f}% complete (fair)"
        else:
            completeness_desc = f"{completeness:.1f}% complete (poor)"

        analysis_txt = (
            f"Rule-based Analysis: This is a '{semantic_type}' column. "
            f"Data is {completeness_desc} with {cardinality_desc}. "
            "AI deep analysis failed to parse, using statistical fallback."
        )

        # Generate basic recommendations
        recommendations = []
        if completeness < 80:
            recommendations.append("Investigate and address missing/null values")
        if cardinality_pct > 95 and total > 100:
            recommendations.append("Consider using this as a unique identifier or key")
        if not recommendations:
            recommendations.append("Run deep AI analysis again for specific insights")

        # Generate basic SQL based on semantic type and data type
        fallback_sql = self._generate_fallback_sql(column_name, semantic_type, dtype, completeness)

        return {
            "analysis_text": analysis_txt,
            "column_type_inference": semantic_type,
            "quality_issues": issues if issues else [{"issue": "No structural issues detected by rule-engine", "severity": "low", "affected_pct": 0}],
            "quality_score": int(completeness * 0.8), # Penalize fallback slightly
            "recommendations": recommendations,
            "transformation_logic": f"Basic {semantic_type} transformation: trim whitespace, handle nulls, apply standard cleaning.",
            "remediation_sql": fallback_sql,
            "generated_code": "# Fallback code not available",
            "histogram_path": ""
        }

    def _generate_fallback_sql(self, column_name: str, semantic_type: str, dtype: str, completeness: float) -> str:
        """Generate basic SQL remediation based on semantic type."""

        # Add data quality comment
        quality_note = f"-- Data Completeness: {completeness:.1f}%\n"

        # Basic SQL templates by semantic type
        if semantic_type == "email":
            return quality_note + f"""SELECT
  CASE
    WHEN TRIM({column_name}) IS NULL OR TRIM({column_name}) = '' THEN 'UNKNOWN'
    WHEN NOT REGEXP_LIKE({column_name}, '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\\\.[A-Z|a-z]{{2,}}$') THEN 'INVALID_EMAIL'
    ELSE LOWER(TRIM({column_name}))
  END as cleaned_{column_name}
FROM source_table"""

        elif semantic_type == "phone" or "phone" in column_name.lower():
            return quality_note + f"""SELECT
  CASE
    WHEN TRIM({column_name}) IS NULL THEN 'UNKNOWN'
    ELSE REGEXP_REPLACE(TRIM({column_name}), '[^0-9]', '')
  END as cleaned_{column_name}
FROM source_table"""

        elif semantic_type == "date" or dtype in ["DATE", "TIMESTAMP", "TIMESTAMP_NTZ"]:
            return quality_note + f"""SELECT
  COALESCE(
    TRY_TO_DATE({column_name}),
    TRY_TO_TIMESTAMP({column_name}),
    CURRENT_DATE
  ) as cleaned_{column_name}
FROM source_table"""

        elif semantic_type in ["id", "key"] or "_id" in column_name.lower() or "_key" in column_name.lower():
            return quality_note + f"""SELECT
  CASE
    WHEN TRIM({column_name}) IS NULL OR TRIM({column_name}) = '' THEN NULL
    ELSE UPPER(TRIM({column_name}))
  END as cleaned_{column_name}
FROM source_table"""

        elif semantic_type in ["physical_address", "address"]:
            return quality_note + f"""SELECT
  CASE
    WHEN TRIM({column_name}) IS NULL OR TRIM({column_name}) = '' THEN 'UNKNOWN'
    ELSE TRIM(UPPER(REGEXP_REPLACE({column_name}, '[^A-Za-z0-9\\\\s,.-]', '')))
  END as cleaned_{column_name}
FROM source_table"""

        elif dtype in ["NUMBER", "DECIMAL", "NUMERIC", "FLOAT"]:
            return quality_note + f"""SELECT
  TRY_TO_NUMBER({column_name}, 10, 2) as cleaned_{column_name}
FROM source_table"""

        elif dtype in ["VARCHAR", "TEXT", "STRING"]:
            return quality_note + f"""SELECT
  CASE
    WHEN TRIM({column_name}) IS NULL OR TRIM({column_name}) = '' THEN NULL
    ELSE TRIM(REGEXP_REPLACE({column_name}, '\\\\s+', ' '))
  END as cleaned_{column_name}
FROM source_table"""

        else:
            # Generic fallback
            return quality_note + f"""SELECT
  CASE
    WHEN {column_name} IS NULL THEN NULL
    ELSE {column_name}
  END as cleaned_{column_name}
FROM source_table
-- Note: Add specific transformations based on data quality analysis"""


if __name__ == "__main__":
    # Simple test
    print("AI Data Quality Companion initialized successfully")
