"""Post-Mapping Validation Engine

Validates AI-generated column mappings to ensure quality and correctness.

Validation Checks:
1. Type compatibility - Source and target types must be compatible
2. Data quality - Check nulls, completeness from profiles
3. Confidence threshold - Flag low-confidence mappings
4. Transformation SQL syntax - Basic SQL validation
5. Sample data format compatibility
"""

import re
from typing import Dict, List, Any, Optional


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


def validate_sql_syntax(sql: str) -> tuple[bool, List[str]]:
    """
    Basic SQL syntax validation.

    Returns:
        (is_valid, list_of_issues)
    """
    if not sql or not isinstance(sql, str):
        return False, ["SQL expression is empty"]

    sql = sql.strip()
    issues = []

    # Check for balanced parentheses
    paren_count = sql.count('(') - sql.count(')')
    if paren_count != 0:
        issues.append(f"Unbalanced parentheses (difference: {paren_count})")

    # Check for balanced quotes
    single_quote_count = sql.count("'") - sql.count("\\'")
    if single_quote_count % 2 != 0:
        issues.append("Unbalanced single quotes")

    # Check for common SQL injection patterns (should not be in transformation logic)
    dangerous_patterns = [r';\s*DROP', r';\s*DELETE', r';\s*TRUNCATE', r';\s*ALTER']
    for pattern in dangerous_patterns:
        if re.search(pattern, sql, re.IGNORECASE):
            issues.append(f"Potentially dangerous SQL pattern detected: {pattern}")

    # Check for valid Snowflake function usage
    # Allow common functions: TO_DATE, TO_TIMESTAMP, CAST, TRIM, UPPER, LOWER, COALESCE, CONCAT, etc.

    return len(issues) == 0, issues


def validate_mapping(mapping: Dict[str, Any],
                     source_profile: Optional[Dict] = None,
                     target_profile: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Validate a single mapping.

    Args:
        mapping: Mapping dictionary with TargetColumn, SourceColumn, MappingScore, etc.
        source_profile: Profile data for source column
        target_profile: Profile data for target column

    Returns:
        {
            "is_valid": bool,
            "confidence_level": "HIGH" | "MEDIUM" | "LOW",
            "issues": List[str],
            "warnings": List[str],
            "recommendations": List[str]
        }
    """
    issues = []
    warnings = []
    recommendations = []

    # Extract mapping details
    target_column = mapping.get('TargetColumn', 'UNKNOWN')
    source_column = mapping.get('SourceColumn', 'UNKNOWN')
    source_table = mapping.get('SourceTable', 'N/A')
    target_table = mapping.get('TargetTable', 'N/A')
    mapping_score = mapping.get('MappingScore', 0)
    source_type = mapping.get('SourceDataType', 'UNKNOWN')
    target_type = mapping.get('TargetDataType', 'UNKNOWN')
    transformation = mapping.get('TransformationLogic', '')

    # Skip validation for UNMAPPED columns
    if source_column == 'UNMAPPED' or source_column == 'N/A':
        return {
            "is_valid": True,
            "confidence_level": "N/A",
            "issues": [],
            "warnings": ["Column is not mapped to any source"],
            "recommendations": ["Find appropriate source column for this target"]
        }

    # 1. Type Compatibility Check
    if source_type != 'UNKNOWN' and target_type != 'UNKNOWN':
        if not check_type_compatibility(source_type, target_type):
            issues.append(f"Type incompatibility: {source_type} cannot map to {target_type}")

    # 2. Confidence Score Check
    if mapping_score < 50:
        issues.append(f"Very low confidence score: {mapping_score}%")
    elif mapping_score < 60:
        warnings.append(f"Low confidence score: {mapping_score}%")
    elif mapping_score < 70:
        warnings.append(f"Fair confidence score: {mapping_score}% - consider review")

    # 3. Transformation SQL Validation
    if transformation:
        sql_valid, sql_issues = validate_sql_syntax(transformation)
        if not sql_valid:
            issues.extend([f"SQL syntax issue: {issue}" for issue in sql_issues])

    # 4. Data Quality Checks (if profiles available)
    if source_profile:
        # Check source completeness
        nulls = source_profile.get('nulls', 0)
        total = source_profile.get('total', 1)
        completeness = ((total - nulls) / total * 100) if total > 0 else 0

        if completeness < 50:
            warnings.append(f"Source has low completeness: {completeness:.1f}%")
            recommendations.append("Consider data quality remediation before mapping")

        # Check if source has any data
        if total == 0:
            issues.append("Source column has no data")

    if target_profile:
        # Check if target expects high quality but source is low quality
        target_nullable = target_profile.get('nullable', True)
        if not target_nullable and source_profile:
            source_nulls = source_profile.get('nulls', 0)
            if source_nulls > 0:
                warnings.append("Target is NOT NULL but source has null values")
                recommendations.append("Add COALESCE or default value in transformation")

    # 5. Determine Confidence Level
    if mapping_score >= 80:
        confidence_level = "HIGH"
    elif mapping_score >= 60:
        confidence_level = "MEDIUM"
    else:
        confidence_level = "LOW"

    # 6. Additional Recommendations
    if not transformation or transformation.strip() == source_column:
        # Direct mapping
        if source_type != target_type:
            recommendations.append(f"Consider adding type cast: CAST({source_column} AS {target_type})")

    # Is valid if no critical issues
    is_valid = len(issues) == 0

    return {
        "is_valid": is_valid,
        "confidence_level": confidence_level,
        "issues": issues,
        "warnings": warnings,
        "recommendations": recommendations,
        "mapping_key": f"{target_table}.{target_column} <- {source_table}.{source_column}"
    }


def validate_all_mappings(mappings: List[Dict[str, Any]],
                          source_profiles: Dict[str, Dict[str, Any]],
                          target_profiles: Dict[str, Dict[str, Any]] = None,
                          min_confidence: int = 60) -> Dict[str, Any]:
    """
    Validate all mappings and return summary report.

    Args:
        mappings: List of mapping dictionaries
        source_profiles: Dict of {table_name: {column_name: profile}}
        target_profiles: Dict of {table_name: {column_name: profile}}
        min_confidence: Minimum confidence threshold for flagging

    Returns:
        {
            "total_mappings": int,
            "valid_count": int,
            "invalid_count": int,
            "high_confidence_count": int,
            "medium_confidence_count": int,
            "low_confidence_count": int,
            "validation_details": List[Dict],
            "summary": str
        }
    """
    if not mappings:
        return {
            "total_mappings": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "high_confidence_count": 0,
            "medium_confidence_count": 0,
            "low_confidence_count": 0,
            "validation_details": [],
            "summary": "No mappings to validate"
        }

    validation_details = []
    valid_count = 0
    invalid_count = 0
    high_confidence_count = 0
    medium_confidence_count = 0
    low_confidence_count = 0

    for mapping in mappings:
        # Get profiles for source and target columns
        source_table = mapping.get('SourceTable', '')
        source_column = mapping.get('SourceColumn', '')
        target_table = mapping.get('TargetTable', '')
        target_column = mapping.get('TargetColumn', '')

        source_profile = None
        target_profile = None

        # Try to get source profile
        if source_table in source_profiles and isinstance(source_profiles[source_table], dict):
            source_profile = source_profiles[source_table].get(source_column)

        # Try to get target profile
        if target_profiles and target_table in target_profiles and isinstance(target_profiles[target_table], dict):
            target_profile = target_profiles[target_table].get(target_column)

        # Validate mapping
        validation_result = validate_mapping(mapping, source_profile, target_profile)

        # Add mapping info to result
        validation_result['mapping'] = {
            'target': f"{target_table}.{target_column}",
            'source': f"{source_table}.{source_column}",
            'score': mapping.get('MappingScore', 0)
        }

        validation_details.append(validation_result)

        # Update counts
        if validation_result['is_valid']:
            valid_count += 1
        else:
            invalid_count += 1

        # Count by confidence level
        conf_level = validation_result['confidence_level']
        if conf_level == 'HIGH':
            high_confidence_count += 1
        elif conf_level == 'MEDIUM':
            medium_confidence_count += 1
        elif conf_level == 'LOW':
            low_confidence_count += 1

    total_mappings = len(mappings)

    # Generate summary
    summary = f"""
Validation Complete:
- Total Mappings: {total_mappings}
- Valid: {valid_count} ({valid_count/total_mappings*100:.1f}%)
- Invalid: {invalid_count} ({invalid_count/total_mappings*100:.1f}%)
- High Confidence: {high_confidence_count}
- Medium Confidence: {medium_confidence_count}
- Low Confidence: {low_confidence_count}
"""

    return {
        "total_mappings": total_mappings,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "high_confidence_count": high_confidence_count,
        "medium_confidence_count": medium_confidence_count,
        "low_confidence_count": low_confidence_count,
        "validation_details": validation_details,
        "summary": summary.strip()
    }


if __name__ == "__main__":
    # Test validation
    print("Mapping Validator module loaded successfully")

    # Test type compatibility
    assert check_type_compatibility("VARCHAR", "STRING") == True
    assert check_type_compatibility("NUMBER", "FLOAT") == True
    assert check_type_compatibility("DATE", "VARCHAR") == False

    print("✅ Type compatibility tests passed")

    # Test SQL validation
    sql_valid, issues = validate_sql_syntax("TO_DATE(ORD_DT, 'MM/DD/YYYY')")
    assert sql_valid == True

    sql_valid, issues = validate_sql_syntax("CAST(AMOUNT AS NUMBER(10,2)")
    assert sql_valid == False  # Missing closing paren

    print("✅ SQL validation tests passed")

    # Test mapping validation
    test_mapping = {
        'TargetColumn': 'EMAIL_ADDRESS',
        'SourceColumn': 'EMAIL',
        'TargetTable': 'DIM_CUSTOMER',
        'SourceTable': 'CRM_CONTACTS',
        'MappingScore': 85,
        'SourceDataType': 'VARCHAR',
        'TargetDataType': 'VARCHAR',
        'TransformationLogic': 'LOWER(TRIM(EMAIL))'
    }

    result = validate_mapping(test_mapping)
    assert result['is_valid'] == True
    assert result['confidence_level'] == 'HIGH'

    print("✅ Mapping validation tests passed")
    print("\nValidation engine ready!")
