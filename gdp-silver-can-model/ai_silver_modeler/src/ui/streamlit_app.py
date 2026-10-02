import streamlit as st
import pandas as pd
import sys
import os
import json
import csv
import hashlib
import tempfile
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Running in SiS - no .env needed

# Add src to pythonpath so we can import our modules
try:
    # Try to find base_dir relative to this file
    base_dir = Path(__file__).resolve().parents[2]
    src_dir = base_dir / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
except Exception:
    # Fallback for environments where __file__ might be restricted (SiS)
    base_dir = None
    # In SiS, modules are relative to the stage root.
    # The current working directory is the stage root, which should already be on sys.path.
    # Add current directory explicitly as fallback.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

from extract.profile_real_data import list_tables, list_schemas, get_connection, get_column_info
from ai.bronze_silver_mapper import map_bronze_to_silver
from ai.iterative_mapper import run_iterative_mapping, generate_column_descriptions, compute_embedding_similarity
from ai.profiler_personas import run_profiler_personas
from utils.csv_sanitizer import sanitize_text, sanitize_dataframe

from decimal import Decimal
import json
from datetime import datetime, date

from utils.stage_exporter import upload_bytes_to_stage, StageExportError

# Custom JSON encoder for Snowflake data types
class SnowflakeEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle Snowflake-specific data types."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='ignore')
        return super().default(obj)

# Handle Snowflake Session (Native SiS support)
def get_current_session():
    try:
        from snowflake.snowpark.context import get_active_session
        return get_active_session()
    except Exception:
        return None

native_session = get_current_session()
is_sis = native_session is not None

DEFAULT_EXPORT_STAGE = os.getenv("EXPORT_STAGE_NAME", "YOUR_DB.YOUR_SCHEMA.YOUR_EXPORT_STAGE")
STAGE_URL_TTL_SECONDS = int(os.getenv("EXPORT_STAGE_TTL_SECONDS", "3600"))


def _safe_close(conn):
    """Close a connection only if we are NOT running in SiS.
    In SiS, get_connection() returns the shared native session connection
    which must not be closed."""
    if not is_sis and conn:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_stage_session_state():
    if "stage_session_folder" not in st.session_state:
        st.session_state.stage_session_folder = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    if "stage_links" not in st.session_state:
        st.session_state.stage_links = {}


def _profiles_signature(profiles: Dict[str, Dict[str, Any]]) -> str:
    if not profiles:
        return "EMPTY"
    parts = []
    for table in sorted(profiles.keys()):
        columns = profiles.get(table, {})
        col_names = []
        if isinstance(columns, dict):
            col_names = sorted(columns.keys())
        parts.append(f"{table}:{','.join(col_names)}")
    return "|".join(parts)


def _get_cached_descriptions(cache_key: str, signature: str):
    cache = st.session_state.get("description_cache", {}).get(cache_key)
    if cache and cache.get("signature") == signature:
        return cache.get("data")
    return None


def _store_description_cache(cache_key: str, signature: str, data):
    if "description_cache" not in st.session_state:
        st.session_state.description_cache = {}
    st.session_state.description_cache[cache_key] = {
        "signature": signature,
        "data": data,
        "cached_at": datetime.now().isoformat()
    }


def _render_export_control(control_id: str, label: str, payload, filename: str, mime: str, auto_stage: bool = False):
    """Unified download control that stages files when running in SiS."""
    data_bytes = payload
    if isinstance(payload, str):
        data_bytes = payload.encode("utf-8-sig")
    elif hasattr(payload, "getvalue"):
        data_bytes = payload.getvalue()

    if not isinstance(data_bytes, (bytes, bytearray)):
        raise ValueError("Export payload must resolve to bytes")
    if len(data_bytes) == 0:
        raise ValueError("Export payload is empty")

    payload_hash = hashlib.sha256(data_bytes).hexdigest()

    if not is_sis:
        st.download_button(
            label=label,
            data=data_bytes,
            file_name=filename,
            mime=mime,
            use_container_width=True,
            key=f"{control_id}_download"
        )
        return

    if not DEFAULT_EXPORT_STAGE:
        st.error("No export stage configured. Set EXPORT_STAGE_NAME env var.")
        return

    _ensure_stage_session_state()
    cache_key = f"{control_id}_stage"

    def _needs_stage() -> bool:
        cached = st.session_state.stage_links.get(cache_key)
        return (
            cached is None
            or cached.get("filename") != filename
            or cached.get("hash") != payload_hash
        )

    def _stage_file():
        conn = None
        try:
            conn = get_connection()
            stage_result = upload_bytes_to_stage(
                data_bytes,
                filename,
                stage_name=DEFAULT_EXPORT_STAGE,
                subdirectory=st.session_state.stage_session_folder,
                conn=conn,
                expires_in=STAGE_URL_TTL_SECONDS
            )
            stage_result["filename"] = filename
            stage_result["hash"] = payload_hash
            stage_result["size"] = len(data_bytes)
            st.session_state.stage_links[cache_key] = stage_result
            st.success(f"Saved to stage path {stage_result['stage_path']}")
        except StageExportError as exc:
            st.session_state.stage_links.pop(cache_key, None)
            st.error(f"Stage export failed: {exc}")
        except Exception as exc:
            st.session_state.stage_links.pop(cache_key, None)
            st.error(f"Stage export failed: {exc}")
        finally:
            _safe_close(conn)

    if auto_stage and _needs_stage():
        _stage_file()
    elif not auto_stage:
        if st.button(label, key=f"{cache_key}_button", use_container_width=True):
            _stage_file()

    link_info = st.session_state.stage_links.get(cache_key)
    if link_info and link_info.get("url"):
        expires = link_info.get("expires_at", "")
        size = link_info.get("size")
        short_hash = link_info.get("hash", "")[:8]
        meta_bits = []
        if size:
            meta_bits.append(f"{size:,} bytes")
        if short_hash:
            meta_bits.append(f"hash {short_hash}")
        meta_text = f" ({' | '.join(meta_bits)})" if meta_bits else ""
        st.markdown(
            f"[➡️ Download {filename}]({link_info['url']}) _(expires {expires})_ {meta_text}"
        )


st.set_page_config(page_title="Data Modeling with AI", layout="wide", page_icon="🤖")
st.title("🤖 Data Modeler Agent")

# --- SiS STREAMLIT COMPATIBILITY ---
# SiS may run an older Streamlit version. Detect available features.
_has_data_editor = hasattr(st, 'data_editor')       # >= 1.23
_has_column_config = hasattr(st, 'column_config')    # >= 1.23
_has_status = hasattr(st, 'status')                  # >= 1.25
_has_cache_data = hasattr(st, 'cache_data')          # >= 1.18


# st.rerun was added in 1.27; before that it was st.experimental_rerun
def _rerun():
    """Cross-version rerun helper."""
    if hasattr(st, 'rerun'):
        st.rerun()
    elif hasattr(st, 'experimental_rerun'):
        st.experimental_rerun()


# st.status context manager shim (>= 1.25)
# Falls back to st.spinner + st.write for older versions
from contextlib import contextmanager

@contextmanager
def _status_context(label, expanded=True):
    """Compatibility wrapper for st.status (>= 1.25).
    Falls back to st.spinner for older Streamlit."""
    if _has_status:
        with st.status(label, expanded=expanded) as status:
            yield status
    else:
        _placeholder = st.empty()
        _placeholder.info(label)

        class _FakeStatus:
            def update(self, label="", state="", expanded=True):
                if state == "error":
                    _placeholder.error(label)
                elif state == "complete":
                    _placeholder.success(label)
                else:
                    _placeholder.info(label)

        yield _FakeStatus()


# container(border=...) was added after Streamlit 1.30. Keep backwards compatibility.
@contextmanager
def _safe_container(placeholder, **kwargs):
    try:
        ctx = placeholder.container(**kwargs)
    except TypeError:
        ctx = placeholder.container()
    with ctx:
        yield ctx


def _normalize_identifier(value: str) -> str:
    if not value:
        return ""
    return value.strip().strip('"')


def _sync_sis_context(role=None, warehouse=None, database=None, schema=None):
    """Align the native Snowflake session with sidebar selections when running in SiS."""
    if not is_sis or not native_session:
        return

    if st.session_state.get("sis_context_locked"):
        return

    def _apply(getter_name: str, setter_name: str, target_value: str, label: str):
        if not target_value or not hasattr(native_session, setter_name):
            return
        desired = _normalize_identifier(target_value)
        if not desired:
            return

        current = None
        getter = getattr(native_session, getter_name, None)
        if getter:
            try:
                current = getter()
            except Exception:
                current = None

        # Skip if already aligned
        if current and _normalize_identifier(current).upper() == desired.upper():
            return

        try:
            getattr(native_session, setter_name)(desired)
        except Exception as exc:
            message = str(exc)
            if "Unsupported statement type" in message or "090236" in message:
                st.session_state["sis_context_locked"] = True
                st.sidebar.info("Snowflake blocked USE statements in this Streamlit session; set the context before launching the app.")
            else:
                st.sidebar.warning(f"Could not switch {label} to {desired}: {exc}")
            return

    _apply('get_current_role', 'use_role', role, 'role')
    if st.session_state.get("sis_context_locked"):
        return
    _apply('get_current_warehouse', 'use_warehouse', warehouse, 'warehouse')
    if st.session_state.get("sis_context_locked"):
        return
    _apply('get_current_database', 'use_database', database, 'database')
    if st.session_state.get("sis_context_locked"):
        return
    _apply('get_current_schema', 'use_schema', schema, 'schema')


# st.cache_data shim: fall back to st.experimental_memo if needed
if not _has_cache_data:
    st.cache_data = getattr(st, 'experimental_memo', st.cache)

# --- CACHED UTILS ---
# We wrap the imported functions to cache their results.
# 'ttl=600' means stats refresh every 10 mins, preventing stale schemas.

@st.cache_data(ttl=600, show_spinner=False)
def list_tables_cached(account, user, role, warehouse, database, schema):
    # We pass connection params as args so cache invalidates if they change
    os.environ["SNOWFLAKE_ACCOUNT"] = account or ""
    os.environ["SNOWFLAKE_USER"] = user or ""
    os.environ["SNOWFLAKE_ROLE"] = role or ""
    os.environ["SNOWFLAKE_WAREHOUSE"] = warehouse or ""
    os.environ["SNOWFLAKE_DATABASE"] = database or ""
    os.environ["SNOWFLAKE_SCHEMA"] = schema or ""

    conn = get_connection()
    try:
        return list_tables(conn, schema=schema, database=database, limit=200)
    finally:
        _safe_close(conn)

@st.cache_data(ttl=600, show_spinner=False)
def get_column_info_cached(account, user, role, warehouse, database, schema, table_name):
    # Re-set env for safety in thread-local execution
    os.environ["SNOWFLAKE_DATABASE"] = database or ""
    os.environ["SNOWFLAKE_SCHEMA"] = schema or ""

    conn = get_connection()
    try:
        return get_column_info(conn, table_name, schema=schema, database=database)
    finally:
        _safe_close(conn)

@st.cache_data(ttl=3600, show_spinner=False)
def profile_table_cached(account, user, role, warehouse, database, schema, table_name, target_columns, sample_pct=100):
    """Cached wrapper for profiling a specific table."""
    os.environ["SNOWFLAKE_DATABASE"] = database or ""
    os.environ["SNOWFLAKE_SCHEMA"] = schema or ""
    conn = get_connection()
    try:
        return profile_table(conn, table_name, target_columns=target_columns, sample_pct=sample_pct, schema=schema, database=database)
    finally:
        _safe_close(conn)

# --- CACHED AI WRAPPERS ---

@st.cache_data(show_spinner=False)
def generate_descriptions_cached(metadata_struct, all_profiles):
    """Cached wrapper for description generation (in-memory, SiS-safe)."""
    # Convert Snowflake types to JSON-safe dicts via round-trip
    safe_profiles = json.loads(json.dumps(all_profiles, cls=SnowflakeEncoder))
    safe_metadata = json.loads(json.dumps(metadata_struct, cls=SnowflakeEncoder))

    # Use in-memory mode: pass dicts directly, no file I/O
    return generate_descriptions('llm', None, None, None,
                                 metadata_dict=safe_metadata,
                                 profiles_dict=safe_profiles)

@st.cache_data(show_spinner=False)
def get_embeddings_cached(texts):
    return get_embeddings_snowflake(texts)

@st.cache_data(show_spinner=False)
def generate_silver_model_cached(cluster_payload):
    return generate_silver_model(cluster_payload)

@st.cache_data(ttl=600, show_spinner=False)
def list_schemas_cached(account, user, role, warehouse, database):
    """List all schemas in the database."""
    os.environ["SNOWFLAKE_ACCOUNT"] = account or ""
    os.environ["SNOWFLAKE_USER"] = user or ""
    os.environ["SNOWFLAKE_ROLE"] = role or ""
    os.environ["SNOWFLAKE_WAREHOUSE"] = warehouse or ""
    os.environ["SNOWFLAKE_DATABASE"] = database or ""
    conn = get_connection()
    try:
        return list_schemas(conn, database=database)
    finally:
        _safe_close(conn)

@st.cache_data(ttl=600, show_spinner=False)
def list_tables_in_schema_cached(account, user, role, warehouse, database, schema):
    """List tables in a specific schema."""
    os.environ["SNOWFLAKE_ACCOUNT"] = account or ""
    os.environ["SNOWFLAKE_USER"] = user or ""
    os.environ["SNOWFLAKE_ROLE"] = role or ""
    os.environ["SNOWFLAKE_WAREHOUSE"] = warehouse or ""
    os.environ["SNOWFLAKE_DATABASE"] = database or ""
    conn = get_connection()
    try:
        return list_tables(conn, schema=schema, database=database, limit=200)
    finally:
        _safe_close(conn)

# --- SIDEBAR: CONNECTION ---
st.sidebar.header("🔌 Snowflake Connection")

if is_sis:
    st.sidebar.success("🔗 Connected natively to Snowflake")
    # Get defaults from native session
    def_account = native_session.connection.account if hasattr(native_session.connection, 'account') else ""
    def_user = native_session.get_current_user() if hasattr(native_session, 'get_current_user') else ""
    def_role = native_session.get_current_role() if hasattr(native_session, 'get_current_role') else ""
    def_warehouse = native_session.get_current_warehouse() if hasattr(native_session, 'get_current_warehouse') else ""
    def_database = native_session.get_current_database() if hasattr(native_session, 'get_current_database') else ""
    def_schema = native_session.get_current_schema() if hasattr(native_session, 'get_current_schema') else "BRONZE"
else:
    def_account = os.getenv("SNOWFLAKE_ACCOUNT", "")
    def_user = os.getenv("SNOWFLAKE_USER", "")
    def_role = os.getenv("SNOWFLAKE_ROLE", "")
    def_warehouse = os.getenv("SNOWFLAKE_WAREHOUSE", "")
    def_database = os.getenv("SNOWFLAKE_DATABASE", "")
    def_schema = os.getenv("SNOWFLAKE_SCHEMA", "BRONZE")

# Initialize sidebar values from default context ONLY ONCE on first load
if "sidebar_account" not in st.session_state:
    st.session_state.sidebar_account = def_account
if "sidebar_user" not in st.session_state:
    st.session_state.sidebar_user = def_user
if "sidebar_role" not in st.session_state:
    st.session_state.sidebar_role = def_role
if "sidebar_warehouse" not in st.session_state:
    st.session_state.sidebar_warehouse = def_warehouse
if "sidebar_database" not in st.session_state:
    st.session_state.sidebar_database = def_database
if "sidebar_schema" not in st.session_state:
    st.session_state.sidebar_schema = def_schema

# Use session state as the source of truth (NOT os.environ)
account = st.sidebar.text_input("❄️ Account", value=st.session_state.sidebar_account, key="account_input")
user = st.sidebar.text_input("👤 User", value=st.session_state.sidebar_user, key="user_input")
role = st.sidebar.text_input("🔑 Role", value=st.session_state.sidebar_role, key="role_input")
warehouse = st.sidebar.text_input("⚙️ Warehouse", value=st.session_state.sidebar_warehouse, key="warehouse_input")
database = st.sidebar.text_input("🗄️ Database", value=st.session_state.sidebar_database, key="database_input")
schema = st.sidebar.text_input("📂 Source Schema", value=st.session_state.sidebar_schema, key="schema_input", help="Schema containing Source tables")

# Update session state when user changes values
st.session_state.sidebar_account = account
st.session_state.sidebar_user = user
st.session_state.sidebar_role = role
st.session_state.sidebar_warehouse = warehouse
st.session_state.sidebar_database = database
st.session_state.sidebar_schema = schema

_sync_sis_context(role=role, warehouse=warehouse, database=database, schema=schema)

# CRITICAL FIX: Snapshot Bronze database & schema NOW before Silver can pollute os.environ
# Store in session state to ensure they're preserved across reruns
if "bronze_db_snapshot" not in st.session_state:
    st.session_state.bronze_db_snapshot = database
if "bronze_schema_snapshot" not in st.session_state:
    st.session_state.bronze_schema_snapshot = schema

# Update snapshot only if sidebar values actually changed (user edited them)
if database != "":
    st.session_state.bronze_db_snapshot = database
if schema != "":
    st.session_state.bronze_schema_snapshot = schema


if st.sidebar.button("⚡ Test Connection", use_container_width=True):
    try:
        # Temporarily set env vars for the get_connection utility
        os.environ["SNOWFLAKE_ACCOUNT"] = account
        os.environ["SNOWFLAKE_USER"] = user
        os.environ["SNOWFLAKE_ROLE"] = role
        os.environ["SNOWFLAKE_WAREHOUSE"] = warehouse
        os.environ["SNOWFLAKE_DATABASE"] = database
        os.environ["SNOWFLAKE_SCHEMA"] = schema
        
        conn = get_connection()
        st.sidebar.success("✅ Connected!")
        _safe_close(conn)
    except Exception as e:
        st.sidebar.error(f"❌ Connection Failed: {e}")

if 'ai_analysis' in st.session_state and st.session_state.ai_analysis:
    st.sidebar.info(f"📊 AI Analysis results available for {len(st.session_state.ai_analysis)} columns")



# --- MAIN PANEL ---

from profiling.generate_long_descriptions import generate_descriptions
from ai.semantic_clustering import build_texts, get_embeddings_snowflake, cluster, fallback_embeddings
from ai.silver_model_generator import generate_silver_model
from ai.generate_silver_snowflake_ddl import parse_robust, sanitize, build_column_line, DEFAULT_PK_CANDIDATES
from extract.profile_real_data import profile_table

# --- SESSION STATE ---
if "profiles" not in st.session_state:
    st.session_state.profiles = None
if "model_data" not in st.session_state:
    st.session_state.model_data = None
if "desc_data" not in st.session_state:
    st.session_state.desc_data = None
if "description_cache" not in st.session_state:
    st.session_state.description_cache = {}

# STEP 3: Approval workflow session state
if "mapping_approval_status" not in st.session_state:
    st.session_state.mapping_approval_status = {}  # Dict[mapping_key, status]
if "mapping_results_original" not in st.session_state:
    st.session_state.mapping_results_original = None


# --- MODE TOGGLE ---

st.subheader("🎯 Mode Selection")
mode = st.radio(
    "Choose pipeline mode:",
    ["🔨 Generate New Model", "🔗 Map to Existing Target Schema"],
    horizontal=True,
    key="pipeline_mode"
)

is_mapping_mode = mode == "🔗 Map to Existing Target Schema"

st.subheader("📥 1. Select Source Tables")

# Use the protected snapshot values
bronze_db = st.session_state.bronze_db_snapshot
bronze_schema = st.session_state.bronze_schema_snapshot

# Display current Bronze context
st.caption(f"📍 **Bronze Context:** `{bronze_db}.{bronze_schema}` (from sidebar connection)")

# Force reset environment to Bronze context
os.environ["SNOWFLAKE_DATABASE"] = bronze_db
os.environ["SNOWFLAKE_SCHEMA"] = bronze_schema

# Fetch tables with explicit parameters (not relying on environment)
tables = []
try:
    tables = list_tables_cached(account, user, role, warehouse, bronze_db, bronze_schema)
    st.caption(f"🔍 Debug: Found {len(tables)} tables in `{bronze_db}.{bronze_schema}`")
except Exception as e:
    st.info(f"Please configure connection in sidebar to see tables. Error: {e}")

# Store Bronze tables in session state
if "bronze_selected" not in st.session_state:
    st.session_state.bronze_selected = []

# Create a unique key based on database and schema
bronze_context_key = f"{bronze_db}_{bronze_schema}"
if "bronze_context" not in st.session_state:
    st.session_state.bronze_context = bronze_context_key

# If context changed, reset selections
if st.session_state.bronze_context != bronze_context_key:
    st.session_state.bronze_selected = []
    st.session_state.bronze_context = bronze_context_key

# Filter to only valid selections
valid_bronze = [t for t in st.session_state.bronze_selected if t in tables]

# Debug info
if len(st.session_state.bronze_selected) > 0:
    st.caption(f"🔍 Debug: Session has {len(st.session_state.bronze_selected)} bronze tables, {len(valid_bronze)} are valid")

selected_tables = st.multiselect(
    "Choose Source tables:", 
    tables,
    default=valid_bronze,
    key="bronze_multiselect"
)

# Store selection immediately
st.session_state.bronze_selected = selected_tables


# --- SILVER SCHEMA SELECTION (Mapping Mode Only) ---
selected_silver_schema = None
selected_silver_tables = []

if is_mapping_mode:
    st.subheader("📤 1.b Select Target (Database/Schema/Tables)")
    st.info("💡 **Tip:** Target tables can be in a different database than Source. Specify the target database and schema below.")
    
    col_db, col_schema = st.columns(2)
    
    with col_db:
        # Allow selecting a different database for Silver target
        silver_database = st.text_input(
            "Silver Database:", 
            value=database, 
            help="Database containing Silver target tables (can be different from Bronze)",
            key="silver_db_input"
        )
        # Store in session state for later use
        st.session_state['silver_database'] = silver_database
    
    with col_schema:
        try:
            # Save original database to restore after
            original_db = os.environ.get("SNOWFLAKE_DATABASE", database)
            
            # Temporarily set to silver database for this query
            os.environ["SNOWFLAKE_DATABASE"] = silver_database
            available_schemas = list_schemas_cached(account, user, role, warehouse, silver_database)
            
            # Restore original database immediately
            os.environ["SNOWFLAKE_DATABASE"] = original_db
            
            selected_silver_schema = st.selectbox(
                "Silver Schema:", 
                available_schemas, 
                index=0 if available_schemas else 0,
                key="silver_schema_selection"
            )
        except Exception as e:
            st.warning(f"Could not load Silver schemas from `{silver_database}`: {e}")
            available_schemas = []
            selected_silver_schema = st.text_input("Silver Schema (manual):", value="SILVER", key="silver_schema_manual")
            # Ensure we restore even on error
            os.environ["SNOWFLAKE_DATABASE"] = database
    
    if selected_silver_schema:
        try:
            # Save original database
            original_db = os.environ.get("SNOWFLAKE_DATABASE", database)
            
            # Temporarily set to silver database
            os.environ["SNOWFLAKE_DATABASE"] = silver_database
            silver_tables = list_tables_in_schema_cached(account, user, role, warehouse, silver_database, selected_silver_schema)
            
            # Restore original database immediately
            os.environ["SNOWFLAKE_DATABASE"] = original_db
            
            # Store Silver tables in a more stable way
            if "silver_selected" not in st.session_state:
                st.session_state.silver_selected = []
            
            # Create context key for Silver
            silver_context_key = f"{silver_database}_{selected_silver_schema}"
            if "silver_context" not in st.session_state:
                st.session_state.silver_context = silver_context_key
            
            # If context changed, reset selections
            if st.session_state.silver_context != silver_context_key:
                st.session_state.silver_selected = []
                st.session_state.silver_context = silver_context_key
            
            # Filter to only valid selections
            valid_silver = [t for t in st.session_state.silver_selected if t in silver_tables]
            
            selected_silver_tables = st.multiselect(
                "Choose Silver tables to map to:", 
                silver_tables,
                default=valid_silver,
                key="silver_multiselect"
            )
            
            # Store selection immediately
            st.session_state.silver_selected = selected_silver_tables
            
        except Exception as e:
            st.warning(f"Could not load Silver tables: {e}")
            # Ensure we restore even on error
            os.environ["SNOWFLAKE_DATABASE"] = database

    
    # Store silver database for later use
    st.session_state.silver_database = silver_database




# --- COLUMN SELECTION ---
selected_columns_map = {} # {table: [col1, col2]}


if selected_tables:
    st.subheader("2️⃣ Select Required Columns")
    
    # get_column_info is now cached, so multiple iterations are fast
    for table in selected_tables:
        # distinct key for each expander to avoid conflicts
        with st.expander(f"Settings for `{table}`", expanded=False):
            # Pass all connection params to ensure cache key matches current sidebar state
            cols_info = get_column_info_cached(account, user, role, warehouse, database, schema, table)
            
            # Filter out binary/unprofitable column types
            binary_types = ("BINARY", "VARBINARY", "GEOMETRY", "GEOGRAPHY")
            filtered_cols_info = [c for c in cols_info if not any(c.get('type', '').upper().startswith(bt) for bt in binary_types)]
            
            if len(filtered_cols_info) < len(cols_info):
                excluded_count = len(cols_info) - len(filtered_cols_info)
                st.caption(f"ℹ️ {excluded_count} binary/geometry column(s) automatically excluded from profiling")
            
            col_names = [c['name'] for c in filtered_cols_info]
            
            if _has_data_editor and _has_column_config:
                # Modern Streamlit: interactive data editor with checkboxes
                df_cols = pd.DataFrame({
                    "Select": [True] * len(col_names),
                    "Column Name": col_names
                })
                edited_df = st.data_editor(
                    df_cols,
                    column_config={
                        "Select": st.column_config.CheckboxColumn(
                            "Include",
                            help="Check to include in profiling",
                            default=True,
                        ),
                        "Column Name": st.column_config.TextColumn(
                            "Column",
                            width="medium",
                            disabled=True
                        )
                    },
                    hide_index=True,
                    use_container_width=True,
                    key=f"editor_{table}"
                )
                picked = edited_df[edited_df["Select"]]["Column Name"].tolist()
            else:
                # SiS fallback: multiselect for column selection
                picked = st.multiselect(
                    f"Select columns to include:",
                    col_names,
                    default=col_names,
                    key=f"editor_{table}"
                )
            selected_columns_map[table] = picked

st.write("---")

# --- PHASE 1: PROFILING ---
if is_mapping_mode:
    st.subheader("📊 3. Profile Source Data")
else:
    st.subheader("📊 3. Profiling")

col1, col2 = st.columns([2, 1])
with col2:
    sample_pct = st.slider("Sample Rate (%)", 1, 100, 100, help="For tables with millions of rows, use a lower sample rate (e.g., 10%) for instant results.")

profile_btn_label = "🔍 Profile Bronze Data" if is_mapping_mode else "🔍 Profile Data"
if st.button(profile_btn_label, use_container_width=True):
    if not selected_tables:
        st.warning("Please select at least one table.")
    else:
        status_msg = "🛠️ Analyzing Bronze Source Data..." if is_mapping_mode else "🛠️ Running Data Quality Analysis..."
        with _status_context(status_msg, expanded=True) as status:

            conn = None
            all_profiles = {}
            try:
                # Use Bronze snapshot values to ensure correct database context
                bronze_db = st.session_state.bronze_db_snapshot
                bronze_schema = st.session_state.bronze_schema_snapshot
                
                # Set environment to Bronze context before profiling
                os.environ["SNOWFLAKE_DATABASE"] = bronze_db
                os.environ["SNOWFLAKE_SCHEMA"] = bronze_schema
                
                conn = get_connection()
                total_cols_profiled = 0
                for table in selected_tables:
                    target_cols = selected_columns_map.get(table, [])
                    if not target_cols: continue
                    st.write(f"Profiling `{bronze_db}.{bronze_schema}.{table}` ({len(target_cols)} columns, {sample_pct}% sample)...")
                    prof = profile_table_cached(account, user, role, warehouse, bronze_db, bronze_schema, table, tuple(target_cols), sample_pct)
                    all_profiles[table] = prof
                    total_cols_profiled += len(prof)
                
                st.session_state.profiles = all_profiles
                if total_cols_profiled > 0:
                    status.update(label=f"✅ Profiling Complete! ({total_cols_profiled} columns analyzed)", state="complete", expanded=False)
                else:
                    status.update(label="⚠️ Profiling finished, but no columns were analyzed.", state="complete", expanded=True)
                    st.warning("No columns were analyzed. Ensure you checked the 'Select' box for columns in step 2.")
            except Exception as e:
                st.error(f"Profiling Error: {str(e)}")
                status.update(label="❌ Profiling Failed", state="error", expanded=True)
            finally:
                if conn:
                    _safe_close(conn)

if st.session_state.profiles:
    st.write("---")
    if is_mapping_mode:
        st.subheader("📊 Bronze Source Analysis")
    else:
        st.subheader("📊 Data Quality Grid")

    
    # Flatten all columns for the rich table
    all_col_stats = []
    for table, cols in st.session_state.profiles.items():
        for col_name, stats in cols.items():
            total = stats.get('total', 0)
            nulls = stats.get('nulls', 0)
            phys_nulls = stats.get('physical_nulls', 0)
            valid = total - nulls
            completeness = (valid / total * 100) if total > 0 else 0
            
            # Sanitize function for CSV-safe values
            def sanitize_value(val):
                """Ensure value is CSV-safe (string or number, no binary/bytes)"""
                return sanitize_text(val)
            
            # Format range with sanitization
            rng = stats.get('range', {})
            min_val = sanitize_value(rng.get('min', 'N/A'))
            max_val = sanitize_value(rng.get('max', 'N/A'))

            # Format length range (for string columns)
            len_rng = stats.get('length_range', {})
            min_len = sanitize_value(len_rng.get('min', 'N/A')) if len_rng else 'N/A'
            max_len = sanitize_value(len_rng.get('max', 'N/A')) if len_rng else 'N/A'

            # Format top values with sanitization
            top_vals = stats.get('top_values', [])
            if top_vals and isinstance(top_vals[0], dict):
                sanitized_vals = []
                for v in top_vals:
                    val = sanitize_value(v.get('value', ''))
                    count = v.get('count', 0)
                    sanitized_vals.append(f"{val} ({count})")
                top_str = ", ".join(sanitized_vals)
            else:
                top_str = ", ".join([sanitize_value(v) for v in top_vals])

            all_col_stats.append({
                "Table": table,
                "Column": col_name,
                "Type": stats.get('datatype', 'UNKNOWN'),
                "Completeness": completeness,
                "Null %": (nulls / total * 100) if total > 0 else 0,
                "Distinct": stats.get('distinct', 0),
                "Min": min_val,
                "Max": max_val,
                "Min Len": min_len,
                "Max Len": max_len,
                "Top Values": top_str
            })
    
    df_dq = sanitize_dataframe(pd.DataFrame(all_col_stats))

    if df_dq.empty:
        st.info("No profiling data available. Please select tables and columns, then click 'Profile Data'.")
    else:
        # 1. Summary Metrics Header
        m1, m2, m3, m4, m5 = st.columns(5)
        
        avg_comp = df_dq["Completeness"].mean()
        total_records = sum(stats.get('total', 0) for table_stats in st.session_state.profiles.values() for stats in table_stats.values())
        unique_cells = df_dq["Distinct"].sum()
        
        # Grade logic
        grade = "A" if avg_comp > 90 else "B" if avg_comp > 75 else "C" if avg_comp > 50 else "D"
        grade_color = "green" if grade == "A" else "orange" if grade in ["B", "C"] else "red"

        m1.metric("Overall Health", f"{grade}", help=f"Average Score: {avg_comp:.1f}%")
        m2.metric("Avg Completeness", f"{avg_comp:.1f}%")
        m3.metric("Total Attributes", len(df_dq))
        m4.metric("High Quality (>95%)", len(df_dq[df_dq["Completeness"] > 95]))
        m5.metric("Action Needed (<50%)", len(df_dq[df_dq["Completeness"] < 50]), help="Attributes with more than 50% missing or placeholder data.")

        # Second row of technical metrics
        st.write("")
        c1, c2, c3 = st.columns(3)
        c1.caption(f"🚀 Total Rows Analyzed: **{total_records:,}**")
        c2.caption(f"🔑 Total Unique Values: **{unique_cells:,}**")
        
        type_counts = df_dq["Type"].value_counts().to_dict()
        type_str = ", ".join([f"**{k}**: {v}" for k, v in type_counts.items()])
        c3.caption(f"📂 Type Distribution: {type_str}")

        # 2. The Professional DQ Grid + inline export
        toolbar_left, toolbar_right = st.columns([6, 1])
        with toolbar_left:
            st.markdown("**Data Quality Grid**")
        with toolbar_right:
            profile_csv = df_dq.to_csv(index=False, lineterminator='\n', quoting=csv.QUOTE_ALL)
            _render_export_control(
                control_id="bronze_dq_export",
                label="📥 Export CSV",
                payload=profile_csv,
                filename=f"bronze_profiling_grid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )

        if _has_data_editor and _has_column_config:
            st.data_editor(
                df_dq,
                column_config={
                    "Completeness": st.column_config.ProgressColumn(
                        "Completeness",
                        help="Ratio of non-null, non-placeholder values",
                        format="%.1f%%",
                        min_value=0,
                        max_value=100,
                    ),
                    "Null %": st.column_config.NumberColumn(
                        "Null %",
                        help="Includes physical nulls, empty strings, and placeholders like 'N/A'",
                        format="%.1f%%"
                    ),
                    "Min Len": st.column_config.NumberColumn(
                        "Min Len",
                        help="Minimum character length for string columns",
                        format="%d"
                    ),
                    "Max Len": st.column_config.NumberColumn(
                        "Max Len",
                        help="Maximum character length for string columns",
                        format="%d"
                    ),
                    "Top Values": st.column_config.TextColumn(
                        "Top Values",
                        width="large"
                    )
                },
                hide_index=True,
                use_container_width=True,
                disabled=True,
                key="dq_table_6"
            )
        else:
            st.dataframe(df_dq, use_container_width=True)
        st.caption("💡 Use the table headers to sort or filter specific columns. High Completeness ensures better AI modeling.")

        # AI persona insights and charts
        agent_insights = run_profiler_personas(df_dq)
        if agent_insights:
            st.subheader("🤖 AI Agent Observations")
            tab_labels = [f"{ins.agent} ({ins.role})" for ins in agent_insights]
            tabs = st.tabs(tab_labels + ["📈 Quality Charts"])

            for idx, insight in enumerate(agent_insights):
                with tabs[idx]:
                    st.markdown(f"**Role Narrative**: {insight.summary}")
                    if insight.highlights:
                        st.markdown("**Focus Areas**")
                        for highlight in insight.highlights:
                            st.markdown(f"- {highlight}")
                    if insight.rules:
                        st.markdown("**Recommended Rules**")
                        for rule in insight.rules:
                            st.markdown(f"- {rule}")

            with tabs[-1]:
                st.markdown("**Completeness (Lowest 10 Columns)**")
                low_comp = df_dq.sort_values('Completeness').head(10).set_index('Column')[['Completeness']]
                if not low_comp.empty:
                    st.bar_chart(low_comp)
                else:
                    st.info("No columns to plot.")

                st.markdown("**Null Percentage (Highest 10 Columns)**")
                high_nulls = df_dq.sort_values('Null %', ascending=False).head(10).set_index('Column')[['Null %']]
                if not high_nulls.empty:
                    st.bar_chart(high_nulls)
                else:
                    st.info("No columns to plot.")

# --- PHASE 3.5: AI PROFILING COMPANION (Optional) ---
if st.session_state.get('profiles'):
    st.write("---")
    st.header("🤖 AI Profiling Companion (Optional)")
    st.markdown("Get deep AI-powered insights into your data quality with semantic analysis, quality scoring, and automated profiling code generation.")

    with st.expander("ℹ️ About the AI Companion", expanded=False):
        st.markdown("""
        The AI Data Quality Companion uses Snowflake Cortex AI to provide:
        - **Semantic Understanding**: What each column represents and its purpose
        - **Quality Analysis**: Identifies format inconsistencies, patterns, and anomalies
        - **Quality Scoring**: 0-100 score based on completeness and issues found
        - **Actionable Recommendations**: Specific transformations for data cleaning
        - **Profiling Code**: Generates executable Python code for histograms and advanced analysis

        **Note:** Analysis is limited to 10 columns per session for performance.
        """)

    # --- NEW: PERSISTENT RESULTS DISPLAY ---
    if 'ai_analysis' in st.session_state and st.session_state.ai_analysis:
        st.info(f"💡 **AI Analysis Results Available:** {len(st.session_state.ai_analysis)} columns analyzed.")
        if st.button("🗑️ Clear Analysis Results"):
            del st.session_state.ai_analysis
            _rerun()

    # 1. Column Selection (max 10 for performance)
    available_cols = []
    for t_name, col_prof in st.session_state.profiles.items():
        for c_name in col_prof.keys():
            available_cols.append(f"{t_name}.{c_name}")

    # Reset column selection if it contains items no longer in available_cols (e.g. table changed)
    if 'ai_companion_columns' in st.session_state:
        st.session_state.ai_companion_columns = [
            c for c in st.session_state.ai_companion_columns if c in available_cols
        ]

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_ai_cols = st.multiselect(
            "Select columns for deep AI analysis (max 10):",
            available_cols,
            help="AI will analyze patterns, quality issues, and generate profiling code for selected columns",
            key="ai_companion_columns"
        )

    with col2:
        st.metric("Selected", f"{len(selected_ai_cols)}/10")

    if len(selected_ai_cols) > 10:
        st.warning("⚠️ Please select maximum 10 columns for performance reasons.")
        selected_ai_cols = selected_ai_cols[:10]

    # 2. Business Context (optional)
    business_context_ai = st.text_area(
        "Business Context (Optional):",
        placeholder="e.g., These are customer mailing addresses for marketing campaigns. Address quality is critical for deliverability.",
        height=80,
        help="Provide context to help the AI understand the business purpose of these columns",
        key="ai_companion_context"
    )

    # 3. Analysis Execution
    run_analysis_disabled = len(selected_ai_cols) == 0

    if st.button("🧠 Run AI Deep Analysis", disabled=run_analysis_disabled, use_container_width=True, key="run_ai_analysis"):
        from ai.data_quality_companion import DataQualityCompanion

        try:
            companion = DataQualityCompanion(
                account=account,
                user=user,
                role=role,
                warehouse=warehouse,
                database=database,
                schema=schema
            )
        except Exception as e:
            st.error(f"❌ Failed to initialize AI Companion: {str(e)}")
            import traceback
            error_msg = traceback.format_exc()
            st.code(error_msg, language="python")
            print(f"[ai_companion] Initialization Error at {datetime.now()}: {error_msg}")
            st.stop()
        results = {}

        with _status_context(f"Analyzing {len(selected_ai_cols)} columns...", expanded=True) as status:
            progress = st.progress(0, text="Starting analysis...")

            for idx, full_col in enumerate(selected_ai_cols):
                table, col = full_col.split('.', 1)
                status.update(label=f"Analyzing {full_col}... ({idx+1}/{len(selected_ai_cols)})")
                progress.progress((idx) / len(selected_ai_cols), text=f"Analyzing {full_col}...")

                try:
                    # Pass the schema and database explicitly
                    analysis = companion.analyze_column(
                        table_name=table, 
                        column_name=col,
                        profile=st.session_state.profiles[table][col],
                        description=st.session_state.desc_data.get('tables', {}).get(table, {}).get(col, "") if st.session_state.get('desc_data') is not None else "",
                        business_context=business_context_ai,
                        schema_name=schema,
                        database_name=database
                    )
                    results[full_col] = analysis
                except Exception as e:
                    import traceback
                    error_details = traceback.format_exc()
                    st.error(f"❌ Error analyzing `{full_col}`")
                    st.code(error_details, language="python")
                    print(f"[ai_companion] Analysis Error ({full_col}) at {datetime.now()}: {error_details}")
                    continue

                progress.progress((idx+1) / len(selected_ai_cols), text=f"Completed {idx+1}/{len(selected_ai_cols)}")
                st.write(f"  ✓ Finished analyzing `{full_col}`")

            status.update(label=f"✅ Analysis complete! Successfully analyzed {len(results)} columns.", state="complete")
            progress.progress(1.0, text="Analysis complete!")

            status.update(label=f"✅ Analysis complete!", state="complete")
            progress.progress(1.0, text="Analysis complete!")

        st.session_state.ai_analysis = results
        # We don't rerun immediately so the user can see the success message
        # But we want the dashboard to show up. 
        # Actually, rerun is better to ensure the whole page sees the new state.
        st.success(f"✅ Successfully analyzed {len(results)} columns!")
        st.write("📊 Results are now available below.")
        _rerun()

    # 4. Results Display (5 Tabs)
    if 'ai_analysis' in st.session_state and st.session_state.ai_analysis:
        st.markdown("---")
        st.subheader("📊 AI Analysis Results")

        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
            "📊 Overview",
            "🔍 Detailed Analysis",
            "🛠️ Remediation & Transformation",
            "💻 Generated Code",
            "📈 Visualizations",
            "📥 Export"
        ])

        with tab1:
            # Summary metrics with quality score distribution
            scores = [r['quality_score'] for r in st.session_state.ai_analysis.values()]
            avg_score = sum(scores) / len(scores) if scores else 0

            metric_cols = st.columns(4)
            with metric_cols[0]:
                st.metric("Average Quality Score", f"{avg_score:.1f}/100")
            with metric_cols[1]:
                high_quality = len([s for s in scores if s >= 80])
                st.metric("High Quality (≥80)", high_quality)
            with metric_cols[2]:
                medium_quality = len([s for s in scores if 50 <= s < 80])
                st.metric("Medium Quality (50-79)", medium_quality)
            with metric_cols[3]:
                low_quality = len([s for s in scores if s < 50])
                st.metric("Low Quality (<50)", low_quality)

            # Sortable table with columns and scores
            import pandas as pd
            overview_data = []
            for col, r in st.session_state.ai_analysis.items():
                overview_data.append({
                    "Column": col,
                    "Quality Score": r['quality_score'],
                    "Type": r['column_type_inference'],
                    "Issues Found": len(r['quality_issues']),
                    "Recommendations": len(r['recommendations'])
                })

            df_overview = pd.DataFrame(overview_data)
            if _has_column_config:
                st.dataframe(
                    df_overview,
                    column_config={
                        "Quality Score": st.column_config.ProgressColumn(
                            "Quality Score",
                            help="Quality score from 0-100",
                            min_value=0,
                            max_value=100,
                            format="%d"
                        )
                    },
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.dataframe(df_overview, use_container_width=True)

        with tab2:
            # Expandable sections per column
            for col, analysis in st.session_state.ai_analysis.items():
                quality_color = "🟢" if analysis['quality_score'] >= 80 else "🟡" if analysis['quality_score'] >= 50 else "🔴"

                with st.expander(f"{quality_color} **{col}** - Quality: {analysis['quality_score']}/100", expanded=False):
                    st.markdown(f"**Semantic Type:** `{analysis['column_type_inference']}`")
                    st.markdown(f"**Analysis:**")
                    st.markdown(analysis['analysis_text'])

                    if analysis['quality_issues']:
                        st.markdown("**Quality Issues Found:**")
                        for issue in analysis['quality_issues']:
                            severity = issue.get('severity', 'medium').lower()
                            severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
                            affected = issue.get('affected_pct', 0)
                            st.markdown(f"- {severity_icon} **{issue['issue']}** (Severity: {severity}, ~{affected:.1f}% affected)")
                    else:
                        st.success("No significant quality issues found!")

                    st.markdown("**Recommendations:**")
                    if analysis['recommendations']:
                        for rec in analysis['recommendations']:
                            st.markdown(f"- {rec}")
        with tab3:
            # Remediation & Transformation Logic
            if st.session_state.ai_analysis:
                col_list = list(st.session_state.ai_analysis.keys())
                col_spec = st.selectbox("Select column for transformation:", col_list, key="rem_col_selector")

                if col_spec:
                    analysis = st.session_state.ai_analysis[col_spec]
                    
                    st.markdown("### 🛠️ Best-Fit Transformation Logic")
                    logic = analysis.get('transformation_logic', 'Not available.')
                    st.info(logic)

                    st.markdown("### 📜 Suggested Remediation SQL (Snowflake)")
                    sql = analysis.get('remediation_sql', '-- No SQL suggested')
                    st.code(sql, language='sql')

                    st.markdown("---")
                    col_act1, col_act2 = st.columns(2)
                    with col_act1:
                        if st.button("👍 Looks Good", key=f"app_{col_spec}"):
                            st.success(f"Logic for {col_spec} accepted!")
                    with col_act2:
                        if st.button("🔄 Suggest Better", key=f"ref_{col_spec}"):
                            # Re-run AI analysis with enhanced context for better SQL
                            from ai.data_quality_companion import DataQualityCompanion

                            with st.spinner(f"Re-analyzing {col_spec} for better suggestions..."):
                                try:
                                    # Parse table and column from col_spec (format: "TABLE.COLUMN")
                                    table, col = col_spec.split('.', 1)

                                    # Get connection params from sidebar
                                    conn_account = st.session_state.get('account_input', '')
                                    conn_user = st.session_state.get('user_input', '')
                                    conn_role = st.session_state.get('role_input', '')
                                    conn_warehouse = st.session_state.get('warehouse_input', '')
                                    conn_database = st.session_state.get('database_input', '')
                                    conn_schema = st.session_state.get('schema_input', '')

                                    # Initialize companion
                                    companion = DataQualityCompanion(
                                        account=conn_account,
                                        user=conn_user,
                                        role=conn_role,
                                        warehouse=conn_warehouse,
                                        database=conn_database,
                                        schema=conn_schema
                                    )

                                    # Get profile and description from session state
                                    profile = st.session_state.profiles[table][col]
                                    description = st.session_state.desc_data.get('tables', {}).get(table, {}).get(col, "") if st.session_state.get('desc_data') is not None else ""

                                    # Get original business context and enhance it
                                    original_context = st.session_state.get('ai_companion_context', '')
                                    enhanced_context = f"""{original_context}

REFINEMENT REQUEST:
The previous SQL suggestion was not satisfactory. Please provide IMPROVED, PRODUCTION-READY SQL with:
1. Concrete transformations (not TODO placeholders)
2. Proper error handling (TRY_CAST, COALESCE, IFNULL)
3. Real-world examples (e.g., date format conversions, string cleaning, validation logic)
4. Comprehensive quality checks
5. Performance-optimized approach

Focus on practical, immediately usable SQL code."""

                                    # Re-run analysis with enhanced context
                                    new_analysis = companion.analyze_column(
                                        table_name=table,
                                        column_name=col,
                                        profile=profile,
                                        description=description,
                                        business_context=enhanced_context,
                                        schema_name=conn_schema,
                                        database_name=conn_database
                                    )

                                    # Update session state with new analysis
                                    st.session_state.ai_analysis[col_spec] = new_analysis

                                    st.success(f"✅ Generated improved suggestions for {col_spec}!")
                                    _rerun()

                                except Exception as e:
                                    import traceback
                                    st.error(f"❌ Failed to regenerate analysis: {str(e)}")
                                    st.code(traceback.format_exc(), language="python")

        with tab4:
            # Code display with validation and execution
            if st.session_state.ai_analysis:
                col_list = list(st.session_state.ai_analysis.keys())
                col_selector = st.selectbox("Select column to view code:", col_list, key="code_col_selector")

                if col_selector:
                    code = st.session_state.ai_analysis[col_selector]['generated_code']

                    if code:
                        from ai.code_validator import validate_code
                        is_safe, error = validate_code(code)

                        if is_safe:
                            st.success("✅ Code validated as safe")
                        else:
                            st.error(f"❌ Security issue detected: {error}")

                        st.code(code, language='python', line_numbers=True)

                        if is_safe:
                            st.info("💡 **Note:** Code execution generates histograms automatically during analysis. You can re-run analysis to regenerate.")
                        else:
                            st.warning("⚠️ This code failed security validation and was not executed.")
                    else:
                        st.info("No code was generated for this column.")

        with tab5:
            # Grid of histograms
            st.markdown("**Generated Histograms and Visualizations**")

            histograms = [(col, analysis) for col, analysis in st.session_state.ai_analysis.items()
                         if analysis.get('histogram_path')]

            if histograms:
                cols_grid = st.columns(2)
                for idx, (col, analysis) in enumerate(histograms):
                    histogram_path = analysis['histogram_path']
                    if histogram_path and os.path.exists(histogram_path):
                        with cols_grid[idx % 2]:
                            st.image(histogram_path, caption=col)
                    else:
                        with cols_grid[idx % 2]:
                            st.warning(f"Histogram for {col} not found")
            else:
                st.info("No histograms were generated. This may occur if code generation failed or data was insufficient.")

        with tab6:
            # Export bundle
            st.markdown("**Download Analysis Results**")

            # Create JSON export
            export_data = {
                "analysis_metadata": {
                    "generated_at": datetime.now().isoformat(),
                    "num_columns_analyzed": len(st.session_state.ai_analysis),
                    "average_quality_score": avg_score,
                    "business_context": business_context_ai
                },
                "column_analyses": st.session_state.ai_analysis
            }

            json_str = json.dumps(export_data, indent=2, default=str)

            col_export1, col_export2 = st.columns(2)

            with col_export1:
                _render_export_control(
                    control_id="ai_analysis_json",
                    label="📄 Download Analysis (JSON)",
                    payload=json_str,
                    filename=f"ai_profiling_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json"
                )

            with col_export2:
                # Create CSV export
                import pandas as pd
                csv_data = []
                for col, analysis in st.session_state.ai_analysis.items():
                    table_name, col_name = col.split('.', 1)
                    csv_data.append({
                        "Table": table_name,
                        "Column": col_name,
                        "Quality Score": analysis['quality_score'],
                        "Semantic Type": analysis['column_type_inference'],
                        "Issues Count": len(analysis['quality_issues']),
                        "Analysis": analysis['analysis_text']
                    })

                df_export = sanitize_dataframe(pd.DataFrame(csv_data))
                csv_str = df_export.to_csv(index=False, lineterminator='\n', quoting=csv.QUOTE_ALL)

                _render_export_control(
                    control_id="ai_analysis_csv",
                    label="📊 Download Summary (CSV)",
                    payload=csv_str,
                    filename=f"ai_profiling_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )

            st.info("💡 **Tip:** Histograms are generated during analysis. Use the Download buttons above to export results.")

# --- PHASE 2: AI MODELING ---
st.write("---")

if is_mapping_mode:
    # --- MAPPING MODE PIPELINE ---
    st.subheader("4️⃣ AI Source-to-Target Mapping")
    
    if not st.session_state.profiles:
        st.info("Please run Bronze profiling above first.")
    elif not selected_silver_tables:
        st.info("Please select Silver target tables above.")
    else:
        # Business Context Input
        st.markdown("**📝 Business Context (Optional)**")
        business_context = st.text_area(
            "Describe how the Source tables relate to Target, naming conventions, or any mapping hints:",
            placeholder="Example: The Source tables are different CRM systems. The Target table is a unified customer view. CUST_ID maps to CUSTOMER_KEY, EMAIL maps to EMAIL_ADDRESS...",
            height=100,
            key="business_context"
        )

        refresh_descriptions_requested = st.checkbox(
            "🔁 Refresh column descriptions on next run",
            value=False,
            key="refresh_descriptions_toggle",
            help="Uncheck to reuse cached Bronze/Silver descriptions for faster reruns"
        )

        # STEP 5: Business Rules Configuration
        with st.expander("⚙️ Configure Business Rules (Optional)", expanded=False):
            st.write("**Business rules control which mappings are allowed and how scores are adjusted.**")

            from ai.business_rules_engine import load_rules, save_rules, get_rules_statistics

            # Load rules
            if 'business_rules_config' not in st.session_state:
                st.session_state.business_rules_config = load_rules()

            rules_config = st.session_state.business_rules_config
            rules = rules_config.get('rules', [])

            # Display statistics
            stats = get_rules_statistics(rules_config)
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Rules", stats['total_rules'])
            col2.metric("Enabled", stats['enabled_rules'])
            col3.metric("Filters", stats['filter_rules'])

            st.write("---")
            st.write("**Active Rules:**")

            # Display each rule with enable/disable toggle
            rules_changed = False
            for idx, rule in enumerate(rules):
                rule_id = rule.get('rule_id', f'rule_{idx}')
                rule_name = rule.get('rule_name', 'Unknown Rule')
                rule_type = rule.get('rule_type', 'UNKNOWN')
                rule_enabled = rule.get('enabled', True)
                action = rule.get('action', {})
                reason = action.get('reason', '')

                col1, col2, col3 = st.columns([3, 1, 1])

                with col1:
                    st.write(f"**{rule_name}**")
                    st.caption(reason)

                with col2:
                    # Rule type emoji
                    rule_type_emoji = {"BOOST": "⬆️", "FILTER": "🚫", "PENALTY": "⬇️"}
                    st.caption(f"{rule_type_emoji.get(rule_type, '❓')} {rule_type}")

                with col3:
                    # Enable/disable toggle
                    new_enabled = st.checkbox(
                        "Enabled",
                        value=rule_enabled,
                        key=f"rule_enabled_{rule_id}_{idx}"
                    )
                    if new_enabled != rule_enabled:
                        rules_config['rules'][idx]['enabled'] = new_enabled
                        rules_changed = True

            # Save button
            if st.button("💾 Save Rules Configuration", key="save_rules"):
                save_rules(rules_config)
                st.session_state.business_rules_config = rules_config
                st.success("✅ Rules configuration saved!")

            if rules_changed:
                st.session_state.business_rules_config = rules_config
                st.info("⚠️ Rules updated. Click 'Save Rules Configuration' to persist changes.")

            # Show thresholds
            st.write("---")
            st.write("**Confidence Thresholds:**")
            thresholds = rules_config.get('thresholds', {})
            col1, col2, col3 = st.columns(3)
            col1.metric("Warning Threshold", f"{thresholds.get('min_confidence_warning', 60)}%")
            col2.metric("Min for Approval", f"{thresholds.get('min_confidence_for_approval', 50)}%")
            col3.metric("Auto-Approve", f"{thresholds.get('auto_approve_threshold', 90)}%")

        if st.button("🧠 Run AI Mapping", use_container_width=True):
            with _status_context("🔍 Running Iterative AI Mapping...", expanded=True) as status:
                try:
                    # 1. Profile Silver tables - use silver database
                    st.write("📊 Profiling target tables...")
                    silver_profiles = {}
                    silver_db = st.session_state.get('silver_database', database)
                    conn = get_connection()
                    for s_table in selected_silver_tables:
                        os.environ["SNOWFLAKE_DATABASE"] = silver_db
                        os.environ["SNOWFLAKE_SCHEMA"] = selected_silver_schema
                        prof = profile_table_cached(account, user, role, warehouse, silver_db, selected_silver_schema, s_table, (), 100)
                        silver_profiles[s_table] = prof
                        st.write(f"  ✓ Profiled `{silver_db}.{selected_silver_schema}.{s_table}`")
                    _safe_close(conn)

                    
                    # 2. Count columns for progress
                    total_target_cols = sum(len(cols) for cols in silver_profiles.values() if isinstance(cols, dict))
                    total_source_cols = sum(len(cols) for cols in st.session_state.profiles.values() if isinstance(cols, dict))
                    st.write(f"🎯 Target: {total_target_cols} Silver columns | Source: {total_source_cols} Bronze columns")
                    
                    # 3. Generate semantic descriptions for better matching (with caching)
                    st.write("📝 Preparing semantic descriptions for columns...")

                    bronze_signature = _profiles_signature(st.session_state.profiles)
                    bronze_cache_key = f"bronze::{schema}::{bronze_signature}"
                    bronze_descriptions = None
                    if not refresh_descriptions_requested:
                        bronze_descriptions = _get_cached_descriptions(bronze_cache_key, bronze_signature)
                        if bronze_descriptions:
                            st.caption("♻️ Reusing cached Bronze descriptions")

                    if bronze_descriptions is None:
                        bronze_descriptions = generate_column_descriptions(
                            st.session_state.profiles,
                            schema,
                            account=account,
                            user=user,
                            role=role,
                            warehouse=warehouse,
                            database=database,
                            schema=schema
                        )
                        _store_description_cache(bronze_cache_key, bronze_signature, bronze_descriptions)
                    st.write(f"  ✓ Source descriptions: {sum(len(v) for v in bronze_descriptions.values())} columns")

                    silver_signature = _profiles_signature(silver_profiles)
                    silver_cache_key = f"silver::{silver_db}.{selected_silver_schema}::{silver_signature}"
                    silver_descriptions = None
                    if not refresh_descriptions_requested:
                        silver_descriptions = _get_cached_descriptions(silver_cache_key, silver_signature)
                        if silver_descriptions:
                            st.caption("♻️ Reusing cached Silver descriptions")

                    if silver_descriptions is None:
                        silver_descriptions = generate_column_descriptions(
                            silver_profiles,
                            selected_silver_schema,
                            account=account,
                            user=user,
                            role=role,
                            warehouse=warehouse,
                            database=database,
                            schema=schema
                        )
                        _store_description_cache(silver_cache_key, silver_signature, silver_descriptions)
                    st.write(f"  ✓ Target descriptions: {sum(len(v) for v in silver_descriptions.values())} columns")
                    
                    # 4. Compute embedding similarity for candidate filtering
                    st.write("🔗 Computing embedding similarity matrix...")
                    similarity_hints = compute_embedding_similarity(
                        bronze_descriptions, 
                        silver_descriptions,
                        account=account,
                        user=user,
                        role=role,
                        warehouse=warehouse,
                        database=database,
                        schema=schema
                    )
                    if similarity_hints:
                        st.write(f"  ✓ Similarity computed for {len(similarity_hints)} target columns")
                    
                    # 5. Create progress bar
                    progress_bar = st.progress(0, text="Initializing iterative mapping...")
                    
                    def update_progress(current, total, message):
                        pct = current / total if total > 0 else 0
                        progress_bar.progress(pct, text=message)
                    
                    # 6. Run iterative AI mapping with descriptions and similarity hints
                    st.write("🤖 Running iterative batch mapping (5 target × 15 source per call)...")
                    mappings = run_iterative_mapping(
                        source_profiles=st.session_state.profiles,
                        target_profiles=silver_profiles,
                        source_schema_name=schema,  # Source schema from sidebar
                        target_schema_name=selected_silver_schema,
                        business_context=business_context,
                        progress_callback=update_progress,
                        source_descriptions=bronze_descriptions,
                        target_descriptions=silver_descriptions,
                        similarity_hints=similarity_hints,
                        # Pass connection context explicitly (or let it use corrected get_snowflake_connection)
                        account=account,
                        user=user,
                        role=role,
                        warehouse=warehouse,
                        database=database,
                        schema=schema
                    )


                    progress_bar.progress(1.0, text="Mapping complete!")

                    # STEP 5: Apply Business Rules
                    try:
                        from ai.business_rules_engine import apply_rules

                        progress_bar.progress(0.92, text="Applying business rules...")
                        st.write("⚙️ Applying business rules...")
                        mappings = apply_rules(
                            mappings,
                            st.session_state.profiles,
                            st.session_state.get('business_rules_config')
                        )
                        st.write(f"  ✓ Business rules applied ({len(mappings)} mappings after filtering)")
                    except Exception as rules_err:
                        print(f"[Rules] Non-critical error: {rules_err}")
                        # Rules failure should not break the mapping workflow

                    st.session_state.mapping_results = mappings

                    # Store mapping context in session state for refinement
                    st.session_state.mapping_context = {
                        'silver_profiles': silver_profiles,
                        'bronze_descriptions': bronze_descriptions,
                        'silver_descriptions': silver_descriptions,
                        'similarity_hints': similarity_hints,
                        'selected_silver_schema': selected_silver_schema,
                        'source_schema': schema,
                        'business_context': business_context
                    }

                    # STEP 1: Post-Mapping Validation
                    try:
                        from ai.mapping_validator import validate_all_mappings

                        progress_bar.progress(0.95, text="Validating mappings...")
                        validation_results = validate_all_mappings(
                            mappings,
                            st.session_state.profiles,
                            target_profiles=silver_profiles,
                            min_confidence=60
                        )
                        st.session_state.mapping_validation_results = validation_results

                        # Show validation summary
                        if validation_results['invalid_count'] > 0:
                            st.warning(f"⚠️ {validation_results['invalid_count']} mappings failed validation")

                    except Exception as val_err:
                        print(f"[Validation] Non-critical error: {val_err}")
                        # Validation failure should not break the mapping workflow

                    status.update(label=f"✅ Iterative Mapping Complete! ({len(mappings)} mappings)", state="complete", expanded=False)
                    
                except Exception as e:
                    import traceback
                    st.error(f"Mapping Error: {e}")
                    st.code(traceback.format_exc())
                    status.update(label="❌ Mapping Failed", state="error", expanded=True)

        
        # Display mapping results with rich visualizations
        if "mapping_results" in st.session_state and st.session_state.mapping_results:
            st.write("---")
            st.subheader("📊 Mapping Results & Visualization")
            
            df_map = pd.DataFrame(st.session_state.mapping_results)

            # STEP 2: Confidence Dashboard
            st.write("**Mapping Confidence Summary:**")

            # Calculate confidence distribution
            excellent = len(df_map[df_map['MappingScore'] >= 90])
            good = len(df_map[(df_map['MappingScore'] >= 70) & (df_map['MappingScore'] < 90)])
            fair = len(df_map[(df_map['MappingScore'] >= 60) & (df_map['MappingScore'] < 70)])
            low = len(df_map[df_map['MappingScore'] < 60])

            # Display metrics
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("🟢 Excellent (≥90%)", excellent)
            col2.metric("🟢 Good (70-89%)", good)
            col3.metric("🟡 Fair (60-69%)", fair)
            col4.metric("🔴 Low (<60%)", low)

            # Confidence distribution bar chart
            try:
                import plotly.graph_objects as go
                fig = go.Figure(data=[go.Bar(
                    x=['Excellent (≥90%)', 'Good (70-89%)', 'Fair (60-69%)', 'Low (<60%)'],
                    y=[excellent, good, fair, low],
                    marker_color=['#2ecc71', '#27ae60', '#f39c12', '#e74c3c'],
                    text=[excellent, good, fair, low],
                    textposition='auto',
                )])
                fig.update_layout(
                    title="Mapping Confidence Distribution",
                    xaxis_title="Confidence Level",
                    yaxis_title="Number of Mappings",
                    height=300,
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                pass  # Skip chart if Plotly not available

            st.write("---")

            # Create tabs for different views - ENHANCED WITH AI FEATURES
            tab_table, tab_sankey, tab_json, tab_justify, tab_export, tab_validation, tab_ai_chat, tab_patterns, tab_dashboard = st.tabs([
                "📋 Mapping Table",
                "🔀 Sankey Diagram",
                "🧠 JSON Model",
                "🔍 AI Justifications",
                "📥 Export",
                "🔍 Validation Report",
                "🤖 AI Chat",
                "📚 Pattern Library",
                "📊 Live Dashboard"
            ])
            
            with tab_table:
                st.write("**Complete Column-Level Mapping with Confidence Scores**")

                # STEP 2: Low Confidence Warning Banner
                low_confidence_count = len(df_map[df_map['MappingScore'] < 60])

                if low_confidence_count > 0:
                    st.warning(f"""
⚠️ **{low_confidence_count} mappings have low confidence (< 60%)**

These mappings may need manual review before approval. Check the Validation Report tab for details.
""")

                # STEP 3: Bulk Approve/Reject Actions
                st.write("**Bulk Actions:**")
                col1, col2, col3 = st.columns(3)

                with col1:
                    if st.button("✅ Approve All High Confidence (≥90%)", use_container_width=True, key="bulk_approve_high"):
                        from ai.approval_manager import bulk_approve

                        high_conf_mappings = df_map[df_map['MappingScore'] >= 90].to_dict('records')
                        approved_count = bulk_approve(
                            high_conf_mappings,
                            min_confidence=90,
                            reviewer="user",
                            approval_file=None
                        )
                        st.success(f"✅ Approved {approved_count} high-confidence mappings!")
                        _rerun()

                with col2:
                    if st.button("⚠️ Flag Low Confidence (<60%)", use_container_width=True, key="bulk_flag_low"):
                        from ai.approval_manager import bulk_flag_low_confidence

                        low_conf_mappings = df_map[df_map['MappingScore'] < 60].to_dict('records')
                        flagged_count = bulk_flag_low_confidence(
                            low_conf_mappings,
                            max_confidence=60,
                            reviewer="user",
                            approval_file=None
                        )
                        st.warning(f"⚠️ Flagged {flagged_count} low-confidence mappings for review!")
                        _rerun()

                with col3:
                    # Show approval statistics
                    from ai.approval_manager import get_approval_statistics
                    stats = get_approval_statistics()
                    if stats['total_reviews'] > 0:
                        st.metric("Approval Rate", f"{stats['approval_rate']:.0f}%")
                    else:
                        st.metric("Approval Rate", "N/A")

                st.write("---")

                # Add warning indicator column to dataframe
                df_map_display = df_map.copy()
                df_map_display['⚠️'] = df_map_display['MappingScore'].apply(
                    lambda score: '⚠️ LOW' if score < 60 else '✅ OK'
                )

                # ENHANCEMENT: Add profiled data columns (Hybrid Approach)
                def get_source_profile_data(row):
                    """Extract profile data for source column"""
                    if row['SourceColumn'] in ['UNMAPPED', 'N/A']:
                        return {'completeness': 0, 'quality': 0, 'sample': '', 'full_profile': None}

                    table = row['SourceTable']
                    col = row['SourceColumn']

                    if table in st.session_state.profiles and col in st.session_state.profiles[table]:
                        profile = st.session_state.profiles[table][col]

                        # Calculate completeness
                        total = profile.get('total', 0)
                        nulls = profile.get('nulls', 0)
                        completeness = ((total - nulls) / total * 100) if total > 0 else 0

                        # Get quality score (calculate from profile data if not enriched)
                        quality = profile.get('quality_score', 0)
                        if quality == 0 and total > 0:
                            comp_score = min(completeness, 100)
                            distinct = profile.get('distinct', 0)
                            cardinality_pct = (distinct / total * 100) if total > 0 else 0
                            cardinality_score = 100 if 1 < cardinality_pct < 95 else max(50, 100 - abs(cardinality_pct - 50))
                            quality = int(comp_score * 0.7 + cardinality_score * 0.3)

                        # Get sample values
                        top_values = profile.get('top_values', [])
                        sample = ', '.join([str(v.get('value', ''))[:20] for v in top_values[:3]]) if top_values else ''

                        return {
                            'completeness': completeness,
                            'quality': quality,
                            'sample': sample,
                            'full_profile': profile  # Store full profile for detailed view
                        }

                    return {'completeness': 0, 'quality': 0, 'sample': '', 'full_profile': None}

                # Add key metric columns (scannable)
                profile_data = df_map_display.apply(get_source_profile_data, axis=1)
                df_map_display['Quality'] = profile_data.apply(lambda x: f"{x['quality']:.0f}")
                df_map_display['Completeness'] = profile_data.apply(lambda x: f"{x['completeness']:.0f}%")

                # Add Profile Data column showing full JSON
                def format_profile_json(x):
                    p = x['full_profile']
                    if not p or not isinstance(p, dict):
                        return ""
                    # Convert to JSON with safe defaults for non-serializable types
                    # Remove indent to keep as single line for CSV compatibility
                    try:
                        return json.dumps(p, default=str, ensure_ascii=False)
                    except Exception as e:
                        return f"<serialization error: {str(e)}>"

                df_map_display['ProfileData'] = profile_data.apply(format_profile_json)

                # Store full profiles for expandable view
                st.session_state['mapping_profiles'] = profile_data.apply(lambda x: x['full_profile']).tolist()

                # PHASE 2: Display Mapping Confidence prominently
                if _has_column_config:
                    st.dataframe(
                        df_map_display,
                        use_container_width=True,
                        height=400,
                        column_config={
                            "MappingScore": st.column_config.ProgressColumn(
                                "Mapping Confidence",
                                help="AI confidence in this mapping (90-100: Excellent, 70-89: Good, 50-69: Fair, <50: Uncertain)",
                                min_value=0,
                                max_value=100,
                                format="%d%%"
                            )
                        }
                    )
                else:
                    st.dataframe(df_map_display, use_container_width=True, height=400)

                # ENHANCEMENT: Full Profile Data Viewer (Expandable)
                st.write("---")
                with st.expander("📊 View Full Profile Data (Detailed JSON View)", expanded=False):
                    st.info("💡 Select a row to view complete profiling data for the source column")

                    # Row selector
                    col1, col2 = st.columns([2, 3])
                    with col1:
                        # Create selection options
                        row_options = [
                            f"{i+1}. {row['SourceTable']}.{row['SourceColumn']}"
                            for i, row in df_map_display.iterrows()
                            if row['SourceColumn'] not in ['UNMAPPED', 'N/A']
                        ]

                        if row_options:
                            selected_row_str = st.selectbox(
                                "Select Source Column:",
                                row_options,
                                key="profile_viewer_selector"
                            )

                            # Extract row index from selection
                            selected_idx = int(selected_row_str.split('.')[0]) - 1

                            # Get full profile
                            full_profile = st.session_state.get('mapping_profiles', [])[selected_idx]

                            if full_profile:
                                with col2:
                                    st.metric(
                                        "Data Type",
                                        full_profile.get('data_type', 'Unknown')
                                    )

                                # Display tabs for different profile sections
                                tab1, tab2, tab3, tab4 = st.tabs([
                                    "📊 Statistics",
                                    "📈 Top Values",
                                    "🔍 Quality Issues",
                                    "💾 Raw JSON"
                                ])

                                with tab1:
                                    st.subheader("Statistical Summary")
                                    col_a, col_b, col_c = st.columns(3)

                                    with col_a:
                                        st.metric("Total Records", f"{full_profile.get('total', 0):,}")
                                        st.metric("Null Count", f"{full_profile.get('nulls', 0):,}")

                                    with col_b:
                                        total = full_profile.get('total', 0)
                                        nulls = full_profile.get('nulls', 0)
                                        completeness = ((total - nulls) / total * 100) if total > 0 else 0
                                        st.metric("Completeness", f"{completeness:.1f}%")
                                        st.metric("Unique Values", f"{full_profile.get('unique_count', 0):,}")

                                    with col_c:
                                        st.metric("Quality Score", f"{full_profile.get('quality_score', 0):.0f}")
                                        if 'cardinality' in full_profile:
                                            st.metric("Cardinality", f"{full_profile['cardinality']:.2f}%")

                                    # Numeric stats if available
                                    if full_profile.get('data_type') in ['NUMBER', 'FLOAT', 'INTEGER']:
                                        st.write("**Numeric Statistics:**")
                                        stat_cols = st.columns(5)
                                        if 'min' in full_profile:
                                            stat_cols[0].metric("Min", f"{full_profile['min']:.2f}")
                                        if 'max' in full_profile:
                                            stat_cols[1].metric("Max", f"{full_profile['max']:.2f}")
                                        if 'mean' in full_profile:
                                            stat_cols[2].metric("Mean", f"{full_profile['mean']:.2f}")
                                        if 'median' in full_profile:
                                            stat_cols[3].metric("Median", f"{full_profile['median']:.2f}")
                                        if 'stddev' in full_profile:
                                            stat_cols[4].metric("Std Dev", f"{full_profile['stddev']:.2f}")

                                with tab2:
                                    st.subheader("Top Values Distribution")
                                    top_values = full_profile.get('top_values', [])
                                    if top_values:
                                        # Create DataFrame for better display
                                        top_df = pd.DataFrame(top_values)
                                        if not top_df.empty and 'value' in top_df.columns:
                                            top_df['percentage'] = (top_df['count'] / full_profile.get('total', 1) * 100).round(2)
                                            if _has_column_config:
                                                st.dataframe(
                                                    top_df[['value', 'count', 'percentage']],
                                                    use_container_width=True,
                                                    column_config={
                                                        "value": "Value",
                                                        "count": "Count",
                                                        "percentage": st.column_config.ProgressColumn(
                                                            "Percentage",
                                                            format="%.2f%%",
                                                            min_value=0,
                                                            max_value=100
                                                        )
                                                    }
                                                )
                                            else:
                                                st.dataframe(top_df[['value', 'count', 'percentage']], use_container_width=True)
                                    else:
                                        st.info("No top values data available")

                                with tab3:
                                    st.subheader("Quality Issues")
                                    quality_issues = full_profile.get('quality_issues', [])
                                    if quality_issues:
                                        for issue in quality_issues:
                                            severity = issue.get('severity', 'info')
                                            icon = "🔴" if severity == 'high' else "🟡" if severity == 'medium' else "🔵"
                                            st.write(f"{icon} **{issue.get('issue', 'Unknown Issue')}**")
                                            if 'description' in issue:
                                                st.write(f"   {issue['description']}")
                                    else:
                                        st.success("✅ No quality issues detected")

                                    # Show recommendations if available
                                    recommendations = full_profile.get('recommendations', [])
                                    if recommendations:
                                        st.write("**Recommendations:**")
                                        for rec in recommendations:
                                            st.write(f"• {rec}")

                                with tab4:
                                    st.subheader("Complete Profile (JSON)")
                                    st.json(full_profile, expanded=False)

                            else:
                                st.warning("No profile data available for this column")
                        else:
                            st.info("No mapped columns available. Please run mapping first.")

                st.write("---")

                # Statistics - PHASE 2 ENHANCED
                total_mappings = len(df_map)
                mapped_count = len(df_map[df_map['SourceColumn'] != 'UNMAPPED'])
                unmapped_count = len(df_map[df_map['SourceColumn'] == 'UNMAPPED'])

                # Calculate average confidence
                mapped_df = df_map[df_map['SourceColumn'] != 'UNMAPPED']
                avg_confidence = mapped_df['MappingScore'].mean() if len(mapped_df) > 0 else 0
                high_confidence = len(mapped_df[mapped_df['MappingScore'] >= 70])

                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Mappings", total_mappings)
                with col2:
                    st.metric("Mapped Columns", mapped_count, delta=f"{mapped_count/total_mappings*100:.1f}%" if total_mappings else "0%")
                with col3:
                    # PHASE 2: Show average confidence
                    confidence_color = "🟢" if avg_confidence >= 70 else "🟡" if avg_confidence >= 50 else "🔴"
                    st.metric("Avg Confidence", f"{confidence_color} {avg_confidence:.0f}%")
                with col4:
                    st.metric("High Confidence (≥70%)", high_confidence, delta=f"{high_confidence/mapped_count*100:.0f}%" if mapped_count > 0 else "0%")
            
            with tab_sankey:
                st.write("**Visual Column-Level Mapping Flow**")
                try:
                    import plotly.graph_objects as go
                    
                    # Build Sankey data - only for mapped columns
                    mapped_df = df_map[df_map['SourceColumn'] != 'UNMAPPED'].copy()
                    
                    if len(mapped_df) > 0:
                        # Get unique source and target nodes
                        source_nodes = mapped_df.apply(lambda r: f"{r.get('SourceTable', 'SRC')}.{r.get('SourceColumn', 'col')}", axis=1).unique().tolist()
                        target_nodes = mapped_df.apply(lambda r: f"{r.get('TargetTable', 'TGT')}.{r.get('TargetColumn', 'col')}", axis=1).unique().tolist()
                        
                        # Create node list: sources first, then targets
                        all_nodes = source_nodes + target_nodes
                        node_indices = {node: i for i, node in enumerate(all_nodes)}
                        
                        # Build links
                        sources = []
                        targets = []
                        values = []
                        hover_texts = []
                        
                        for _, row in mapped_df.iterrows():
                            src_node = f"{row.get('SourceTable', 'SRC')}.{row.get('SourceColumn', 'col')}"
                            tgt_node = f"{row.get('TargetTable', 'TGT')}.{row.get('TargetColumn', 'col')}"
                            
                            if src_node in node_indices and tgt_node in node_indices:
                                sources.append(node_indices[src_node])
                                targets.append(node_indices[tgt_node])
                                values.append(1)
                                hover_texts.append(row.get('Justification', '')[:100])
                        
                        # Colors: blue for source, green for target
                        node_colors = ['#3498db'] * len(source_nodes) + ['#27ae60'] * len(target_nodes)
                        
                        fig = go.Figure(data=[go.Sankey(
                            node=dict(
                                pad=15,
                                thickness=20,
                                line=dict(color="black", width=0.5),
                                label=all_nodes,
                                color=node_colors
                            ),
                            link=dict(
                                source=sources,
                                target=targets,
                                value=values,
                                customdata=hover_texts,
                                hovertemplate='%{customdata}<extra></extra>'
                            )
                        )])
                        
                        fig.update_layout(
                            title_text="Bronze → Silver Column Mapping Flow",
                            font_size=10,
                            height=600
                        )
                        
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.warning("No mapped columns to display in Sankey diagram.")
                        
                except ImportError:
                    st.warning("Install plotly for Sankey diagram: `pip install plotly`")
                    st.info("Falling back to Mermaid diagram...")
                    
                    # Mermaid fallback
                    unique_links = set()
                    for _, row in df_map.iterrows():
                        if row.get('SourceColumn') != 'UNMAPPED':
                            unique_links.add((row.get('SourceTable', 'SRC'), row.get('TargetTable', 'TGT')))
                    
                    if unique_links:
                        mermaid_lines = ["graph LR", "  subgraph Bronze"]
                        for s_table in sorted(set(r[0] for r in unique_links)):
                            s_id = s_table.replace(".", "_").replace(" ", "_")
                            mermaid_lines.append(f"    B_{s_id}[\"{s_table}\"]")
                        mermaid_lines.append("  end\n  subgraph Silver")
                        for t_table in sorted(set(r[1] for r in unique_links)):
                            t_id = t_table.replace(".", "_").replace(" ", "_")
                            mermaid_lines.append(f"    S_{t_id}[\"{t_table}\"]")
                        mermaid_lines.append("  end")
                        for s_table, t_table in sorted(unique_links):
                            s_id = s_table.replace(".", "_").replace(" ", "_")
                            t_id = t_table.replace(".", "_").replace(" ", "_")
                            mermaid_lines.append(f"  B_{s_id} --> S_{t_id}")
                        st.markdown(f"```mermaid\n" + "\n".join(mermaid_lines) + "\n```")
            
            with tab_json:
                st.write("**Raw Mapping Model (JSON Format)**")
                
                # Convert flat results to nested JSON structure
                nested_model = {"mapped_columns": []}
                
                # Group by target column
                for target_col in df_map['TargetColumn'].unique():
                    target_rows = df_map[df_map['TargetColumn'] == target_col]
                    first_row = target_rows.iloc[0]
                    
                    source_mappings = []
                    for _, row in target_rows.iterrows():
                        if row.get('SourceColumn') != 'UNMAPPED':
                            source_mappings.append({
                                "source_column_name": row.get('SourceColumn', ''),
                                "source_table_name": row.get('SourceTable', ''),
                                "source_schema_name": row.get('SourceSchema', ''),
                                "justification": row.get('Justification', '')
                            })
                    
                    nested_model["mapped_columns"].append({
                        "target_column_name": target_col,
                        "target_table_name": first_row.get('TargetTable', ''),
                        "target_schema_name": first_row.get('TargetSchema', ''),
                        "source_column_name": source_mappings,
                        "final_transformation_logic": first_row.get('TransformationLogic', ''),
                        "work_notes": first_row.get('WorkNotes', '')
                    })
                
                st.json(nested_model)
                
                # Download JSON
                json_str = json.dumps(nested_model, indent=2)
                _render_export_control(
                    control_id="mapping_model_json",
                    label="📥 Download JSON Model",
                    payload=json_str,
                    filename="mapping_model.json",
                    mime="application/json"
                )
            
            with tab_justify:
                st.write("**AI Justifications & Transformation Logic with Confidence Scores**")

                # Group by target table for easier navigation
                for target_table in df_map['TargetTable'].unique():
                    table_rows = df_map[df_map['TargetTable'] == target_table]

                    # Calculate average confidence for this table
                    table_mapped = table_rows[table_rows['SourceColumn'] != 'UNMAPPED']
                    table_avg_conf = table_mapped['MappingScore'].mean() if len(table_mapped) > 0 else 0

                    with st.expander(f"🎯 **{target_table}** ({len(table_rows)} columns) - Avg Confidence: {table_avg_conf:.0f}%", expanded=True):
                        for _, row in table_rows.iterrows():
                            source_info = f"{row.get('SourceTable', 'N/A')}.{row.get('SourceColumn', 'N/A')}"
                            target_info = row.get('TargetColumn', 'N/A')
                            mapping_score = row.get('MappingScore', 0)

                            if row.get('SourceColumn') == 'UNMAPPED':
                                st.markdown(f"❌ **{target_info}** → `UNMAPPED`")
                            else:
                                # PHASE 2: Show confidence with color coding
                                if mapping_score >= 90:
                                    confidence_badge = "🟢 Excellent"
                                elif mapping_score >= 70:
                                    confidence_badge = "🟢 Good"
                                elif mapping_score >= 50:
                                    confidence_badge = "🟡 Fair"
                                else:
                                    confidence_badge = "🔴 Uncertain"

                                # STEP 2: Highlight low-confidence mappings
                                if mapping_score < 60:
                                    st.error(f"⚠️ **LOW CONFIDENCE MAPPING:** {target_info} ← {source_info} | Confidence: **{confidence_badge} ({mapping_score:.0f}%)**")
                                else:
                                    st.markdown(f"✅ **{target_info}** ← `{source_info}` | Confidence: **{confidence_badge} ({mapping_score:.0f}%)**")

                                # STEP 3: Individual Approve/Reject Buttons
                                mapping_key = f"{row.get('TargetTable')}.{row.get('TargetColumn')}->{row.get('SourceTable')}.{row.get('SourceColumn')}"

                                col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 3])

                                with col_btn1:
                                    if st.button(f"✅ Approve", key=f"approve_{mapping_key}_{_}", use_container_width=True):
                                        from ai.approval_manager import record_approval
                                        approval_id = record_approval(row, "APPROVED", notes="", reviewer="user")
                                        st.session_state.mapping_approval_status[mapping_key] = "APPROVED"
                                        st.success("Approved!")
                                        _rerun()

                                with col_btn2:
                                    if st.button(f"❌ Reject", key=f"reject_{mapping_key}_{_}", use_container_width=True):
                                        from ai.approval_manager import record_approval
                                        approval_id = record_approval(row, "REJECTED", notes="", reviewer="user")
                                        st.session_state.mapping_approval_status[mapping_key] = "REJECTED"
                                        st.warning("Rejected")
                                        _rerun()

                                with col_btn3:
                                    # Show current approval status
                                    current_status = st.session_state.mapping_approval_status.get(mapping_key, "")
                                    if current_status:
                                        if current_status == "APPROVED":
                                            st.success(f"Status: ✅ {current_status}")
                                        elif current_status == "REJECTED":
                                            st.error(f"Status: ❌ {current_status}")
                                        else:
                                            st.info(f"Status: {current_status}")

                                if row.get('Justification'):
                                    st.caption(f"💡 *{row.get('Justification')}*")

                                if row.get('TransformationLogic'):
                                    st.code(row.get('TransformationLogic'), language="sql")

                                if row.get('WorkNotes') and row.get('WorkNotes') != 'Awaiting initial scan.':
                                    st.info(f"📝 Work Notes: {row.get('WorkNotes')}")

                            st.markdown("---")
            
            with tab_export:
                st.write("**Export Options**")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    df_map_export = sanitize_dataframe(df_map)
                    csv_data = df_map_export.to_csv(index=False, lineterminator='\n', quoting=csv.QUOTE_ALL)
                    _render_export_control(
                        control_id="mapping_full_csv",
                        label="📥 Download Full CSV",
                        payload=csv_data,
                        filename="bronze_to_silver_mapping.csv",
                        mime="text/csv"
                    )
                
                with col2:
                    mapped_only = sanitize_dataframe(df_map[df_map['SourceColumn'] != 'UNMAPPED'])
                    csv_mapped = mapped_only.to_csv(index=False, lineterminator='\n', quoting=csv.QUOTE_ALL)
                    _render_export_control(
                        control_id="mapping_mapped_only_csv",
                        label="📥 Download Mapped Only CSV",
                        payload=csv_mapped,
                        filename="bronze_to_silver_mapped_only.csv",
                        mime="text/csv"
                    )
                
                st.write("---")

                # ENHANCEMENT 2: Downloadable Word Document
                st.write("**📄 Comprehensive Analysis Report**")
                st.info("Generate a detailed Word document with complete end-to-end analysis including sources, targets, mappings, and data quality insights.")

                if st.button("📄 Generate Complete Analysis Report (Word)", use_container_width=True, type="primary", key="generate_word_report"):
                    with st.spinner("Generating comprehensive Word document..."):
                        try:
                            from docx import Document
                            from docx.shared import Inches, Pt, RGBColor
                            from docx.enum.text import WD_ALIGN_PARAGRAPH
                            from io import BytesIO

                            # Create document
                            doc = Document()

                            # Title
                            title = doc.add_heading('Bronze-to-Silver Mapping Analysis Report', 0)
                            title.alignment = WD_ALIGN_PARAGRAPH.CENTER

                            # Metadata
                            doc.add_paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                            doc.add_paragraph(f"Analyzed by: Silver Model Builder AI")
                            doc.add_page_break()

                            # 1. Executive Summary
                            doc.add_heading('1. Executive Summary', level=1)
                            total_targets = len(df_map['TargetColumn'].unique())
                            mapped_count_report = len(df_map[df_map['SourceColumn'] != 'UNMAPPED'])
                            unmapped_count_report = len(df_map[df_map['SourceColumn'] == 'UNMAPPED'])
                            avg_conf_report = df_map[df_map['SourceColumn'] != 'UNMAPPED']['MappingScore'].mean()

                            doc.add_paragraph(f"Total Target Columns: {total_targets}")
                            doc.add_paragraph(f"Successfully Mapped: {mapped_count_report} ({mapped_count_report/total_targets*100:.1f}%)")
                            doc.add_paragraph(f"Unmapped: {unmapped_count_report} ({unmapped_count_report/total_targets*100:.1f}%)")
                            doc.add_paragraph(f"Average Mapping Confidence: {avg_conf_report:.1f}%")

                            # 2. Source (Bronze) Tables
                            doc.add_page_break()
                            doc.add_heading('2. Source (Bronze) Tables Analysis', level=1)

                            source_tables = df_map[df_map['SourceTable'] != 'UNMAPPED']['SourceTable'].unique()
                            doc.add_paragraph(f"Total Source Tables: {len(source_tables)}")

                            for table in source_tables:
                                doc.add_heading(f"Table: {table}", level=2)

                                # Get columns from this table in profiles
                                if table in st.session_state.profiles:
                                    table_profiles = st.session_state.profiles[table]
                                    doc.add_paragraph(f"Total Columns: {len(table_profiles)}")

                                    # Add column details
                                    for col, profile in list(table_profiles.items())[:10]:  # Limit to first 10
                                        doc.add_paragraph(f"• {col} ({profile.get('datatype', 'N/A')})", style='List Bullet')

                                        # Add quality metrics if available
                                        total = profile.get('total', 0)
                                        nulls = profile.get('nulls', 0)
                                        completeness_report = ((total - nulls) / total * 100) if total > 0 else 0
                                        quality = profile.get('quality_score', 0)

                                        doc.add_paragraph(f"  Completeness: {completeness_report:.1f}%, Quality Score: {quality}", style='List Bullet 2')

                            # 3. Target (Silver) Tables
                            doc.add_page_break()
                            doc.add_heading('3. Target (Silver) Tables Analysis', level=1)

                            target_tables = df_map['TargetTable'].unique()
                            doc.add_paragraph(f"Total Target Tables: {len(target_tables)}")

                            for table in target_tables:
                                doc.add_heading(f"Table: {table}", level=2)
                                table_mappings = df_map[df_map['TargetTable'] == table]
                                doc.add_paragraph(f"Total Columns: {len(table_mappings)}")
                                doc.add_paragraph(f"Mapped Columns: {len(table_mappings[table_mappings['SourceColumn'] != 'UNMAPPED'])}")
                                avg_score_table = table_mappings[table_mappings['SourceColumn'] != 'UNMAPPED']['MappingScore'].mean()
                                doc.add_paragraph(f"Average Mapping Confidence: {avg_score_table:.1f}%")

                            # 4. Detailed Column Mappings
                            doc.add_page_break()
                            doc.add_heading('4. Detailed Column Mappings', level=1)

                            for idx, row in df_map.head(50).iterrows():  # Limit to first 50 for document size
                                doc.add_heading(f"{row['TargetTable']}.{row['TargetColumn']}", level=3)

                                if row['SourceColumn'] != 'UNMAPPED':
                                    doc.add_paragraph(f"Source: {row['SourceTable']}.{row['SourceColumn']}")
                                    doc.add_paragraph(f"Confidence Score: {row['MappingScore']}%")
                                    doc.add_paragraph(f"Transformation: {row.get('TransformationLogic', 'N/A')}")
                                    doc.add_paragraph(f"Justification: {row.get('Justification', 'N/A')}")
                                else:
                                    doc.add_paragraph("Status: UNMAPPED - No suitable source found")

                            # 5. Data Quality Analysis (if available)
                            if 'ai_analysis' in st.session_state and st.session_state.ai_analysis:
                                doc.add_page_break()
                                doc.add_heading('5. AI Data Quality Analysis', level=1)

                                for col_spec, analysis in list(st.session_state.ai_analysis.items())[:20]:  # Limit to first 20
                                    doc.add_heading(col_spec, level=3)
                                    doc.add_paragraph(f"Semantic Type: {analysis.get('column_type_inference', 'N/A')}")
                                    doc.add_paragraph(f"Quality Score: {analysis.get('quality_score', 0)}/100")
                                    doc.add_paragraph(f"Analysis: {analysis.get('analysis_text', 'N/A')}")

                                    # Add quality issues
                                    issues = analysis.get('quality_issues', [])
                                    if issues:
                                        doc.add_paragraph("Quality Issues:")
                                        for issue in issues[:5]:
                                            doc.add_paragraph(f"• {issue.get('issue', 'N/A')} (Severity: {issue.get('severity', 'N/A')})", style='List Bullet')

                            # 6. Validation Results
                            if "mapping_validation_results" in st.session_state and st.session_state.mapping_validation_results:
                                doc.add_page_break()
                                doc.add_heading('6. Validation Results', level=1)

                                validation = st.session_state.mapping_validation_results
                                doc.add_paragraph(f"Total Mappings: {validation['total_mappings']}")
                                doc.add_paragraph(f"Valid: {validation['valid_count']}")
                                doc.add_paragraph(f"Invalid: {validation['invalid_count']}")
                                doc.add_paragraph(f"High Confidence: {validation['high_confidence_count']}")
                                doc.add_paragraph(f"Medium Confidence: {validation['medium_confidence_count']}")
                                doc.add_paragraph(f"Low Confidence: {validation['low_confidence_count']}")

                            # Save to BytesIO
                            doc_bytes = BytesIO()
                            doc.save(doc_bytes)
                            doc_bytes.seek(0)

                            _render_export_control(
                                control_id="analysis_report_docx",
                                label="📥 Download Report (DOCX)",
                                payload=doc_bytes.getvalue(),
                                filename=f"bronze_silver_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx",
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                auto_stage=True
                            )

                            st.success("✅ Report generated successfully! Click the button above to download.")

                        except ImportError:
                            st.error("❌ python-docx library not installed. Please install with: pip install python-docx")
                        except Exception as e:
                            st.error(f"❌ Error generating report: {str(e)}")
                            import traceback
                            st.code(traceback.format_exc())

                st.write("---")
                st.write("**Quick Stats:**")
                st.write(f"- Total target columns: **{len(df_map['TargetColumn'].unique())}**")
                st.write(f"- Source tables used: **{len(df_map[df_map['SourceTable'] != 'UNMAPPED']['SourceTable'].unique())}**")
                st.write(f"- Target tables: **{len(df_map['TargetTable'].unique())}**")

            # STEP 1: Validation Report Tab
            with tab_validation:
                st.write("**Post-Mapping Validation Report**")

                if "mapping_validation_results" in st.session_state and st.session_state.mapping_validation_results:
                    validation = st.session_state.mapping_validation_results

                    # Validation Summary Metrics
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Total Mappings", validation['total_mappings'])
                    with col2:
                        valid_pct = (validation['valid_count'] / validation['total_mappings'] * 100) if validation['total_mappings'] > 0 else 0
                        st.metric("Valid", validation['valid_count'], delta=f"{valid_pct:.0f}%", delta_color="normal")
                    with col3:
                        invalid_pct = (validation['invalid_count'] / validation['total_mappings'] * 100) if validation['total_mappings'] > 0 else 0
                        st.metric("Invalid", validation['invalid_count'], delta=f"{invalid_pct:.0f}%", delta_color="inverse")
                    with col4:
                        st.metric("High Confidence", validation['high_confidence_count'])

                    # Confidence Distribution
                    st.write("**Confidence Distribution:**")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("🟢 High", validation['high_confidence_count'])
                    with col2:
                        st.metric("🟡 Medium", validation['medium_confidence_count'])
                    with col3:
                        st.metric("🔴 Low", validation['low_confidence_count'])

                    # Detailed Validation Results
                    st.write("---")
                    st.write("**Detailed Validation Results:**")

                    # Filter options
                    filter_option = st.selectbox(
                        "Filter by:",
                        ["All Mappings", "Invalid Only", "Low Confidence Only", "Warnings Only"]
                    )

                    validation_details = validation['validation_details']

                    # Apply filter
                    if filter_option == "Invalid Only":
                        validation_details = [v for v in validation_details if not v['is_valid']]
                    elif filter_option == "Low Confidence Only":
                        validation_details = [v for v in validation_details if v['confidence_level'] == 'LOW']
                    elif filter_option == "Warnings Only":
                        validation_details = [v for v in validation_details if len(v['warnings']) > 0]

                    # Display validation details
                    for idx, detail in enumerate(validation_details):
                        mapping_info = detail.get('mapping', {})
                        is_valid = detail['is_valid']
                        conf_level = detail['confidence_level']
                        issues = detail.get('issues', [])
                        warnings = detail.get('warnings', [])
                        recommendations = detail.get('recommendations', [])

                        # Color code by status
                        if not is_valid:
                            st.error(f"❌ **{mapping_info.get('target', 'N/A')}** ← {mapping_info.get('source', 'N/A')} (Score: {mapping_info.get('score', 0)}%)")
                        elif conf_level == 'LOW':
                            st.warning(f"⚠️ **{mapping_info.get('target', 'N/A')}** ← {mapping_info.get('source', 'N/A')} (Score: {mapping_info.get('score', 0)}%)")
                        else:
                            st.success(f"✅ **{mapping_info.get('target', 'N/A')}** ← {mapping_info.get('source', 'N/A')} (Score: {mapping_info.get('score', 0)}%)")

                        # Show issues
                        if issues:
                            st.markdown("**Issues:**")
                            for issue in issues:
                                st.markdown(f"- 🚫 {issue}")

                        # Show warnings
                        if warnings:
                            st.markdown("**Warnings:**")
                            for warning in warnings:
                                st.markdown(f"- ⚠️ {warning}")

                        # Show recommendations
                        if recommendations:
                            st.markdown("**Recommendations:**")
                            for rec in recommendations:
                                st.markdown(f"- 💡 {rec}")

                        st.markdown("---")

                    # Summary text
                    st.write("**Summary:**")
                    st.code(validation['summary'])

                else:
                    st.info("Validation results not available. Run mapping to see validation report.")

            # NEW TAB: AI Chat Interface
            with tab_ai_chat:
                st.write("**🤖 Natural Language Mapping Assistant**")
                st.markdown("""
                Ask me anything about your mappings in plain English! I can help you:
                - Map specific columns
                - Search for patterns
                - Update transformations
                - Validate mappings
                """)

                # Chat interface
                if 'chat_history' not in st.session_state:
                    st.session_state.chat_history = []

                # Chat input
                user_query = st.text_input(
                    "Your question:",
                    placeholder="Example: Map all email columns from CRM to customer dimension",
                    key="nl_query_input"
                )

                col1, col2 = st.columns([1, 5])
                with col1:
                    if st.button("🚀 Ask AI", use_container_width=True, key="nl_ask_button"):
                        if user_query.strip():
                            with st.spinner("AI is thinking..."):
                                try:
                                    from ai.nl_mapping_interface import NLMappingInterface

                                    # Get Snowflake connection (uses env vars set from sidebar)
                                    conn = get_connection()

                                    # Initialize NL interface
                                    nl_interface = NLMappingInterface()

                                    # Build rich context with actual mapping data
                                    context = {
                                        'current_mappings': st.session_state.mapping_results,
                                        'available_tables': list(set([m['SourceTable'] for m in st.session_state.mapping_results if m.get('SourceColumn') not in ['UNMAPPED', 'N/A']]))
                                    }

                                    # Get direct LLM answer with full mapping context
                                    response = nl_interface.answer_with_context(user_query, context, conn=conn)

                                    # Close connection
                                    _safe_close(conn)

                                    # Add to chat history
                                    st.session_state.chat_history.append({
                                        'query': user_query,
                                        'response': response,
                                        'timestamp': datetime.now().isoformat()
                                    })

                                    st.markdown(response)

                                except Exception as e:
                                    st.error(f"AI Error: {str(e)}")
                                    import traceback
                                    st.error(traceback.format_exc())
                        else:
                            st.warning("Please enter a question")

                with col2:
                    if st.button("🗑️ Clear Chat", use_container_width=True, key="nl_clear_button"):
                        st.session_state.chat_history = []
                        _rerun()

                # Display chat history
                if st.session_state.chat_history:
                    st.write("---")
                    st.write("**Chat History:**")
                    for idx, chat in enumerate(reversed(st.session_state.chat_history[-10:])):
                        with st.expander(f"💬 {chat['query'][:50]}...", expanded=(idx == 0)):
                            st.markdown(f"**You:** {chat['query']}")
                            st.markdown(f"**AI:** {chat['response']}")
                            st.caption(f"🕒 {chat['timestamp']}")

            # NEW TAB: Pattern Library
            with tab_patterns:
                st.write("**📚 Cross-Project Pattern Library**")
                st.markdown("""
                Learn from successful mappings across projects and get intelligent recommendations based on patterns.
                """)

                try:
                    from ai.pattern_library import PatternLibrary

                    pattern_lib = PatternLibrary()

                    # Show statistics
                    stats = pattern_lib.get_pattern_statistics()

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Total Patterns", stats['total_patterns'])
                    col2.metric("Avg Confidence", f"{stats['avg_confidence']:.1%}" if stats['avg_confidence'] > 0 else "0%")
                    col3.metric("Total Usage", stats['total_occurrences'])
                    col4.metric("High Confidence", stats['high_confidence_patterns'])

                    st.write("---")

                    # Pattern Learning
                    st.write("**🎓 Learn from Current Mappings**")
                    if st.button("📖 Learn Patterns from Current Mappings", use_container_width=True, key="learn_patterns_button"):
                        with st.spinner("Learning patterns..."):
                            learned_count = 0
                            for mapping in st.session_state.mapping_results:
                                if mapping.get('MappingScore', 0) >= 80:
                                    pattern_id = pattern_lib.learn_pattern_from_mapping(mapping, project_id="current_project")
                                    if pattern_id:
                                        learned_count += 1

                            st.success(f"✅ Learned {learned_count} new patterns from high-confidence mappings!")
                            _rerun()

                    st.write("---")

                    # Top Patterns
                    st.write("**🏆 Top Patterns**")
                    if stats['top_tags']:
                        tag_data = pd.DataFrame(stats['top_tags'], columns=['Tag', 'Count'])
                        st.bar_chart(tag_data.set_index('Tag'))
                    else:
                        st.info("No patterns learned yet. Start by learning from your current mappings!")

                    st.write("---")

                    # Pattern Recommendations
                    st.write("**💡 Get Pattern Recommendations**")
                    target_col_input = st.text_input("Enter target column name:", key="pattern_target_col")

                    if st.button("🔍 Get Recommendations", use_container_width=True, key="get_recommendations_button"):
                        if target_col_input:
                            # Build source columns list
                            source_cols = []
                            for table, profiles in st.session_state.profiles.items():
                                for col in profiles.keys():
                                    source_cols.append({
                                        'column_name': col,
                                        'source_table_name': table
                                    })

                            recommendations = pattern_lib.get_pattern_recommendations(target_col_input, source_cols)

                            if recommendations:
                                st.write(f"**Found {len(recommendations)} recommendations:**")
                                for rec in recommendations:
                                    with st.container():
                                        st.markdown(f"**✓ {rec['source_table']}.{rec['source_column']}**")
                                        st.write(f"Confidence: {rec['confidence']:.0%}")
                                        st.write(f"Transformation: `{rec['transformation']}`")
                                        st.write(f"Reason: {rec['reason']}")
                                        st.markdown("---")
                            else:
                                st.info("No pattern recommendations found. Try learning more patterns first!")

                except Exception as e:
                    st.error(f"Pattern Library Error: {str(e)}")

            # NEW TAB: Real-Time Dashboard
            with tab_dashboard:
                st.write("**📊 Real-Time Data Quality Dashboard**")
                st.markdown("Live monitoring of your data pipeline quality metrics")

                # Quality Metrics Overview
                df_map_dashboard = pd.DataFrame(st.session_state.mapping_results)

                # Create dashboard layout
                st.write("### 📈 Quality Trends")

                # Score distribution
                score_bins = [0, 50, 60, 70, 80, 90, 100]
                score_labels = ['0-50%', '50-60%', '60-70%', '70-80%', '80-90%', '90-100%']
                df_map_dashboard['ScoreBin'] = pd.cut(df_map_dashboard['MappingScore'], bins=score_bins, labels=score_labels)

                score_dist = df_map_dashboard['ScoreBin'].value_counts().sort_index()

                fig_dist = go.Figure(data=[
                    go.Bar(
                        x=score_dist.index.astype(str),
                        y=score_dist.values,
                        marker_color=['#e74c3c', '#e67e22', '#f39c12', '#f1c40f', '#2ecc71', '#27ae60'],
                        text=score_dist.values,
                        textposition='auto'
                    )
                ])
                fig_dist.update_layout(
                    title="Mapping Confidence Distribution",
                    xaxis_title="Confidence Range",
                    yaxis_title="Number of Mappings",
                    height=400
                )
                st.plotly_chart(fig_dist, use_container_width=True)

                # Quality Score by Table
                st.write("### 📊 Quality by Target Table")
                table_quality = df_map_dashboard.groupby('TargetTable').agg({
                    'MappingScore': 'mean',
                    'TargetColumn': 'count'
                }).rename(columns={'MappingScore': 'Avg Score', 'TargetColumn': 'Column Count'})

                fig_table = go.Figure(data=[
                    go.Bar(
                        x=table_quality.index,
                        y=table_quality['Avg Score'],
                        marker_color=table_quality['Avg Score'].apply(lambda x: '#2ecc71' if x >= 80 else ('#f39c12' if x >= 60 else '#e74c3c')),
                        text=table_quality['Avg Score'].round(1),
                        textposition='auto',
                        hovertemplate='<b>%{x}</b><br>Avg Score: %{y:.1f}%<br>Columns: %{customdata}<extra></extra>',
                        customdata=table_quality['Column Count']
                    )
                ])
                fig_table.update_layout(
                    title="Average Mapping Quality by Table",
                    xaxis_title="Target Table",
                    yaxis_title="Average Confidence Score",
                    height=400
                )
                st.plotly_chart(fig_table, use_container_width=True)

                # Live Alerts
                st.write("### 🚨 Live Quality Alerts")
                alerts = []

                # Check for low confidence mappings
                low_conf = len(df_map_dashboard[df_map_dashboard['MappingScore'] < 60])
                if low_conf > 0:
                    alerts.append({"severity": "high", "message": f"{low_conf} mappings have low confidence (<60%)", "action": "Review and improve these mappings"})

                # Check for unmapped columns
                unmapped = len(df_map_dashboard[df_map_dashboard['SourceColumn'] == 'UNMAPPED'])
                if unmapped > 0:
                    alerts.append({"severity": "medium", "message": f"{unmapped} columns are unmapped", "action": "Find source columns for these targets"})

                # Check for validation issues
                if 'mapping_validation_results' in st.session_state:
                    validation = st.session_state.mapping_validation_results
                    if validation['invalid_count'] > 0:
                        alerts.append({"severity": "high", "message": f"{validation['invalid_count']} mappings failed validation", "action": "Fix type compatibility and data quality issues"})

                if alerts:
                    for alert in alerts:
                        if alert['severity'] == 'high':
                            st.error(f"🚨 **HIGH:** {alert['message']}\n\n💡 Action: {alert['action']}")
                        elif alert['severity'] == 'medium':
                            st.warning(f"⚠️ **MEDIUM:** {alert['message']}\n\n💡 Action: {alert['action']}")
                        else:
                            st.info(f"ℹ️ **INFO:** {alert['message']}\n\n💡 Action: {alert['action']}")
                else:
                    st.success("✅ **All systems operational** - No quality alerts at this time!")

                # Refresh button
                if st.button("🔄 Refresh Dashboard", use_container_width=True, key="refresh_dashboard"):
                    _rerun()

            # STEP 6: Iterative Refinement Section
            st.write("---")
            st.subheader("🔄 Iterative Refinement")

            # Initialize iteration counter
            if 'refinement_iteration' not in st.session_state:
                st.session_state.refinement_iteration = 0

            if st.session_state.refinement_iteration > 0:
                st.info(f"📊 Current Iteration: **{st.session_state.refinement_iteration}**")

            with st.expander("🔄 Refine Mappings with Additional Feedback", expanded=False):
                st.markdown("""
**Iterative Refinement** allows you to provide additional context to improve low-confidence mappings.

**How it works:**
1. Review current mappings and identify issues
2. Provide specific feedback about preferences
3. AI re-runs mapping with your feedback incorporated
4. Compare results and approve refined mappings
                """)

                # Get current statistics
                low_confidence = df_map[df_map['MappingScore'] < 60]

                # Get approved/rejected mappings from session state
                approved_keys = [k for k, v in st.session_state.mapping_approval_status.items() if v == 'APPROVED']
                rejected_keys = [k for k, v in st.session_state.mapping_approval_status.items() if v == 'REJECTED']

                col1, col2, col3 = st.columns(3)
                col1.metric("Low Confidence", len(low_confidence))
                col2.metric("Approved", len(approved_keys))
                col3.metric("Rejected", len(rejected_keys))

                # Refinement feedback
                refinement_feedback = st.text_area(
                    "What should the AI focus on?",
                    placeholder="""Example:
- For CUSTOMER_ID, prefer sources from MASTER_CUSTOMER table
- EMAIL columns should map to EMAIL_ADDRESS, not EMAIL_ADDR
- Avoid staging tables (STG_*) as sources""",
                    height=150,
                    key="refinement_feedback"
                )

                # Options
                col1, col2 = st.columns(2)
                with col1:
                    focus_on_low = st.checkbox("Focus on low-confidence mappings", value=True, key="refine_focus_low")
                with col2:
                    use_approvals = st.checkbox("Learn from approved/rejected", value=True, key="refine_use_approvals")

                # Run refinement
                if st.button("🔄 Run Refinement Iteration", use_container_width=True, type="primary", key="run_refinement"):
                    if not refinement_feedback.strip() and not use_approvals:
                        st.error("Please provide feedback or enable learning from approvals")
                    else:
                        with st.spinner("🔄 Running refinement..."):
                            from ai.refinement_engine import create_refinement_prompt, calculate_improvement_stats, record_refinement_iteration

                            # Backup original if first iteration
                            if 'mapping_results_original' not in st.session_state or st.session_state.mapping_results_original is None:
                                st.session_state.mapping_results_original = st.session_state.mapping_results.copy()

                            # Get approved/rejected examples
                            approved_mappings = []
                            rejected_mappings = []
                            if use_approvals:
                                for mapping in st.session_state.mapping_results:
                                    mapping_key = f"{mapping['TargetTable']}.{mapping['TargetColumn']}->{mapping['SourceTable']}.{mapping['SourceColumn']}"
                                    status_val = st.session_state.mapping_approval_status.get(mapping_key, '')
                                    if status_val == 'APPROVED':
                                        approved_mappings.append(mapping)
                                    elif status_val == 'REJECTED':
                                        rejected_mappings.append(mapping)

                            # Create enhanced context
                            enhanced_context = create_refinement_prompt(
                                st.session_state.mapping_results,
                                refinement_feedback,
                                low_confidence.to_dict('records') if focus_on_low else [],
                                approved_mappings,
                                rejected_mappings
                            )

                            # Re-run mapping with enhanced context
                            from ai.iterative_mapper import run_iterative_mapping

                            # Get mapping context from session state
                            if 'mapping_context' not in st.session_state:
                                st.error("❌ Mapping context not available. Please run initial mapping first.")
                                st.stop()

                            mapping_context = st.session_state.mapping_context

                            # Get connection params from sidebar session state
                            conn_account = st.session_state.get('account_input', '')
                            conn_user = st.session_state.get('user_input', '')
                            conn_role = st.session_state.get('role_input', '')
                            conn_warehouse = st.session_state.get('warehouse_input', '')
                            conn_database = st.session_state.get('database_input', '')
                            conn_schema = st.session_state.get('schema_input', '')

                            print(f"[refinement] Enhanced context length: {len(enhanced_context)} chars")
                            print(f"[refinement] Enhanced context preview: {enhanced_context[:200]}")

                            refined_mappings = run_iterative_mapping(
                                source_profiles=st.session_state.profiles,
                                target_profiles=mapping_context['silver_profiles'],
                                source_schema_name=mapping_context['source_schema'],
                                target_schema_name=mapping_context['selected_silver_schema'],
                                business_context=enhanced_context,
                                progress_callback=None,
                                source_descriptions=mapping_context['bronze_descriptions'],
                                target_descriptions=mapping_context['silver_descriptions'],
                                similarity_hints=mapping_context['similarity_hints'],
                                account=conn_account, user=conn_user, role=conn_role, warehouse=conn_warehouse,
                                database=conn_database, schema=conn_schema
                            )

                            # Apply business rules to refined mappings
                            from ai.business_rules_engine import apply_rules
                            refined_mappings = apply_rules(refined_mappings, st.session_state.profiles, st.session_state.get('business_rules_config'))

                            # Calculate improvement
                            improvement_stats = calculate_improvement_stats(
                                st.session_state.mapping_results,
                                refined_mappings
                            )

                            # Record iteration
                            st.session_state.refinement_iteration += 1
                            record_refinement_iteration(
                                st.session_state.refinement_iteration,
                                refinement_feedback,
                                improvement_stats
                            )

                            # Update session state
                            st.session_state.mapping_results = refined_mappings

                        # Show improvement stats (outside spinner)
                        st.success(f"""
**✅ Refinement Iteration {st.session_state.refinement_iteration} Complete!**
- Improved: {improvement_stats['improved_count']} / {improvement_stats['total_mappings']}
- Avg Score Before: {improvement_stats['avg_score_original']:.1f}%
- Avg Score After: {improvement_stats['avg_score_refined']:.1f}%
- Avg Improvement: +{improvement_stats['avg_improvement']:.1f}%
                        """)
                        _rerun()

                # Compare with original
                if 'mapping_results_original' in st.session_state and st.session_state.mapping_results_original is not None and st.session_state.refinement_iteration > 0:
                    st.write("---")
                    if st.button("📊 Show Before/After Comparison", key="show_comparison"):
                        df_original = pd.DataFrame(st.session_state.mapping_results_original)
                        df_refined = pd.DataFrame(st.session_state.mapping_results)

                        # Create comparison table
                        comparison_data = []
                        for _, refined_row in df_refined.iterrows():
                            original_match = df_original[
                                (df_original['TargetColumn'] == refined_row['TargetColumn']) &
                                (df_original['TargetTable'] == refined_row['TargetTable'])
                            ]

                            if not original_match.empty:
                                original_score = original_match.iloc[0]['MappingScore']
                                refined_score = refined_row['MappingScore']

                                comparison_data.append({
                                    'Target': f"{refined_row['TargetTable']}.{refined_row['TargetColumn']}",
                                    'Original Score': original_score,
                                    'Refined Score': refined_score,
                                    'Improvement': refined_score - original_score
                                })

                        df_comparison = pd.DataFrame(comparison_data)
                        changed = df_comparison[df_comparison['Improvement'] != 0]
                        st.write(f"**{len(changed)} mappings changed:**")
                        st.dataframe(changed, use_container_width=True)

                    # Revert option
                    if st.button("↩️ Revert to Original", key="revert_original"):
                        st.session_state.mapping_results = st.session_state.mapping_results_original
                        st.session_state.mapping_results_original = None
                        st.session_state.refinement_iteration = 0
                        st.info("Reverted to original mappings")
                        _rerun()


else:
    # --- ORIGINAL GENERATION MODE PIPELINE ---
    st.subheader("🪄 4. AI Model Generation")
    
    if not st.session_state.profiles:
        st.info("Please run profiling above to unlock AI modeling.")
    else:
        if st.button("🪄 Start AI Model", use_container_width=True):
            monitor_placeholder = st.empty()
            progress = st.progress(0)
            
            pipeline_steps = [
                "1. AI Description Generation",
                "2. Semantic Clustering",
                "3. Model Logic Design",
                "4. Snowflake DDL Generation"
            ]


            def update_monitor(active_idx):
                with _safe_container(monitor_placeholder, border=True):
                    st.write("### 🛤️ AI Pipeline Process Monitor")
                    cols = st.columns(len(pipeline_steps))
                    for i, name in enumerate(pipeline_steps):
                        with cols[i]:
                            if i < active_idx:
                                st.markdown(f"✅ **{name}**\n\nDone")
                            elif i == active_idx:
                                st.markdown(f"⏳ **{name}**\n\nRunning...")
                            else:
                                st.markdown(f"⚪ **{name}**\n\nPending")
                    if active_idx < len(pipeline_steps):
                        st.write(f"Current Activity: **{pipeline_steps[active_idx]}**")
                    else:
                        st.write("Current Activity: **🏁 Pipeline Complete**")

            try:
                all_profiles = st.session_state.profiles
                
                # Convert to "Metadata" format required by description generator
                metadata_struct = {}
                for t, p in all_profiles.items():
                    metadata_struct[t] = {"columns": [{"name": k, "datatype": v.get("datatype")} for k, v in p.items()]}
                
                # 1. DESCRIPTIONS
                update_monitor(0)
                progress.progress(0.25)
                desc_data = generate_descriptions_cached(metadata_struct, all_profiles)
                st.session_state.desc_data = desc_data
                
                # 2. CLUSTERING
                update_monitor(1)
                progress.progress(0.5)
                texts = build_texts(desc_data)
                labels = [t.split(':', 1)[0] for t in texts]
                
                try:
                    embeds = get_embeddings_cached(texts)
                except Exception as e:
                    st.warning(f"Snowflake Embed failed ({e}), falling back to TF-IDF")
                    embeds = fallback_embeddings(texts)
                    
                clusters = cluster(embeds, labels, k=max(2, int(len(texts)**0.5)))
                cluster_payload = {'embed_model': 'ui-run', 'cluster_count': len(clusters), 'clusters': clusters}
                
                # 3. SILVER MODEL
                update_monitor(2)
                progress.progress(0.75)
                model_json_str = generate_silver_model_cached(cluster_payload)
                model_data = parse_robust(model_json_str)
                if isinstance(model_data, str):
                    model_data = parse_robust(model_data)
                st.session_state.model_data = model_data

                if not isinstance(model_data, dict) or "entities" not in model_data:
                    st.error("Failed to generate valid model JSON from LLM.")
                    st.stop()
                    
                # 4. DDL GENERATION
                update_monitor(3)
                progress.progress(0.9)
                
                ddl_statements = []
                csv_rows = []
                target_schema = "SILVER"
                
                for entity in model_data.get("entities", []):
                    t_name = sanitize(entity.get("entity_name"))
                    attrs_sql = []
                    pk_cols = []
                    
                    for attr in entity.get("attributes", []):
                        col = sanitize(attr.get("name"))
                        dtype = attr.get("datatype")
                        desc = attr.get("description", "")
                        desc_safe = desc.replace("'", "''")
                        attrs_sql.append(f"    {col} {dtype} COMMENT '{desc_safe}'")
                        
                        if attr.get("is_pk") or col in DEFAULT_PK_CANDIDATES:
                            pk_cols.append(col)
                        
                        src_cols = attr.get("source_columns", [])
                        if not src_cols: src_cols = ["UNKNOWN.UNKNOWN.UNKNOWN"]
                            
                        for src in src_cols:
                            parts = src.split('.')
                            if len(parts) == 3: s_schema, s_table, s_col = parts
                            elif len(parts) == 2: s_schema, s_table, s_col = schema, parts[0], parts[1]
                            else: s_schema, s_table, s_col = schema, "UNKNOWN", parts[0]
                            
                            s_desc = ""
                            if s_table in desc_data and s_col in desc_data[s_table]:
                                s_desc = desc_data[s_table][s_col]
                            
                            s_type = ""
                            if s_table in all_profiles and s_col in all_profiles[s_table]:
                                s_type = all_profiles[s_table][s_col].get("datatype", "")

                            csv_rows.append({
                                "SourceSchema": s_schema, "SourceTableName": s_table, "SourceColumn": s_col,
                                "SourceDescription": s_desc, "SourceDataType": s_type,
                                "TargetSchema": target_schema, "TargetTableName": t_name, "TargetColumn": col,
                                "TargetDataType": dtype, "TargetDescription": desc,
                                "TransformationRule": "Direct Map" if s_col == col else "Renamed/Transformed",
                                "MappingRationale": attr.get("rationale", "Standard business mapping")
                            })
                    
                    pk_clause = f",\n    PRIMARY KEY ({', '.join(pk_cols)})" if pk_cols else ""
                    full_name = f"{database}.{target_schema}.{t_name}"
                    ddl = f"CREATE TRANSIENT TABLE IF NOT EXISTS {full_name} (\n" + ",\n".join(attrs_sql) + pk_clause + "\n);"
                    ddl_statements.append(ddl)
                
                progress.progress(1.0)
                update_monitor(4)
                st.success("✅ AI Modeling Complete!")
                st.session_state.final_sql = "\n\n".join(ddl_statements)
                st.session_state.csv_rows = csv_rows

            except Exception as e:
                st.error(f"Pipeline Failed: {e}")
                st.exception(e)



# --- RESULTS ---
if "final_sql" in st.session_state:
    st.write("---")
    st.subheader("5️⃣ Generated Artifacts")
    
    tab1, tab2, tab_logic, tab3, tab4 = st.tabs(["📄 DDL Script", "🧠 Logical Model (JSON)", "🔍 AI Logic & Clustering", "📥 Model Export", "🔗 Lineage"])
    
    with tab1:
        st.code(st.session_state.final_sql, language="sql")
        _render_export_control(
            control_id="silver_model_ddl",
            label="Download DDL",
            payload=st.session_state.final_sql,
            filename="silver_model.sql",
            mime="text/plain"
        )
        
    with tab2:
        st.json(st.session_state.model_data)
        
    with tab_logic:
        st.info("The AI identifies 'Clusters' based on semantic similarity of column descriptions and profiles before generating the final Silver model.")
        
        # Load semantic clusters if file exists
        clusters_file = base_dir / "data" / "real_run" / "profile" / "semantic_clusters.json"
        if clusters_file.exists():
            try:
                clusters_data = json.loads(clusters_file.read_text(encoding='utf-8'))
                st.write(f"**Embed Model Used:** `{clusters_data.get('embed_model', 'N/A')}`")
                st.write(f"**Found {clusters_data.get('cluster_count', 0)} Semantic Groups**")
                
                for idx, columns in clusters_data.get('clusters', {}).items():
                    with st.expander(f"Group {idx}: {len(columns)} Related Columns"):
                        for c in columns:
                            st.write(f"- `{c}`")
            except Exception as e:
                st.error(f"Error loading clusters: {e}")
        else:
            st.warning("Semantic clusters file not found. Ensure the pipeline completed successfully.")

    with tab3:
        if st.session_state.csv_rows:
            df_export = sanitize_dataframe(pd.DataFrame(st.session_state.csv_rows))
            st.dataframe(df_export, use_container_width=True)
            csv = df_export.to_csv(index=False, lineterminator='\n', quoting=csv.QUOTE_ALL)
            _render_export_control(
                control_id="silver_model_mapping_csv",
                label="Download Mapping CSV",
                payload=csv,
                filename="silver_mapping.csv",
                mime="text/csv"
            )

    with tab4:
        unique_links = set((r['SourceTableName'], r['TargetTableName']) for r in st.session_state.csv_rows)
        if unique_links:
            mermaid_lines = ["graph LR", "  subgraph Bronze"]
            for s_table in sorted(set(r[0] for r in unique_links)):
                s_id = s_table.replace(".", "_").replace(" ", "_")
                mermaid_lines.append(f"    B_{s_id}[\"{s_table}\"]")
            mermaid_lines.append("  end\n  subgraph Silver")
            for t_table in sorted(set(r[1] for r in unique_links)):
                t_id = t_table.replace(".", "_").replace(" ", "_")
                mermaid_lines.append(f"    S_{t_id}[\"{t_table}\"]")
            mermaid_lines.append("  end")
            for s_table, t_table in sorted(unique_links):
                s_id = s_table.replace(".", "_").replace(" ", "_")
                t_id = t_table.replace(".", "_").replace(" ", "_")
                mermaid_lines.append(f"  B_{s_id} --> S_{t_id}")
            st.markdown(f"```mermaid\n" + "\n".join(mermaid_lines) + "\n```")