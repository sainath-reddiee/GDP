"""Cross-Project Pattern Learning

Learn mapping patterns across projects and suggest best practices.

Features:
- Pattern extraction from successful mappings
- Confidence-based pattern ranking
- Industry best practices library
- Team collaboration and sharing
- Pattern recommendation engine
"""

import json
import uuid
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import Counter, defaultdict
import re


def _resolve_pattern_path() -> Path:
    """Resolve the pattern library path, preferring project data dir, falling back to /tmp."""
    project_path = Path("data/pattern_library/patterns.json")
    if project_path.exists():
        return project_path
    try:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        return project_path
    except OSError:
        pass
    return Path(tempfile.gettempdir()) / "pattern_library" / "patterns.json"


# Default path for pattern library
DEFAULT_PATTERN_LIBRARY = _resolve_pattern_path()


class PatternLibrary:
    """Manages reusable mapping patterns across projects."""

    def __init__(self, library_file: Path = None):
        """Initialize pattern library."""
        self.library_file = library_file or DEFAULT_PATTERN_LIBRARY
        self._ensure_library_exists()

    def _ensure_library_exists(self):
        """Create pattern library file if it doesn't exist."""
        try:
            self.library_file.parent.mkdir(parents=True, exist_ok=True)

            if not self.library_file.exists():
                initial_data = {
                    "patterns": [],
                    "industry_templates": self._load_industry_templates(),
                    "metadata": {
                        "created_at": datetime.now().isoformat(),
                        "total_patterns": 0,
                        "last_updated": datetime.now().isoformat()
                    }
                }
                self.library_file.write_text(json.dumps(initial_data, indent=2), encoding='utf-8')
        except OSError as e:
            print(f"[pattern_library] Cannot write library file ({e}), using in-memory defaults")

    def _load_industry_templates(self) -> List[Dict]:
        """Load pre-built industry templates."""
        return [
            {
                "template_id": "retail_customer_id",
                "industry": "retail",
                "pattern_type": "column_mapping",
                "source_pattern": ".*(?:CUST|CUSTOMER).*(?:ID|KEY|NUM)",
                "target_pattern": "CUSTOMER_ID",
                "transformation": "UPPER(TRIM({source}))",
                "confidence": 0.95,
                "description": "Standard customer ID mapping for retail",
                "usage_count": 0
            },
            {
                "template_id": "finance_transaction_date",
                "industry": "finance",
                "pattern_type": "column_mapping",
                "source_pattern": ".*(?:TXN|TRANS|TRANSACTION).*DATE",
                "target_pattern": "TRANSACTION_DATE",
                "transformation": "TO_DATE({source}, 'YYYY-MM-DD')",
                "confidence": 0.92,
                "description": "Transaction date mapping for financial data",
                "usage_count": 0
            },
            {
                "template_id": "healthcare_patient_id",
                "industry": "healthcare",
                "pattern_type": "column_mapping",
                "source_pattern": ".*(?:PAT|PATIENT).*(?:ID|NUM|KEY)",
                "target_pattern": "PATIENT_ID",
                "transformation": "UPPER(TRIM({source}))",
                "confidence": 0.93,
                "description": "Patient ID mapping for healthcare systems",
                "usage_count": 0
            },
            {
                "template_id": "ecommerce_email_standard",
                "industry": "ecommerce",
                "pattern_type": "column_mapping",
                "source_pattern": ".*EMAIL.*",
                "target_pattern": "EMAIL_ADDRESS",
                "transformation": "LOWER(TRIM({source}))",
                "confidence": 0.90,
                "description": "Email standardization for e-commerce",
                "usage_count": 0
            }
        ]

    def learn_pattern_from_mapping(self, mapping: Dict[str, Any], project_id: str = None) -> Optional[str]:
        """
        Extract and store a reusable pattern from a successful mapping.

        Args:
            mapping: Mapping dictionary with TargetColumn, SourceColumn, etc.
            project_id: Optional project identifier

        Returns:
            Pattern ID if learned, None otherwise
        """
        # Only learn from high-confidence mappings
        if mapping.get('MappingScore', 0) < 70:
            return None

        # Extract pattern
        source_col = mapping.get('SourceColumn', '')
        target_col = mapping.get('TargetColumn', '')
        source_table = mapping.get('SourceTable', '')
        target_table = mapping.get('TargetTable', '')
        transformation = mapping.get('TransformationLogic', '')

        if not source_col or not target_col:
            return None

        # Generate pattern regex
        source_pattern = self._extract_column_pattern(source_col)
        target_pattern = self._extract_column_pattern(target_col)
        table_pattern = self._extract_table_pattern(source_table, target_table)

        # Load existing patterns
        library = self._load_library()

        # Check if similar pattern exists
        for pattern in library['patterns']:
            if (pattern.get('source_pattern') == source_pattern and
                pattern.get('target_pattern') == target_pattern):
                # Update existing pattern
                pattern['occurrences'] = pattern.get('occurrences', 1) + 1
                pattern['confidence'] = min(0.99, pattern.get('confidence', 0.5) + 0.05)
                pattern['last_seen'] = datetime.now().isoformat()
                if project_id:
                    pattern['projects'] = list(set(pattern.get('projects', []) + [project_id]))
                self._save_library(library)
                return pattern['pattern_id']

        # Create new pattern
        pattern_id = str(uuid.uuid4())
        new_pattern = {
            "pattern_id": pattern_id,
            "source_pattern": source_pattern,
            "target_pattern": target_pattern,
            "table_pattern": table_pattern,
            "transformation": transformation,
            "confidence": 0.75,
            "occurrences": 1,
            "created_at": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "projects": [project_id] if project_id else [],
            "tags": self._extract_tags(source_col, target_col),
            "example": {
                "source": source_col,
                "target": target_col,
                "source_table": source_table,
                "target_table": target_table
            }
        }

        library['patterns'].append(new_pattern)
        library['metadata']['total_patterns'] = len(library['patterns'])
        library['metadata']['last_updated'] = datetime.now().isoformat()

        self._save_library(library)

        print(f"[pattern_library] Learned new pattern: {source_pattern} -> {target_pattern}")
        return pattern_id

    def _extract_column_pattern(self, column_name: str) -> str:
        """Extract regex pattern from column name."""
        # Convert to uppercase
        col_upper = column_name.upper()

        # Extract words
        words = re.findall(r'[A-Z0-9]+', col_upper)

        if not words:
            return column_name

        # Create pattern with wildcards
        # Examples:
        # CUSTOMER_ID -> .*CUSTOMER.*ID
        # EMAIL_ADDRESS -> .*EMAIL.*ADDRESS
        # FIRST_NAME -> .*FIRST.*NAME

        if len(words) == 1:
            return f".*{words[0]}.*"
        else:
            # Keep key words (usually last 1-2 words)
            if len(words) >= 3:
                key_words = words[-2:]
            else:
                key_words = words

            pattern = ".*" + ".*".join(key_words) + ".*"
            return pattern

    def _extract_table_pattern(self, source_table: str, target_table: str) -> Optional[str]:
        """Extract table naming pattern."""
        if not source_table or not target_table:
            return None

        # Extract prefix patterns (DIM_, FACT_, STG_, etc.)
        source_prefix = re.match(r'^([A-Z]+)_', source_table.upper())
        target_prefix = re.match(r'^([A-Z]+)_', target_table.upper())

        if source_prefix and target_prefix:
            return f"{source_prefix.group(1)}_* -> {target_prefix.group(1)}_*"

        return None

    def _extract_tags(self, source_col: str, target_col: str) -> List[str]:
        """Extract semantic tags from column names."""
        tags = []

        # Common data types
        if any(term in source_col.upper() for term in ['ID', 'KEY', 'NUM']):
            tags.append('identifier')
        if any(term in source_col.upper() for term in ['EMAIL', 'MAIL']):
            tags.append('email')
        if any(term in source_col.upper() for term in ['DATE', 'DT', 'TIME', 'TIMESTAMP']):
            tags.append('temporal')
        if any(term in source_col.upper() for term in ['NAME', 'TITLE']):
            tags.append('text')
        if any(term in source_col.upper() for term in ['AMOUNT', 'PRICE', 'COST', 'VALUE']):
            tags.append('numeric')
        if any(term in source_col.upper() for term in ['ADDRESS', 'ADDR', 'LOCATION']):
            tags.append('address')
        if any(term in source_col.upper() for term in ['PHONE', 'MOBILE', 'TEL']):
            tags.append('phone')

        return tags if tags else ['general']

    def get_pattern_recommendations(self, target_column: str, source_columns: List[Dict],
                                   min_confidence: float = 0.6) -> List[Dict]:
        """
        Get pattern-based recommendations for a target column.

        Args:
            target_column: Target column name
            source_columns: List of available source column dicts
            min_confidence: Minimum confidence threshold

        Returns:
            List of recommended mappings with patterns applied
        """
        library = self._load_library()

        recommendations = []

        # Try to match against learned patterns
        for pattern in library['patterns']:
            if pattern.get('confidence', 0) < min_confidence:
                continue

            target_pattern = pattern.get('target_pattern', '')

            # Check if target column matches pattern
            if re.search(target_pattern.replace('.*', '.*?'), target_column, re.IGNORECASE):
                # Find matching source columns
                source_pattern = pattern.get('source_pattern', '')

                for source_col_dict in source_columns:
                    source_col = source_col_dict.get('column_name', '')

                    if re.search(source_pattern.replace('.*', '.*?'), source_col, re.IGNORECASE):
                        recommendations.append({
                            "target_column": target_column,
                            "source_column": source_col,
                            "source_table": source_col_dict.get('source_table_name', ''),
                            "transformation": pattern.get('transformation', '').replace('{source}', source_col),
                            "confidence": pattern.get('confidence', 0.7),
                            "pattern_id": pattern.get('pattern_id'),
                            "reason": f"Matched learned pattern (seen {pattern.get('occurrences', 1)} times)",
                            "tags": pattern.get('tags', [])
                        })

        # Try industry templates
        for template in library.get('industry_templates', []):
            target_pattern = template.get('target_pattern', '')

            if re.search(target_pattern.replace('.*', '.*?'), target_column, re.IGNORECASE):
                source_pattern = template.get('source_pattern', '')

                for source_col_dict in source_columns:
                    source_col = source_col_dict.get('column_name', '')

                    if re.search(source_pattern.replace('.*', '.*?'), source_col, re.IGNORECASE):
                        recommendations.append({
                            "target_column": target_column,
                            "source_column": source_col,
                            "source_table": source_col_dict.get('source_table_name', ''),
                            "transformation": template.get('transformation', '').replace('{source}', source_col),
                            "confidence": template.get('confidence', 0.8),
                            "pattern_id": template.get('template_id'),
                            "reason": f"Industry template ({template.get('industry', 'general')})",
                            "tags": [template.get('industry', 'general')]
                        })

        # Sort by confidence
        recommendations.sort(key=lambda x: x['confidence'], reverse=True)

        return recommendations[:5]  # Top 5 recommendations

    def get_pattern_statistics(self) -> Dict[str, Any]:
        """Get statistics about learned patterns."""
        library = self._load_library()
        patterns = library.get('patterns', [])

        if not patterns:
            return {
                "total_patterns": 0,
                "avg_confidence": 0,
                "total_occurrences": 0,
                "top_tags": [],
                "high_confidence_patterns": 0,
                "recent_patterns": 0
            }

        # Calculate statistics
        total_patterns = len(patterns)
        avg_confidence = sum(p.get('confidence', 0) for p in patterns) / total_patterns
        total_occurrences = sum(p.get('occurrences', 1) for p in patterns)

        # Count tags
        tag_counter = Counter()
        for pattern in patterns:
            tags = pattern.get('tags', [])
            tag_counter.update(tags)

        return {
            "total_patterns": total_patterns,
            "avg_confidence": avg_confidence,
            "total_occurrences": total_occurrences,
            "top_tags": tag_counter.most_common(10),
            "high_confidence_patterns": len([p for p in patterns if p.get('confidence', 0) >= 0.8]),
            "recent_patterns": len([p for p in patterns if p.get('last_seen', '') >= datetime.now().isoformat()[:10]])
        }

    def _load_library(self) -> Dict:
        """Load pattern library from file."""
        try:
            return json.loads(self.library_file.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"[pattern_library] Error loading library: {e}")
            return {"patterns": [], "industry_templates": [], "metadata": {}}

    def _save_library(self, library: Dict):
        """Save pattern library to file."""
        try:
            self.library_file.write_text(json.dumps(library, indent=2), encoding='utf-8')
        except Exception as e:
            print(f"[pattern_library] Error saving library: {e}")


if __name__ == "__main__":
    print("Pattern Library module loaded successfully")

    # Test pattern learning
    test_library = PatternLibrary()

    # Example mapping
    test_mapping = {
        'TargetColumn': 'CUSTOMER_ID',
        'TargetTable': 'DIM_CUSTOMER',
        'SourceColumn': 'CUST_KEY',
        'SourceTable': 'CRM_CONTACTS',
        'MappingScore': 95,
        'TransformationLogic': 'UPPER(TRIM(CUST_KEY))'
    }

    pattern_id = test_library.learn_pattern_from_mapping(test_mapping, project_id="test_project")
    print(f"Learned pattern: {pattern_id}")

    # Get statistics
    stats = test_library.get_pattern_statistics()
    print(f"Pattern statistics: {stats}")

    # Test recommendations
    source_cols = [
        {'column_name': 'CUSTOMER_KEY', 'source_table_name': 'CRM'},
        {'column_name': 'EMAIL_ADDR', 'source_table_name': 'CONTACTS'}
    ]

    recommendations = test_library.get_pattern_recommendations('CUSTOMER_ID', source_cols)
    print(f"Recommendations: {len(recommendations)}")

    print("\nPattern Library ready!")
