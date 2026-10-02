"""Business Rules Engine for Mapping Filtering and Boosting

Applies configurable business rules to filter/boost/penalize mappings.

Rule Types:
- BOOST: Increase mapping score
- FILTER: Remove mapping from results
- PENALTY: Decrease mapping score

Features:
- Regex pattern matching for tables and columns
- Quality score thresholds
- Priority-based rule application
- User-configurable rules via JSON
"""

import json
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional


def _resolve_rules_path() -> Path:
    """Resolve the rules file path, preferring project data dir, falling back to /tmp."""
    # Try project-relative path first (works in local dev)
    project_path = Path("data/business_rules/rules_config.json")
    if project_path.exists():
        return project_path
    # Try to create in project dir (local dev)
    try:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        return project_path
    except OSError:
        pass
    # Fallback to temp dir (SiS: read-only filesystem)
    return Path(tempfile.gettempdir()) / "business_rules" / "rules_config.json"


# Default path for rules configuration
DEFAULT_RULES_FILE = _resolve_rules_path()


def _create_default_rules() -> Dict:
    """Create default business rules configuration."""
    return {
        "rules": [
            {
                "rule_id": "rule_001",
                "rule_name": "Prefer Master Tables",
                "rule_type": "BOOST",
                "enabled": True,
                "priority": 100,
                "conditions": {
                    "source_table_pattern": "MASTER_.*|.*_MASTER",
                    "target_column_pattern": ".*"
                },
                "action": {
                    "boost_score": 15,
                    "reason": "Master tables contain authoritative data"
                }
            },
            {
                "rule_id": "rule_002",
                "rule_name": "Block Staging Tables",
                "rule_type": "FILTER",
                "enabled": True,
                "priority": 200,
                "conditions": {
                    "source_table_pattern": "STG_.*|STAGING_.*|.*_STG"
                },
                "action": {
                    "filter_out": True,
                    "reason": "Staging tables are transient and unreliable"
                }
            },
            {
                "rule_id": "rule_003",
                "rule_name": "High Quality Required for Keys",
                "rule_type": "FILTER",
                "enabled": True,
                "priority": 75,
                "conditions": {
                    "target_column_pattern": ".*_ID|.*_KEY|.*_PK",
                    "min_quality_score": 70
                },
                "action": {
                    "filter_out": True,
                    "reason": "Key columns need high quality sources (≥70%)"
                }
            },
            {
                "rule_id": "rule_004",
                "rule_name": "Penalize Test/Temp Tables",
                "rule_type": "PENALTY",
                "enabled": True,
                "priority": 50,
                "conditions": {
                    "source_table_pattern": "TEST_.*|TEMP_.*|TMP_.*"
                },
                "action": {
                    "penalty_score": 30,
                    "reason": "Test/temp tables are not production-ready"
                }
            },
            {
                "rule_id": "rule_005",
                "rule_name": "Boost Dimension Table Sources",
                "rule_type": "BOOST",
                "enabled": True,
                "priority": 80,
                "conditions": {
                    "source_table_pattern": "DIM_.*",
                    "target_table_pattern": ".*"
                },
                "action": {
                    "boost_score": 10,
                    "reason": "Dimension tables are well-structured for analytics"
                }
            }
        ],
        "thresholds": {
            "min_confidence_warning": 60,
            "min_confidence_for_approval": 50,
            "auto_approve_threshold": 90
        },
        "metadata": {
            "created_at": "2026-02-07",
            "version": "1.0",
            "description": "Default business rules for mapping quality"
        }
    }


def _ensure_rules_file_exists(rules_file: Path = None) -> Path:
    """
    Ensure the rules configuration file exists.

    Args:
        rules_file: Path to rules file (uses default if None)

    Returns:
        Path object to the rules file
    """
    file_path = rules_file or DEFAULT_RULES_FILE

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Create file with default rules if it doesn't exist
        if not file_path.exists():
            default_rules = _create_default_rules()
            file_path.write_text(json.dumps(default_rules, indent=2), encoding='utf-8')
            print(f"[business_rules_engine] Created default rules file at {file_path}")
    except OSError as e:
        print(f"[business_rules_engine] Cannot write rules file ({e}), using in-memory defaults")
        return file_path

    return file_path


def load_rules(rules_file: Path = None) -> Dict:
    """
    Load business rules from JSON file.

    Args:
        rules_file: Path to rules file (uses default if None)

    Returns:
        Dict with rules configuration
    """
    file_path = _ensure_rules_file_exists(rules_file)

    try:
        data = json.loads(file_path.read_text(encoding='utf-8'))
        return data
    except Exception as e:
        print(f"[business_rules_engine] Error loading rules: {e}")
        # Return default rules on error
        return _create_default_rules()


def save_rules(rules_config: Dict, rules_file: Path = None):
    """
    Save business rules to JSON file.

    Args:
        rules_config: Rules configuration dict
        rules_file: Path to rules file (uses default if None)
    """
    file_path = _ensure_rules_file_exists(rules_file)

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(rules_config, indent=2), encoding='utf-8')
        print(f"[business_rules_engine] Saved rules to {file_path}")
    except OSError as e:
        # SiS: filesystem may be read-only, rules stay in session_state only
        print(f"[business_rules_engine] Cannot persist rules to disk ({e}), changes are session-only")


def _rule_matches(mapping: Dict[str, Any], rule: Dict, source_profiles: Dict = None) -> bool:
    """
    Check if mapping matches rule conditions.

    Args:
        mapping: Mapping dictionary
        rule: Rule dictionary with conditions
        source_profiles: Optional source profile data for quality checks

    Returns:
        True if mapping matches all rule conditions
    """
    conditions = rule.get('conditions', {})

    # Check source table pattern
    source_table_pattern = conditions.get('source_table_pattern', '')
    if source_table_pattern:
        source_table = mapping.get('SourceTable', '')
        if not re.match(source_table_pattern, source_table.upper()):
            return False

    # Check target table pattern
    target_table_pattern = conditions.get('target_table_pattern', '')
    if target_table_pattern:
        target_table = mapping.get('TargetTable', '')
        if not re.match(target_table_pattern, target_table.upper()):
            return False

    # Check source column pattern
    source_column_pattern = conditions.get('source_column_pattern', '')
    if source_column_pattern:
        source_column = mapping.get('SourceColumn', '')
        if not re.match(source_column_pattern, source_column.upper()):
            return False

    # Check target column pattern
    target_column_pattern = conditions.get('target_column_pattern', '')
    if target_column_pattern:
        target_column = mapping.get('TargetColumn', '')
        if not re.match(target_column_pattern, target_column.upper()):
            return False

    # Check minimum quality score
    min_quality_score = conditions.get('min_quality_score')
    if min_quality_score is not None and source_profiles:
        source_table = mapping.get('SourceTable', '')
        source_column = mapping.get('SourceColumn', '')

        # Try to get quality score from profile
        if source_table in source_profiles:
            table_profiles = source_profiles[source_table]
            if isinstance(table_profiles, dict) and source_column in table_profiles:
                profile = table_profiles[source_column]
                quality_score = profile.get('quality_score', 0)

                if quality_score < min_quality_score:
                    return False

    # Check minimum mapping score
    min_mapping_score = conditions.get('min_mapping_score')
    if min_mapping_score is not None:
        mapping_score = mapping.get('MappingScore', 0)
        if mapping_score < min_mapping_score:
            return False

    # Check maximum mapping score
    max_mapping_score = conditions.get('max_mapping_score')
    if max_mapping_score is not None:
        mapping_score = mapping.get('MappingScore', 0)
        if mapping_score > max_mapping_score:
            return False

    # All conditions matched
    return True


def apply_rules(mappings: List[Dict[str, Any]],
               source_profiles: Dict = None,
               rules_config: Dict = None,
               rules_file: Path = None) -> List[Dict[str, Any]]:
    """
    Apply business rules to filter/boost/penalize mappings.

    Args:
        mappings: List of mapping dictionaries
        source_profiles: Optional source profile data
        rules_config: Optional rules configuration (loads from file if None)
        rules_file: Optional path to rules file

    Returns:
        List of mappings after applying rules
    """
    # Load rules if not provided
    if rules_config is None:
        rules_config = load_rules(rules_file)

    rules = rules_config.get('rules', [])

    # Filter to only enabled rules and sort by priority (highest first)
    enabled_rules = [r for r in rules if r.get('enabled', True)]
    enabled_rules.sort(key=lambda r: r.get('priority', 0), reverse=True)

    if not enabled_rules:
        print(f"[business_rules_engine] No enabled rules to apply")
        return mappings

    print(f"[business_rules_engine] Applying {len(enabled_rules)} business rules")

    # Track statistics
    filtered_count = 0
    boosted_count = 0
    penalized_count = 0

    # Apply rules
    filtered_mappings = []

    for mapping in mappings:
        # Skip UNMAPPED columns
        if mapping.get('SourceColumn') in ['UNMAPPED', 'N/A']:
            filtered_mappings.append(mapping)
            continue

        original_score = mapping.get('MappingScore', 0)
        current_mapping = mapping.copy()
        should_filter = False
        applied_rules = []

        # Apply each rule in priority order
        for rule in enabled_rules:
            if _rule_matches(current_mapping, rule, source_profiles):
                rule_type = rule.get('rule_type', '')
                action = rule.get('action', {})
                rule_name = rule.get('rule_name', 'Unknown')

                if rule_type == 'FILTER':
                    # Filter out this mapping
                    should_filter = True
                    filtered_count += 1
                    applied_rules.append(f"FILTERED by {rule_name}")
                    break  # Stop processing further rules

                elif rule_type == 'BOOST':
                    # Boost score
                    boost = action.get('boost_score', 0)
                    current_mapping['MappingScore'] = min(100, current_mapping['MappingScore'] + boost)
                    boosted_count += 1
                    applied_rules.append(f"BOOST +{boost} by {rule_name}")

                elif rule_type == 'PENALTY':
                    # Penalize score
                    penalty = action.get('penalty_score', 0)
                    current_mapping['MappingScore'] = max(0, current_mapping['MappingScore'] - penalty)
                    penalized_count += 1
                    applied_rules.append(f"PENALTY -{penalty} by {rule_name}")

        # Only keep mapping if not filtered
        if not should_filter:
            # Add note about applied rules
            if applied_rules and current_mapping['MappingScore'] != original_score:
                existing_justification = current_mapping.get('Justification', '')
                rules_note = f" [Rules: {', '.join(applied_rules)}]"
                current_mapping['Justification'] = f"{existing_justification}{rules_note}"

            filtered_mappings.append(current_mapping)

    print(f"[business_rules_engine] Results: {boosted_count} boosted, {penalized_count} penalized, {filtered_count} filtered")
    print(f"[business_rules_engine] Kept {len(filtered_mappings)}/{len(mappings)} mappings")

    return filtered_mappings


def get_rules_statistics(rules_config: Dict = None, rules_file: Path = None) -> Dict[str, Any]:
    """
    Get statistics about business rules.

    Args:
        rules_config: Optional rules configuration (loads from file if None)
        rules_file: Optional path to rules file

    Returns:
        Dict with rules statistics
    """
    if rules_config is None:
        rules_config = load_rules(rules_file)

    rules = rules_config.get('rules', [])

    enabled_count = len([r for r in rules if r.get('enabled', True)])
    disabled_count = len(rules) - enabled_count

    # Count by type
    boost_count = len([r for r in rules if r.get('rule_type') == 'BOOST' and r.get('enabled', True)])
    filter_count = len([r for r in rules if r.get('rule_type') == 'FILTER' and r.get('enabled', True)])
    penalty_count = len([r for r in rules if r.get('rule_type') == 'PENALTY' and r.get('enabled', True)])

    return {
        "total_rules": len(rules),
        "enabled_rules": enabled_count,
        "disabled_rules": disabled_count,
        "boost_rules": boost_count,
        "filter_rules": filter_count,
        "penalty_rules": penalty_count
    }


if __name__ == "__main__":
    # Test business rules engine
    print("Business Rules Engine module loaded successfully")

    # Load default rules
    rules = load_rules()
    print(f"Loaded {len(rules['rules'])} rules")

    # Test rule matching
    test_mapping = {
        'TargetColumn': 'CUSTOMER_ID',
        'TargetTable': 'DIM_CUSTOMER',
        'SourceColumn': 'CUST_KEY',
        'SourceTable': 'MASTER_CUSTOMER',
        'MappingScore': 75,
        'TransformationLogic': 'CUST_KEY',
        'Justification': 'Direct ID mapping'
    }

    # Apply rules
    result = apply_rules([test_mapping], source_profiles=None, rules_config=rules)
    print(f"Applied rules result: {len(result)} mappings")
    if result:
        print(f"  Score changed: {test_mapping['MappingScore']} -> {result[0]['MappingScore']}")
        print(f"  Justification: {result[0].get('Justification', '')}")

    # Get statistics
    stats = get_rules_statistics(rules)
    print(f"Rules statistics: {stats}")

    print("\nBusiness Rules Engine ready!")
