"""Security validator for AI-generated Python code.

Validates code before execution to prevent malicious operations like:
- Arbitrary code execution (eval, exec, __import__)
- File I/O operations (open, file)
- System commands (os.system, subprocess)
- Network operations
- Imports from disallowed modules

Uses multi-layer defense:
1. Pattern-based regex checks
2. AST (Abstract Syntax Tree) parsing
3. Import whitelist validation
"""

import ast
import re
from typing import Tuple


# Allowed modules for data profiling and visualization
ALLOWED_IMPORTS = {
    'pandas', 'pd',
    'numpy', 'np',
    'matplotlib', 'plt',
    're',
    'json',
    'math',
    'statistics',
    'collections',
    'itertools',
    'datetime',
    'decimal'
}

# Dangerous patterns that should never appear in generated code
DANGEROUS_PATTERNS = [
    r'\beval\s*\(',                    # eval() execution
    r'\bexec\s*\(',                    # exec() execution
    r'\b__import__\s*\(',              # Dynamic imports
    r'\bopen\s*\(',                    # File operations
    r'\bfile\s*\(',                    # File object creation
    r'os\.system',                     # System commands
    r'os\.popen',                      # Process pipes
    r'os\.exec',                       # Process execution
    r'subprocess\.',                   # Subprocess module
    r'socket\.',                       # Network operations
    r'urllib\.',                       # URL operations
    r'requests\.',                     # HTTP requests
    r'http\.',                         # HTTP operations
    r'ftplib\.',                       # FTP operations
    r'pickle\.',                       # Pickle serialization (arbitrary code exec)
    r'marshal\.',                      # Marshal serialization
    r'__builtins__',                   # Access to built-ins
    r'input\s*\(',                     # User input
    r'raw_input\s*\(',                 # Raw user input (Python 2)
]


def validate_code(code: str) -> Tuple[bool, str]:
    """
    Validates AI-generated code for security issues.

    Args:
        code: Python code string to validate

    Returns:
        Tuple of (is_safe: bool, error_message: str)
        - (True, "") if code is safe
        - (False, "error description") if code is unsafe
    """

    if not code or not isinstance(code, str):
        return False, "Code is empty or not a string"

    # Strip any markdown code blocks if present
    code = code.strip()
    if code.startswith('```python'):
        code = code[9:]
    if code.startswith('```'):
        code = code[3:]
    if code.endswith('```'):
        code = code[:-3]
    code = code.strip()

    # Layer 1: Pattern-based security checks
    for pattern in DANGEROUS_PATTERNS:
        match = re.search(pattern, code, re.IGNORECASE)
        if match:
            return False, f"Dangerous pattern detected: {match.group()}"

    # Layer 2: AST parsing and validation
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    except Exception as e:
        return False, f"Failed to parse code: {e}"

    # Layer 3: Import validation
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_base = alias.name.split('.')[0]
                if module_base not in ALLOWED_IMPORTS:
                    return False, f"Disallowed import: {alias.name}"

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_base = node.module.split('.')[0]
                if module_base not in ALLOWED_IMPORTS:
                    return False, f"Disallowed import from: {node.module}"

        # Check for dangerous function calls
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                dangerous_funcs = {'eval', 'exec', '__import__', 'compile', 'input',
                                 'open', 'file', 'globals', 'locals', 'vars', 'dir'}
                if func_name in dangerous_funcs:
                    return False, f"Disallowed function call: {func_name}()"

    # All checks passed
    return True, ""


def sanitize_code_output(code: str, max_length: int = 50000) -> str:
    """
    Sanitize code for display purposes.

    Args:
        code: Python code string
        max_length: Maximum length to display

    Returns:
        Sanitized code string
    """
    if not code:
        return ""

    # Truncate if too long
    if len(code) > max_length:
        code = code[:max_length] + "\n\n... (truncated)"

    return code


def get_safe_namespace():
    """
    Returns a safe namespace dictionary for code execution.

    Includes only allowed modules and safe built-in functions.
    """
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt
    import re
    import json
    import math
    import statistics
    from collections import Counter, defaultdict
    from datetime import datetime, timedelta
    from decimal import Decimal

    # Create a restricted __import__ function that only allows safe modules
    def safe_import(name, *args, **kwargs):
        """Restricted import that only allows whitelisted modules."""
        module_base = name.split('.')[0]
        if module_base in ALLOWED_IMPORTS:
            # __builtins__ can be a module or dict depending on exec context
            if isinstance(__builtins__, dict):
                import_func = __builtins__['__import__']
            else:
                import_func = __builtins__.__import__
            return import_func(name, *args, **kwargs)
        else:
            raise ImportError(f"Import of '{name}' is not allowed for security reasons")

    # Safe built-in functions only
    safe_builtins = {
        'abs': abs,
        'all': all,
        'any': any,
        'bool': bool,
        'dict': dict,
        'enumerate': enumerate,
        'filter': filter,
        'float': float,
        'int': int,
        'len': len,
        'list': list,
        'map': map,
        'max': max,
        'min': min,
        'range': range,
        'reversed': reversed,
        'round': round,
        'set': set,
        'sorted': sorted,
        'str': str,
        'sum': sum,
        'tuple': tuple,
        'zip': zip,
        'True': True,
        'False': False,
        'None': None,
        '__import__': safe_import,  # Add restricted import capability
        'type': type,
        'isinstance': isinstance,
        'hasattr': hasattr,
        'getattr': getattr,
        'setattr': setattr,
    }

    return {
        '__builtins__': safe_builtins,
        'pd': pd,
        'pandas': pd,
        'np': np,
        'numpy': np,
        'plt': plt,
        'matplotlib': plt,
        're': re,
        'json': json,
        'math': math,
        'statistics': statistics,
        'Counter': Counter,
        'defaultdict': defaultdict,
        'datetime': datetime,
        'timedelta': timedelta,
        'Decimal': Decimal,
    }


if __name__ == "__main__":
    # Test cases
    test_cases = [
        # Safe code examples
        ("import pandas as pd\ndf = pd.DataFrame()", True),
        ("import numpy as np\nresults = np.mean([1,2,3])", True),
        ("import matplotlib.pyplot as plt\nplt.hist([1,2,3])", True),

        # Unsafe code examples
        ("eval('1+1')", False),
        ("exec('import os')", False),
        ("__import__('os')", False),
        ("open('file.txt', 'w')", False),
        ("import os\nos.system('ls')", False),
        ("import subprocess\nsubprocess.call(['ls'])", False),
    ]

    print("Running code validator tests...")
    for code, expected_safe in test_cases:
        is_safe, error = validate_code(code)
        status = "✅" if is_safe == expected_safe else "❌"
        print(f"{status} Expected safe={expected_safe}, Got safe={is_safe}: {code[:50]}")
        if not is_safe:
            print(f"   Error: {error}")
    print("\nValidation complete!")
