"""Learning Engine for Mapping Improvement

Learns from user corrections to improve future mapping recommendations.

Features:
- Record user corrections
- Extract learned patterns from correction history
- Apply learned patterns to boost/penalize mapping scores
- Pattern-based learning (can evolve to ML later)
"""

import json
import re
import uuid
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import Counter


def _resolve_correction_path() -> Path:
    """Resolve the correction file path, preferring project data dir, falling back to /tmp."""
    project_path = Path("data/mapping_feedback/correction_history.json")
    if project_path.exists():
        return project_path
    try:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        return project_path
    except OSError:
        pass
    return Path(tempfile.gettempdir()) / "mapping_feedback" / "correction_history.json"


# Default path for correction history
DEFAULT_CORRECTION_FILE = _resolve_correction_path()


def _ensure_correction_file_exists(correction_file: Path = None) -> Path:
    """
    Ensure the correction history file exists.

    Args:
        correction_file: Path to correction file (uses default if None)

    Returns:
        Path object to the correction file
    """
    file_path = correction_file or DEFAULT_CORRECTION_FILE

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Create file with empty structure if it doesn't exist
        if not file_path.exists():
            initial_data = {
                "corrections": [],
                "metadata": {
                    "created_at": datetime.now().isoformat(),
                    "total_corrections": 0,
                    "patterns_learned": 0
                }
            }
            file_path.write_text(json.dumps(initial_data, indent=2), encoding='utf-8')
    except OSError as e:
        print(f"[learning_engine] Cannot write correction file ({e}), using in-memory defaults")

    return file_path


def load_correction_history(correction_file: Path = None) -> Dict:
    """
    Load correction history from JSON file.

    Args:
        correction_file: Path to correction file (uses default if None)

    Returns:
        Dict with correction history
    """
    file_path = _ensure_correction_file_exists(correction_file)

    try:
        data = json.loads(file_path.read_text(encoding='utf-8'))
        return data
    except Exception as e:
        print(f"[learning_engine] Error loading correction history: {e}")
        return {
            "corrections": [],
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "total_corrections": 0,
                "patterns_learned": 0
            }
        }


def save_correction_history(data: Dict, correction_file: Path = None):
    """
    Save correction history to JSON file.

    Args:
        data: Correction history data
        correction_file: Path to correction file (uses default if None)
    """
    file_path = _ensure_correction_file_exists(correction_file)

    try:
        file_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"[learning_engine] Error saving correction history: {e}")


def record_correction(original_mapping: Dict[str, Any],
                     new_source_column: str,
                     new_source_table: str,
                     new_transformation: str,
                     user_feedback: str = "",
                     correction_file: Path = None) -> str:
    """
    Record user correction to correction_history.json.

    Args:
        original_mapping: Original AI-generated mapping
        new_source_column: Corrected source column name
        new_source_table: Corrected source table name
        new_transformation: Corrected transformation logic
        user_feedback: Optional user feedback explaining the correction
        correction_file: Path to correction file (uses default if None)

    Returns:
        UUID of the correction record
    """
    # Load existing history
    history = load_correction_history(correction_file)

    # Create new correction record
    correction_id = str(uuid.uuid4())
    correction_record = {
        "correction_id": correction_id,
        "timestamp": datetime.now().isoformat(),
        "target_column": original_mapping.get('TargetColumn', 'UNKNOWN'),
        "target_table": original_mapping.get('TargetTable', 'UNKNOWN'),
        "original_mapping": {
            "source_column": original_mapping.get('SourceColumn', 'UNKNOWN'),
            "source_table": original_mapping.get('SourceTable', 'UNKNOWN'),
            "mapping_score": original_mapping.get('MappingScore', 0),
            "transformation_logic": original_mapping.get('TransformationLogic', '')
        },
        "corrected_mapping": {
            "source_column": new_source_column,
            "source_table": new_source_table,
            "mapping_score": 95,  # User corrections are high confidence
            "transformation_logic": new_transformation
        },
        "user_feedback": user_feedback
    }

    # Add to history
    history["corrections"].append(correction_record)

    # Update metadata
    history["metadata"]["total_corrections"] = len(history["corrections"])

    # Save updated history
    save_correction_history(history, correction_file)

    print(f"[learning_engine] Recorded correction for {original_mapping.get('TargetTable')}.{original_mapping.get('TargetColumn')}")

    return correction_id


def extract_pattern_from_column_name(column_name: str) -> str:
    """
    Extract regex pattern from column name.

    Examples:
        "CUSTOMER_ID" -> ".*_ID"
        "EMAIL_ADDRESS" -> ".*_ADDRESS"
        "FIRST_NAME" -> ".*_NAME"
    """
    if not column_name:
        return ".*"

    # Extract suffix pattern (last word after underscore)
    parts = column_name.upper().split('_')
    if len(parts) >= 2:
        suffix = parts[-1]
        return f".*_{suffix}"

    return column_name.upper()


def get_learned_patterns(correction_file: Path = None, min_occurrences: int = 2) -> List[Dict[str, Any]]:
    """
    Extract learned patterns from correction history.

    Analyzes user corrections to identify patterns:
    - Preferred source patterns for target patterns
    - Table preferences (e.g., prefer MASTER tables over STG tables)
    - Transformation preferences

    Args:
        correction_file: Path to correction file (uses default if None)
        min_occurrences: Minimum number of corrections to form a pattern

    Returns:
        List of learned patterns with confidence boosts
    """
    history = load_correction_history(correction_file)
    corrections = history.get("corrections", [])

    if len(corrections) < min_occurrences:
        return []

    patterns = []

    # Pattern 1: Target pattern -> Preferred source pattern
    target_to_source_map = {}

    for correction in corrections:
        target_col = correction.get('target_column', '')
        target_table = correction.get('target_table', '')
        original_source = correction.get('original_mapping', {}).get('source_column', '')
        corrected_source = correction.get('corrected_mapping', {}).get('source_column', '')
        corrected_table = correction.get('corrected_mapping', {}).get('source_table', '')

        # Extract patterns
        target_pattern = extract_pattern_from_column_name(target_col)
        original_pattern = extract_pattern_from_column_name(original_source)
        corrected_pattern = extract_pattern_from_column_name(corrected_source)

        # Record correction pattern
        key = target_pattern
        if key not in target_to_source_map:
            target_to_source_map[key] = {
                'preferred_sources': [],
                'rejected_sources': [],
                'preferred_tables': []
            }

        target_to_source_map[key]['preferred_sources'].append(corrected_pattern)
        target_to_source_map[key]['rejected_sources'].append(original_pattern)
        target_to_source_map[key]['preferred_tables'].append(corrected_table)

    # Analyze patterns and create rules
    for target_pattern, data in target_to_source_map.items():
        preferred_sources = data['preferred_sources']
        rejected_sources = data['rejected_sources']
        preferred_tables = data['preferred_tables']

        # Find most common preferred source pattern
        if preferred_sources:
            source_counter = Counter(preferred_sources)
            most_common_source, source_count = source_counter.most_common(1)[0]

            if source_count >= min_occurrences:
                patterns.append({
                    "pattern_id": f"pattern_{len(patterns) + 1}",
                    "pattern_type": "column_name_preference",
                    "target_pattern": target_pattern,
                    "preferred_source_pattern": most_common_source,
                    "confidence_boost": min(20, source_count * 5),  # Up to 20 point boost
                    "occurrences": source_count,
                    "reason": f"Users corrected {source_count} times: targets matching '{target_pattern}' prefer sources matching '{most_common_source}'"
                })

        # Find most common preferred table pattern
        if preferred_tables:
            table_counter = Counter(preferred_tables)
            most_common_table, table_count = table_counter.most_common(1)[0]

            if table_count >= min_occurrences:
                # Extract table pattern (e.g., "MASTER_*", "DIM_*")
                table_pattern_match = re.match(r'^([A-Z]+)_.*', most_common_table.upper())
                if table_pattern_match:
                    table_prefix = table_pattern_match.group(1)
                    patterns.append({
                        "pattern_id": f"pattern_{len(patterns) + 1}",
                        "pattern_type": "table_preference",
                        "target_pattern": target_pattern,
                        "preferred_table_pattern": f"{table_prefix}_.*",
                        "confidence_boost": min(15, table_count * 5),  # Up to 15 point boost
                        "occurrences": table_count,
                        "reason": f"Users prefer table pattern '{table_prefix}_*' for targets matching '{target_pattern}'"
                    })

    # Update metadata
    history["metadata"]["patterns_learned"] = len(patterns)
    save_correction_history(history, correction_file)

    print(f"[learning_engine] Extracted {len(patterns)} learned patterns")
    return patterns


def apply_learned_patterns(mappings: List[Dict[str, Any]],
                          learned_patterns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Apply learned patterns to boost/penalize mapping scores.

    Args:
        mappings: List of mapping dictionaries
        learned_patterns: List of learned patterns from get_learned_patterns()

    Returns:
        List of mappings with adjusted scores
    """
    if not learned_patterns:
        return mappings

    adjusted_mappings = []

    for mapping in mappings:
        target_col = mapping.get('TargetColumn', '')
        source_col = mapping.get('SourceColumn', '')
        source_table = mapping.get('SourceTable', '')
        original_score = mapping.get('MappingScore', 0)

        # Skip UNMAPPED columns
        if source_col == 'UNMAPPED' or source_col == 'N/A':
            adjusted_mappings.append(mapping)
            continue

        # Extract patterns
        target_pattern = extract_pattern_from_column_name(target_col)
        source_pattern = extract_pattern_from_column_name(source_col)

        # Apply each learned pattern
        score_adjustment = 0
        applied_patterns = []

        for pattern in learned_patterns:
            pattern_type = pattern.get('pattern_type', '')

            if pattern_type == 'column_name_preference':
                # Check if target matches pattern
                if re.match(pattern['target_pattern'].replace('.*', '.*?'), target_col.upper()):
                    # Check if source matches preferred pattern
                    if re.match(pattern['preferred_source_pattern'].replace('.*', '.*?'), source_col.upper()):
                        score_adjustment += pattern['confidence_boost']
                        applied_patterns.append(pattern['pattern_id'])

            elif pattern_type == 'table_preference':
                # Check if target matches pattern
                if re.match(pattern['target_pattern'].replace('.*', '.*?'), target_col.upper()):
                    # Check if source table matches preferred pattern
                    if re.match(pattern['preferred_table_pattern'].replace('.*', '.*?'), source_table.upper()):
                        score_adjustment += pattern['confidence_boost']
                        applied_patterns.append(pattern['pattern_id'])

        # Apply adjustment (cap at 100)
        new_score = min(100, original_score + score_adjustment)

        # Create adjusted mapping
        adjusted_mapping = mapping.copy()
        adjusted_mapping['MappingScore'] = new_score

        if score_adjustment > 0:
            # Add note about learning adjustment
            existing_justification = adjusted_mapping.get('Justification', '')
            adjusted_mapping['Justification'] = f"{existing_justification} [Learning: +{score_adjustment} points from user corrections]"

        adjusted_mappings.append(adjusted_mapping)

    print(f"[learning_engine] Applied learned patterns to {len(mappings)} mappings")
    return adjusted_mappings


def get_correction_statistics(correction_file: Path = None) -> Dict[str, Any]:
    """
    Get correction statistics for dashboard display.

    Args:
        correction_file: Path to correction file (uses default if None)

    Returns:
        Dict with correction statistics
    """
    history = load_correction_history(correction_file)
    corrections = history.get("corrections", [])

    if not corrections:
        return {
            "total_corrections": 0,
            "patterns_learned": 0,
            "most_corrected_targets": [],
            "most_preferred_tables": []
        }

    # Count most corrected target columns
    target_counter = Counter()
    table_counter = Counter()

    for correction in corrections:
        target_key = f"{correction.get('target_table', '')}.{correction.get('target_column', '')}"
        target_counter[target_key] += 1

        corrected_table = correction.get('corrected_mapping', {}).get('source_table', '')
        if corrected_table:
            table_counter[corrected_table] += 1

    return {
        "total_corrections": len(corrections),
        "patterns_learned": history.get("metadata", {}).get("patterns_learned", 0),
        "most_corrected_targets": target_counter.most_common(5),
        "most_preferred_tables": table_counter.most_common(5)
    }


if __name__ == "__main__":
    # Test learning engine
    print("Learning Engine module loaded successfully")

    # Test recording a correction
    test_original_mapping = {
        'TargetColumn': 'CUSTOMER_ID',
        'TargetTable': 'DIM_CUSTOMER',
        'SourceColumn': 'CUST_NUM',
        'SourceTable': 'STG_CUSTOMERS',
        'MappingScore': 75,
        'TransformationLogic': 'CUST_NUM'
    }

    correction_id = record_correction(
        test_original_mapping,
        new_source_column="CUSTOMER_KEY",
        new_source_table="MASTER_CUSTOMER",
        new_transformation="CUSTOMER_KEY",
        user_feedback="CUSTOMER_KEY is the authoritative source from MASTER table"
    )

    print(f"Created correction record: {correction_id}")

    # Test pattern extraction
    patterns = get_learned_patterns(min_occurrences=1)
    print(f"Learned patterns: {len(patterns)}")
    for pattern in patterns:
        print(f"  - {pattern['reason']}")

    # Test statistics
    stats = get_correction_statistics()
    print(f"Correction statistics: {stats}")

    print("\nLearning Engine ready!")
