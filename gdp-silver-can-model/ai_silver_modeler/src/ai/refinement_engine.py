"""Iterative Refinement Engine for Mapping Improvement

Allows users to iteratively refine mappings with additional feedback.

Features:
- Create enhanced business context from user feedback
- Incorporate approved/rejected mapping examples
- Focus on low-confidence mappings
- Calculate improvement statistics
- Track refinement iterations
"""

import json
import uuid
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional


def _resolve_refinement_path() -> Path:
    """Resolve the refinement file path, preferring project data dir, falling back to /tmp."""
    project_path = Path("data/mapping_feedback/refinement_history.json")
    if project_path.exists():
        return project_path
    try:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        return project_path
    except OSError:
        pass
    return Path(tempfile.gettempdir()) / "mapping_feedback" / "refinement_history.json"


# Default path for refinement history
DEFAULT_REFINEMENT_FILE = _resolve_refinement_path()


def _ensure_refinement_file_exists(refinement_file: Path = None) -> Path:
    """
    Ensure the refinement history file exists.

    Args:
        refinement_file: Path to refinement file (uses default if None)

    Returns:
        Path object to the refinement file
    """
    file_path = refinement_file or DEFAULT_REFINEMENT_FILE

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Create file with empty structure if it doesn't exist
        if not file_path.exists():
            initial_data = {
                "iterations": [],
                "metadata": {
                    "created_at": datetime.now().isoformat(),
                    "total_iterations": 0
                }
            }
            file_path.write_text(json.dumps(initial_data, indent=2), encoding='utf-8')
    except OSError as e:
        print(f"[refinement_engine] Cannot write refinement file ({e}), using in-memory defaults")

    return file_path


def create_refinement_prompt(original_mappings: List[Dict[str, Any]],
                            user_feedback: str,
                            low_confidence_mappings: List[Dict[str, Any]] = None,
                            approved_mappings: List[Dict[str, Any]] = None,
                            rejected_mappings: List[Dict[str, Any]] = None) -> str:
    """
    Create enhanced business context for refinement iteration.

    Combines:
    - User's explicit feedback
    - Examples from approved mappings (good patterns)
    - Examples from rejected mappings (patterns to avoid)
    - Focus areas (low confidence mappings)

    Args:
        original_mappings: Original mapping results
        user_feedback: User's explicit feedback/instructions
        low_confidence_mappings: Optional list of low-confidence mappings to focus on
        approved_mappings: Optional list of approved mappings (good examples)
        rejected_mappings: Optional list of rejected mappings (bad examples)

    Returns:
        Enhanced business context string
    """
    prompt_parts = []

    # Part 1: User's explicit feedback
    if user_feedback and user_feedback.strip():
        prompt_parts.append(f"USER FEEDBACK:\n{user_feedback.strip()}\n")

    # Part 2: Approved mappings as good examples
    if approved_mappings and len(approved_mappings) > 0:
        prompt_parts.append("APPROVED MAPPINGS (Good Examples to Follow):")
        for mapping in approved_mappings[:5]:  # Limit to top 5
            target = f"{mapping.get('TargetTable')}.{mapping.get('TargetColumn')}"
            source = f"{mapping.get('SourceTable')}.{mapping.get('SourceColumn')}"
            score = mapping.get('MappingScore', 0)
            prompt_parts.append(f"  ✓ {target} ← {source} (Score: {score}%)")
        prompt_parts.append("")

    # Part 3: Rejected mappings as bad examples
    if rejected_mappings and len(rejected_mappings) > 0:
        prompt_parts.append("REJECTED MAPPINGS (Patterns to Avoid):")
        for mapping in rejected_mappings[:5]:  # Limit to top 5
            target = f"{mapping.get('TargetTable')}.{mapping.get('TargetColumn')}"
            source = f"{mapping.get('SourceTable')}.{mapping.get('SourceColumn')}"
            score = mapping.get('MappingScore', 0)
            prompt_parts.append(f"  ✗ {target} ← {source} (Score: {score}%) - AVOID THIS PATTERN")
        prompt_parts.append("")

    # Part 4: Low confidence mappings that need improvement
    if low_confidence_mappings and len(low_confidence_mappings) > 0:
        prompt_parts.append(f"FOCUS ON IMPROVING THESE {len(low_confidence_mappings)} LOW-CONFIDENCE MAPPINGS:")
        for mapping in low_confidence_mappings[:10]:  # Limit to top 10
            target = f"{mapping.get('TargetTable')}.{mapping.get('TargetColumn')}"
            source = f"{mapping.get('SourceTable')}.{mapping.get('SourceColumn')}"
            score = mapping.get('MappingScore', 0)
            prompt_parts.append(f"  ⚠ {target} ← {source} (Current Score: {score}%) - NEEDS BETTER MATCH")
        prompt_parts.append("")

    # Part 5: General instructions
    prompt_parts.append("REFINEMENT GOALS:")
    prompt_parts.append("- Improve mapping confidence scores")
    prompt_parts.append("- Find better source columns based on approved examples")
    prompt_parts.append("- Avoid patterns that were rejected")
    prompt_parts.append("- Focus on low-confidence mappings that need improvement")

    enhanced_context = "\n".join(prompt_parts)

    return enhanced_context


def calculate_improvement_stats(original: List[Dict[str, Any]],
                                refined: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate improvement statistics between original and refined mappings.

    Args:
        original: Original mapping results
        refined: Refined mapping results

    Returns:
        Dict with improvement statistics
    """
    # Create mapping keys for comparison
    original_map = {}
    for mapping in original:
        key = f"{mapping.get('TargetTable')}.{mapping.get('TargetColumn')}"
        original_map[key] = mapping

    refined_map = {}
    for mapping in refined:
        key = f"{mapping.get('TargetTable')}.{mapping.get('TargetColumn')}"
        refined_map[key] = mapping

    # Calculate statistics
    total_mappings = len(refined_map)
    improved_count = 0
    degraded_count = 0
    unchanged_count = 0
    total_score_change = 0

    score_changes = []

    for key, refined_mapping in refined_map.items():
        if key in original_map:
            original_score = original_map[key].get('MappingScore', 0)
            refined_score = refined_mapping.get('MappingScore', 0)
            score_change = refined_score - original_score

            score_changes.append({
                'mapping': key,
                'original_score': original_score,
                'refined_score': refined_score,
                'change': score_change
            })

            total_score_change += score_change

            if score_change > 0:
                improved_count += 1
            elif score_change < 0:
                degraded_count += 1
            else:
                unchanged_count += 1

    # Calculate averages
    avg_score_original = sum(m.get('MappingScore', 0) for m in original) / len(original) if original else 0
    avg_score_refined = sum(m.get('MappingScore', 0) for m in refined) / len(refined) if refined else 0
    avg_improvement = avg_score_refined - avg_score_original

    # Sort by improvement (largest improvements first)
    score_changes.sort(key=lambda x: x['change'], reverse=True)

    return {
        'total_mappings': total_mappings,
        'improved_count': improved_count,
        'degraded_count': degraded_count,
        'unchanged_count': unchanged_count,
        'avg_score_original': avg_score_original,
        'avg_score_refined': avg_score_refined,
        'avg_improvement': avg_improvement,
        'total_score_change': total_score_change,
        'top_improvements': score_changes[:10],  # Top 10 improvements
        'top_degradations': score_changes[-5:] if degraded_count > 0 else []  # Top 5 degradations
    }


def record_refinement_iteration(iteration: int,
                                feedback: str,
                                stats: Dict[str, Any],
                                refinement_file: Path = None) -> str:
    """
    Record refinement iteration to history file.

    Args:
        iteration: Iteration number
        feedback: User feedback for this iteration
        stats: Improvement statistics
        refinement_file: Path to refinement file (uses default if None)

    Returns:
        UUID of the iteration record
    """
    file_path = _ensure_refinement_file_exists(refinement_file)

    try:
        data = json.loads(file_path.read_text(encoding='utf-8'))
    except Exception:
        data = {
            "iterations": [],
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "total_iterations": 0
            }
        }

    # Create iteration record
    iteration_id = str(uuid.uuid4())
    iteration_record = {
        "iteration_id": iteration_id,
        "iteration": iteration,
        "timestamp": datetime.now().isoformat(),
        "user_feedback": feedback,
        "improvement_stats": stats
    }

    # Add to history
    data["iterations"].append(iteration_record)

    # Update metadata
    data["metadata"]["total_iterations"] = len(data["iterations"])

    # Save
    try:
        file_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"[refinement_engine] Error saving iteration: {e}")

    print(f"[refinement_engine] Recorded iteration {iteration}")

    return iteration_id


def get_refinement_history(refinement_file: Path = None) -> Dict:
    """
    Get refinement history.

    Args:
        refinement_file: Path to refinement file (uses default if None)

    Returns:
        Dict with refinement history
    """
    file_path = _ensure_refinement_file_exists(refinement_file)

    try:
        data = json.loads(file_path.read_text(encoding='utf-8'))
        return data
    except Exception as e:
        print(f"[refinement_engine] Error loading history: {e}")
        return {
            "iterations": [],
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "total_iterations": 0
            }
        }


if __name__ == "__main__":
    # Test refinement engine
    print("Refinement Engine module loaded successfully")

    # Test creating refinement prompt
    test_user_feedback = """
    For CUSTOMER_ID columns, prefer MASTER_CUSTOMER table over STG_CUSTOMERS.
    EMAIL columns should map to EMAIL_ADDRESS, not EMAIL_ADDR.
    """

    test_approved = [
        {
            'TargetTable': 'DIM_CUSTOMER',
            'TargetColumn': 'CUSTOMER_ID',
            'SourceTable': 'MASTER_CUSTOMER',
            'SourceColumn': 'CUST_KEY',
            'MappingScore': 95
        }
    ]

    test_rejected = [
        {
            'TargetTable': 'DIM_CUSTOMER',
            'TargetColumn': 'CUSTOMER_ID',
            'SourceTable': 'STG_CUSTOMERS',
            'SourceColumn': 'CUST_NUM',
            'MappingScore': 65
        }
    ]

    test_low_confidence = [
        {
            'TargetTable': 'DIM_PRODUCT',
            'TargetColumn': 'PRODUCT_NAME',
            'SourceTable': 'PRODUCTS',
            'SourceColumn': 'NAME',
            'MappingScore': 45
        }
    ]

    enhanced_prompt = create_refinement_prompt(
        [],
        test_user_feedback,
        test_low_confidence,
        test_approved,
        test_rejected
    )

    print(f"Enhanced prompt created ({len(enhanced_prompt)} characters)")
    print("\n--- Sample Enhanced Prompt ---")
    print(enhanced_prompt[:500])
    print("...\n")

    # Test improvement statistics
    original_mappings = [
        {'TargetTable': 'DIM_CUSTOMER', 'TargetColumn': 'ID', 'MappingScore': 70},
        {'TargetTable': 'DIM_CUSTOMER', 'TargetColumn': 'NAME', 'MappingScore': 60}
    ]

    refined_mappings = [
        {'TargetTable': 'DIM_CUSTOMER', 'TargetColumn': 'ID', 'MappingScore': 85},
        {'TargetTable': 'DIM_CUSTOMER', 'TargetColumn': 'NAME', 'MappingScore': 75}
    ]

    stats = calculate_improvement_stats(original_mappings, refined_mappings)
    print(f"Improvement stats: {stats['improved_count']} improved, avg +{stats['avg_improvement']:.1f}%")

    # Record iteration
    iteration_id = record_refinement_iteration(1, test_user_feedback, stats)
    print(f"Recorded iteration: {iteration_id}")

    print("\nRefinement Engine ready!")
