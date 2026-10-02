"""Natural Language Mapping Interface

Conversational AI that interprets user intent and generates mappings.

Features:
- Natural language query understanding
- Intent classification (map, search, update, validate)
- Interactive clarification
- Context-aware responses
- Multi-turn conversations
"""

import json
from typing import Dict, Any


class NLMappingInterface:
    """Natural Language interface for column mapping operations."""

    def __init__(self, conn=None):
        """Initialize NL interface with optional Snowflake connection."""
        self.conn = conn
        self.conversation_history = []

    def interpret_query(self, user_query: str, context: Dict = None, conn=None) -> Dict[str, Any]:
        """
        Interpret user's natural language query using Snowflake Cortex LLM.

        Args:
            user_query: User's natural language input
            context: Optional context (current mappings, tables, etc.)
            conn: Optional Snowflake connection (uses self.conn if not provided)

        Returns:
            {
                "intent": "map|search|update|validate|help",
                "entities": {...},
                "confidence": 0.0-1.0,
                "clarification_needed": bool,
                "clarification_questions": [...],
                "suggested_action": {...}
            }
        """
        # Use provided connection or fallback to instance connection
        active_conn = conn or self.conn

        if not active_conn:
            print("[nl_interface] No connection provided, using fallback interpretation")
            return self._fallback_interpretation(user_query)

        try:
            # Build context for LLM
            context_str = self._build_context_string(context) if context else ""

            # Create interpretation prompt
            prompt = f"""You are an AI assistant specialized in data mapping operations.

User Query: {user_query}

Current Context:
{context_str}

Your Task: Interpret the user's intent and extract relevant entities.

Output JSON Schema:
{{
  "intent": "map|search|update|validate|help|explain",
  "entities": {{
    "target_tables": ["table1", "table2"],
    "target_columns": ["col1", "col2"],
    "source_tables": ["table1"],
    "source_columns": ["col1"],
    "conditions": ["condition1"],
    "transformations": ["transformation1"]
  }},
  "confidence": 0.85,
  "interpretation": "Brief interpretation of what user wants",
  "clarification_needed": false,
  "clarification_questions": [],
  "suggested_action": {{
    "action_type": "execute_mapping|show_results|ask_confirmation",
    "action_params": {{}}
  }}
}}

Examples:

1. Query: Map all customer ID fields from CRM to customer dimension
   Intent: map
   Entities: target_tables: customer dimension, target_columns: customer ID, source_tables: CRM

2. Query: Show me all email columns
   Intent: search
   Entities: target_columns: email

3. Query: All email addresses should be lowercase
   Intent: update
   Entities: target_columns: email, transformations: lowercase

RESPOND WITH VALID JSON ONLY, NO MARKDOWN"""

            # Call Cortex LLM using native Snowflake function
            cursor = active_conn.cursor()

            # Escape single quotes in prompt
            escaped_prompt = prompt.replace("'", "''")

            cortex_query = f"""
            SELECT SNOWFLAKE.CORTEX.COMPLETE(
                'mistral-large2',
                '{escaped_prompt}'
            ) as response
            """

            cursor.execute(cortex_query)
            result = cursor.fetchone()

            if result and result[0]:
                response_text = result[0].strip()

                # Parse JSON response - handle markdown code blocks
                if response_text.startswith('```'):
                    lines = response_text.split('\n')
                    # Remove first and last lines (``` markers)
                    response_text = '\n'.join(lines[1:-1])
                    if response_text.startswith('json'):
                        response_text = response_text[4:].strip()

                try:
                    interpretation = json.loads(response_text)
                except json.JSONDecodeError as je:
                    print(f"[nl_interface] JSON parse error: {je}")
                    print(f"[nl_interface] Response text: {response_text[:500]}")
                    return self._fallback_interpretation(user_query)

                # Add to conversation history
                self.conversation_history.append({
                    "user_query": user_query,
                    "interpretation": interpretation
                })

                return interpretation

            return self._fallback_interpretation(user_query)

        except Exception as e:
            print(f"[nl_interface] Error interpreting query: {e}")
            import traceback
            traceback.print_exc()
            return self._fallback_interpretation(user_query)

    def answer_with_context(self, user_query: str, context: Dict, conn=None) -> str:
        """
        Answer user query directly using LLM with full mapping context.

        This gives the LLM access to actual mapping data so it can provide
        specific, intelligent answers about the user's mappings.
        """
        active_conn = conn or self.conn

        if not active_conn:
            return self._fallback_answer(user_query, context)

        try:
            # Build rich context with actual mapping data
            context_str = self._build_rich_context(context)

            prompt = f"""You are an AI data mapping assistant. Answer the user's question about their Bronze-to-Silver column mappings.

USER QUESTION: {user_query}

CURRENT MAPPING DATA:
{context_str}

INSTRUCTIONS:
- Give a SPECIFIC, HELPFUL answer based on the actual mapping data above
- If the user asks about a specific column, find it in the data and explain the mapping
- If the user asks to search, list matching mappings with details
- If the user asks about quality or issues, analyze the mapping scores and flag concerns
- If the user asks to update/change a mapping, explain what they should change and where
- Use markdown formatting for readability
- Be concise but thorough

ANSWER:"""

            cursor = active_conn.cursor()
            escaped_prompt = prompt.replace("'", "''")

            query = f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{escaped_prompt}') AS response"
            cursor.execute(query)
            result = cursor.fetchone()
            cursor.close()

            if result and result[0]:
                response = result[0].strip()
                # Clean markdown code block wrapper if present
                if response.startswith('```'):
                    lines = response.split('\n')
                    response = '\n'.join(lines[1:-1])
                return response

            return self._fallback_answer(user_query, context)

        except Exception as e:
            print(f"[nl_interface] Error in answer_with_context: {e}")
            import traceback
            traceback.print_exc()
            return self._fallback_answer(user_query, context)

    def _build_rich_context(self, context: Dict) -> str:
        """Build rich context string including actual mapping data."""
        parts = []
        mappings = context.get('current_mappings', [])

        if not mappings:
            return "No mappings available yet."

        parts.append(f"Total Mappings: {len(mappings)}")

        tables = context.get('available_tables', [])
        if tables:
            parts.append(f"Source Tables: {', '.join(tables[:10])}")

        # Include actual mapping data (limit to prevent token overflow)
        parts.append("\nMapping Details (TargetColumn -> SourceTable.SourceColumn | Score | Transformation):")
        for i, m in enumerate(mappings[:50]):
            target = m.get('TargetColumn', '?')
            source_table = m.get('SourceTable', '?')
            source_col = m.get('SourceColumn', '?')
            score = m.get('MappingScore', 0)
            transform = m.get('TransformationLogic', '')[:60]
            justification = m.get('Justification', '')[:60]
            target_desc = m.get('TargetDescription', '')[:40]
            source_desc = m.get('SourceDescription', '')[:40]
            parts.append(
                f"  {i+1}. {target} ({target_desc}) -> {source_table}.{source_col} ({source_desc}) "
                f"| Score: {score}% | Transform: {transform} | Reason: {justification}"
            )

        if len(mappings) > 50:
            parts.append(f"  ... and {len(mappings) - 50} more mappings")

        # Summary stats
        scores = [m.get('MappingScore', 0) for m in mappings]
        avg_score = sum(scores) / len(scores) if scores else 0
        low_conf = sum(1 for s in scores if s < 60)
        unmapped = sum(1 for m in mappings if m.get('SourceColumn') in ['N/A', 'UNMAPPED'])

        parts.append(f"\nSummary: Avg Score={avg_score:.0f}%, Low Confidence (<60%)={low_conf}, Unmapped={unmapped}")

        return "\n".join(parts)

    def _fallback_answer(self, user_query: str, context: Dict) -> str:
        """Generate a basic answer without LLM when connection is unavailable."""
        mappings = context.get('current_mappings', [])
        query_lower = user_query.lower()

        # Basic search
        results = []
        for m in mappings:
            target = m.get('TargetColumn', '').lower()
            source = m.get('SourceColumn', '').lower()
            if any(term in target or term in source for term in query_lower.split()):
                results.append(m)

        if results:
            lines = [f"Found {len(results)} matching mapping(s):"]
            for r in results[:10]:
                lines.append(f"- {r.get('TargetColumn')} <- {r.get('SourceTable')}.{r.get('SourceColumn')} (Score: {r.get('MappingScore', 0)}%)")
            return "\n".join(lines)

        return f"I found {len(mappings)} total mappings. Could you be more specific about what you'd like to know?"

    def _build_context_string(self, context: Dict) -> str:
        """Build context string from context dictionary."""
        parts = []

        if 'current_mappings' in context:
            mappings_count = len(context['current_mappings'])
            parts.append(f"- Current Mappings: {mappings_count} mappings loaded")

        if 'available_tables' in context:
            parts.append(f"- Available Tables: {', '.join(context['available_tables'][:5])}")

        if 'recent_actions' in context:
            parts.append(f"- Recent Action: {context['recent_actions'][-1] if context['recent_actions'] else 'None'}")

        return "\n".join(parts) if parts else "No context available"

    def _fallback_interpretation(self, query: str) -> Dict:
        """Simple rule-based fallback interpretation."""
        query_lower = query.lower()

        # Simple pattern matching
        intent = "help"
        if any(word in query_lower for word in ["map", "mapping", "connect", "link"]):
            intent = "map"
        elif any(word in query_lower for word in ["show", "find", "search", "list"]):
            intent = "search"
        elif any(word in query_lower for word in ["update", "change", "modify", "set"]):
            intent = "update"
        elif any(word in query_lower for word in ["validate", "check", "verify"]):
            intent = "validate"

        return {
            "intent": intent,
            "entities": {},
            "confidence": 0.5,
            "interpretation": f"Detected intent: {intent} (fallback mode)",
            "clarification_needed": True,
            "clarification_questions": ["Could you be more specific about which tables/columns you want to work with?"],
            "suggested_action": {"action_type": "ask_confirmation", "action_params": {}}
        }

    def execute_intent(self, interpretation: Dict, context: Dict) -> Dict[str, Any]:
        """
        Execute the interpreted intent.

        Args:
            interpretation: Parsed user intent
            context: Execution context

        Returns:
            Execution result
        """
        intent = interpretation.get('intent', 'help')

        if intent == 'map':
            return self._execute_mapping_intent(interpretation, context)
        elif intent == 'search':
            return self._execute_search_intent(interpretation, context)
        elif intent == 'update':
            return self._execute_update_intent(interpretation, context)
        elif intent == 'validate':
            return self._execute_validation_intent(interpretation, context)
        else:
            return {
                "success": True,
                "message": "I can help you with mapping, searching, updating, or validating column mappings. What would you like to do?",
                "suggestions": [
                    "Map customer ID fields",
                    "Show all email columns",
                    "Validate current mappings"
                ]
            }

    def _execute_mapping_intent(self, interpretation: Dict, context: Dict) -> Dict:
        """Execute mapping operation based on interpretation."""
        entities = interpretation.get('entities', {})

        # Extract target and source information
        target_columns = entities.get('target_columns', [])
        source_tables = entities.get('source_tables', [])

        return {
            "success": True,
            "action": "create_mappings",
            "parameters": {
                "target_columns": target_columns,
                "source_tables": source_tables,
                "auto_execute": interpretation.get('confidence', 0) > 0.8
            },
            "message": f"I'll create mappings for {len(target_columns)} target column(s) from {len(source_tables)} source table(s).",
            "requires_confirmation": interpretation.get('confidence', 0) < 0.8
        }

    def _execute_search_intent(self, interpretation: Dict, context: Dict) -> Dict:
        """Execute search operation."""
        entities = interpretation.get('entities', {})
        search_terms = entities.get('target_columns', []) + entities.get('source_columns', [])

        # Search in context
        current_mappings = context.get('current_mappings', [])

        # Filter mappings
        results = []
        for mapping in current_mappings:
            target_col = mapping.get('TargetColumn', '').lower()
            source_col = mapping.get('SourceColumn', '').lower()

            for term in search_terms:
                if term.lower() in target_col or term.lower() in source_col:
                    results.append(mapping)
                    break

        return {
            "success": True,
            "action": "display_results",
            "results": results,
            "message": f"Found {len(results)} mapping(s) matching your search."
        }

    def _execute_update_intent(self, interpretation: Dict, context: Dict) -> Dict:
        """Execute update operation."""
        entities = interpretation.get('entities', {})

        return {
            "success": True,
            "action": "update_mappings",
            "parameters": entities,
            "message": "I'll update the mappings based on your requirements.",
            "requires_confirmation": True
        }

    def _execute_validation_intent(self, interpretation: Dict, context: Dict) -> Dict:
        """Execute validation operation."""
        return {
            "success": True,
            "action": "validate_mappings",
            "message": "Running validation on current mappings...",
            "requires_confirmation": False
        }

    def generate_response(self, execution_result: Dict) -> str:
        """Generate human-friendly response from execution result."""
        if not execution_result.get('success'):
            return f"❌ {execution_result.get('message', 'Operation failed')}"

        message = execution_result.get('message', '')

        if execution_result.get('requires_confirmation'):
            message += "\n\nShall I proceed? [Yes/No]"

        if 'suggestions' in execution_result:
            suggestions = execution_result['suggestions']
            message += "\n\n**Suggestions:**\n" + "\n".join([f"• {s}" for s in suggestions])

        return message


if __name__ == "__main__":
    print("Natural Language Mapping Interface loaded successfully")

    # Test interpretation
    test_queries = [
        "Map all customer email fields from CRM to customer dimension",
        "Show me columns with low quality scores",
        "All email addresses should be lowercase and validated",
        "Find unmapped columns",
        "Validate current mappings"
    ]

    print("\nTest Queries:")
    for query in test_queries:
        print(f"  - {query}")

    print("\nNL Interface ready!")
