"""Approval Manager for Mapping Review Workflow

Manages user approval/rejection of AI-generated mappings.

Features:
- Record approval/rejection decisions
- Track approval history
- Generate approval statistics
- Support for review notes
"""

import json
import uuid
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional


def _resolve_approval_path() -> Path:
    """Resolve the approval file path, preferring project data dir, falling back to /tmp."""
    project_path = Path("data/mapping_feedback/approval_history.json")
    if project_path.exists():
        return project_path
    try:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        return project_path
    except OSError:
        pass
    return Path(tempfile.gettempdir()) / "mapping_feedback" / "approval_history.json"


# Default path for approval history
DEFAULT_APPROVAL_FILE = _resolve_approval_path()


def _ensure_approval_file_exists(approval_file: Path = None) -> Path:
    """
    Ensure the approval history file exists.

    Args:
        approval_file: Path to approval file (uses default if None)

    Returns:
        Path object to the approval file
    """
    file_path = approval_file or DEFAULT_APPROVAL_FILE

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Create file with empty structure if it doesn't exist
        if not file_path.exists():
            initial_data = {
                "approvals": [],
                "metadata": {
                    "created_at": datetime.now().isoformat(),
                    "total_approvals": 0,
                    "total_rejections": 0,
                    "total_pending": 0
                }
            }
            file_path.write_text(json.dumps(initial_data, indent=2), encoding='utf-8')
    except OSError as e:
        print(f"[approval_manager] Cannot write approval file ({e}), using in-memory defaults")

    return file_path


def load_approval_history(approval_file: Path = None) -> Dict:
    """
    Load approval history from JSON file.

    Args:
        approval_file: Path to approval file (uses default if None)

    Returns:
        Dict with approval history
    """
    file_path = _ensure_approval_file_exists(approval_file)

    try:
        data = json.loads(file_path.read_text(encoding='utf-8'))
        return data
    except Exception as e:
        print(f"[approval_manager] Error loading approval history: {e}")
        # Return empty structure on error
        return {
            "approvals": [],
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "total_approvals": 0,
                "total_rejections": 0,
                "total_pending": 0
            }
        }


def save_approval_history(data: Dict, approval_file: Path = None):
    """
    Save approval history to JSON file.

    Args:
        data: Approval history data
        approval_file: Path to approval file (uses default if None)
    """
    file_path = _ensure_approval_file_exists(approval_file)

    try:
        file_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"[approval_manager] Error saving approval history: {e}")


def record_approval(mapping: Dict[str, Any],
                   status: str,
                   notes: str = "",
                   reviewer: str = "unknown",
                   approval_file: Path = None) -> str:
    """
    Record approval/rejection decision for a mapping.

    Args:
        mapping: Mapping dictionary (TargetColumn, SourceColumn, etc.)
        status: "APPROVED", "REJECTED", or "PENDING"
        notes: Optional review notes
        reviewer: Username or identifier of reviewer
        approval_file: Path to approval file (uses default if None)

    Returns:
        UUID of the approval record
    """
    if status not in ["APPROVED", "REJECTED", "PENDING"]:
        raise ValueError(f"Invalid status: {status}. Must be APPROVED, REJECTED, or PENDING")

    # Load existing history
    history = load_approval_history(approval_file)

    # Create new approval record
    approval_id = str(uuid.uuid4())
    approval_record = {
        "approval_id": approval_id,
        "timestamp": datetime.now().isoformat(),
        "mapping": {
            "target_column": mapping.get('TargetColumn', 'UNKNOWN'),
            "target_table": mapping.get('TargetTable', 'UNKNOWN'),
            "target_schema": mapping.get('TargetSchema', 'UNKNOWN'),
            "source_column": mapping.get('SourceColumn', 'UNKNOWN'),
            "source_table": mapping.get('SourceTable', 'UNKNOWN'),
            "source_schema": mapping.get('SourceSchema', 'UNKNOWN'),
            "mapping_score": mapping.get('MappingScore', 0),
            "transformation_logic": mapping.get('TransformationLogic', ''),
            "justification": mapping.get('Justification', '')
        },
        "status": status,
        "reviewer": reviewer,
        "review_notes": notes
    }

    # Add to history
    history["approvals"].append(approval_record)

    # Update metadata
    if status == "APPROVED":
        history["metadata"]["total_approvals"] = history["metadata"].get("total_approvals", 0) + 1
    elif status == "REJECTED":
        history["metadata"]["total_rejections"] = history["metadata"].get("total_rejections", 0) + 1
    elif status == "PENDING":
        history["metadata"]["total_pending"] = history["metadata"].get("total_pending", 0) + 1

    # Save updated history
    save_approval_history(history, approval_file)

    print(f"[approval_manager] Recorded {status} for {mapping.get('TargetTable')}.{mapping.get('TargetColumn')}")

    return approval_id


def get_approval_statistics(approval_file: Path = None) -> Dict[str, int]:
    """
    Get approval statistics.

    Args:
        approval_file: Path to approval file (uses default if None)

    Returns:
        Dict with approval statistics
    """
    history = load_approval_history(approval_file)

    approvals = history.get("approvals", [])
    total = len(approvals)

    # Count by status
    approved_count = sum(1 for a in approvals if a.get('status') == 'APPROVED')
    rejected_count = sum(1 for a in approvals if a.get('status') == 'REJECTED')
    pending_count = sum(1 for a in approvals if a.get('status') == 'PENDING')

    # Calculate rates
    approval_rate = (approved_count / total * 100) if total > 0 else 0
    rejection_rate = (rejected_count / total * 100) if total > 0 else 0

    return {
        "total_reviews": total,
        "approved_count": approved_count,
        "rejected_count": rejected_count,
        "pending_count": pending_count,
        "approval_rate": approval_rate,
        "rejection_rate": rejection_rate
    }


def get_approvals_by_status(status: str, approval_file: Path = None) -> List[Dict]:
    """
    Get all approvals matching a specific status.

    Args:
        status: "APPROVED", "REJECTED", or "PENDING"
        approval_file: Path to approval file (uses default if None)

    Returns:
        List of approval records matching the status
    """
    history = load_approval_history(approval_file)
    approvals = history.get("approvals", [])

    return [a for a in approvals if a.get('status') == status]


def get_approval_for_mapping(target_table: str, target_column: str,
                            approval_file: Path = None) -> Optional[Dict]:
    """
    Get the most recent approval decision for a specific mapping.

    Args:
        target_table: Target table name
        target_column: Target column name
        approval_file: Path to approval file (uses default if None)

    Returns:
        Most recent approval record or None if not found
    """
    history = load_approval_history(approval_file)
    approvals = history.get("approvals", [])

    # Find all approvals for this mapping (most recent first)
    matching_approvals = [
        a for a in reversed(approvals)
        if a.get('mapping', {}).get('target_table') == target_table
        and a.get('mapping', {}).get('target_column') == target_column
    ]

    return matching_approvals[0] if matching_approvals else None


def bulk_approve(mappings: List[Dict[str, Any]],
                min_confidence: int = 90,
                reviewer: str = "bulk_action",
                approval_file: Path = None) -> int:
    """
    Bulk approve all mappings with confidence >= min_confidence.

    Args:
        mappings: List of mapping dictionaries
        min_confidence: Minimum confidence score to auto-approve (default: 90)
        reviewer: Username or identifier of reviewer
        approval_file: Path to approval file (uses default if None)

    Returns:
        Number of mappings approved
    """
    approved_count = 0

    for mapping in mappings:
        score = mapping.get('MappingScore', 0)
        if score >= min_confidence:
            record_approval(
                mapping,
                "APPROVED",
                notes=f"Auto-approved (confidence >= {min_confidence}%)",
                reviewer=reviewer,
                approval_file=approval_file
            )
            approved_count += 1

    return approved_count


def bulk_flag_low_confidence(mappings: List[Dict[str, Any]],
                             max_confidence: int = 60,
                             reviewer: str = "bulk_action",
                             approval_file: Path = None) -> int:
    """
    Bulk flag all mappings with confidence <= max_confidence as PENDING.

    Args:
        mappings: List of mapping dictionaries
        max_confidence: Maximum confidence score to flag (default: 60)
        reviewer: Username or identifier of reviewer
        approval_file: Path to approval file (uses default if None)

    Returns:
        Number of mappings flagged
    """
    flagged_count = 0

    for mapping in mappings:
        score = mapping.get('MappingScore', 0)
        if score <= max_confidence:
            record_approval(
                mapping,
                "PENDING",
                notes=f"Flagged for review (confidence <= {max_confidence}%)",
                reviewer=reviewer,
                approval_file=approval_file
            )
            flagged_count += 1

    return flagged_count


if __name__ == "__main__":
    # Test approval manager
    print("Approval Manager module loaded successfully")

    # Test creating approval record
    test_mapping = {
        'TargetColumn': 'EMAIL_ADDRESS',
        'TargetTable': 'DIM_CUSTOMER',
        'TargetSchema': 'SILVER',
        'SourceColumn': 'EMAIL',
        'SourceTable': 'CRM_CONTACTS',
        'SourceSchema': 'BRONZE',
        'MappingScore': 88,
        'TransformationLogic': 'LOWER(TRIM(EMAIL))',
        'Justification': 'Direct email field mapping'
    }

    approval_id = record_approval(
        test_mapping,
        "APPROVED",
        notes="Looks good!",
        reviewer="test_user"
    )

    print(f"Created approval record: {approval_id}")

    # Test getting statistics
    stats = get_approval_statistics()
    print(f"Approval statistics: {stats}")

    print("\nApproval Manager ready!")
