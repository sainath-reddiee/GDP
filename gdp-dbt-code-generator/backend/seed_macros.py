import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the backend directory
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config.settings import settings
from app.routers.mapping_router import MacroLibrary, Base
from snowflake.sqlalchemy import URL

# Create Snowflake engine
engine = create_engine(
    URL(
        account=settings.SNOWFLAKE_ACCOUNT,
        user=settings.SNOWFLAKE_USER,
        password=settings.SNOWFLAKE_PASSWORD,
        database=settings.SNOWFLAKE_DATABASE,
        schema=settings.SNOWFLAKE_SCHEMA,
        warehouse=settings.SNOWFLAKE_WAREHOUSE,
        role=settings.SNOWFLAKE_ROLE,
    )
)
SessionLocal = sessionmaker(bind=engine)

STANDARD_MACROS = [
    {
        "name": "initcap_trim",
        "description": "Trim whitespace and capitalize the first letter of each word. Perfect for cleaning name or city columns.",
        "sql_content": "{% macro initcap_trim(column_name) %}\n    INITCAP(TRIM({{ column_name }}))\n{% endmacro %}"
    },
    {
        "name": "standardize_phone",
        "description": "Clean phone numbers by removing non-numeric characters. Standardizes various formats to a digit-only string.",
        "sql_content": "{% macro standardize_phone(column_name) %}\n    REGEXP_REPLACE({{ column_name }}, '[^0-9]', '')\n{% endmacro %}"
    },
    {
        "name": "mask_email",
        "description": "Mask email addresses for PII protection. Shows only the first 2 characters and the domain.",
        "sql_content": "{% macro mask_email(column_name) %}\n    CASE \n        WHEN {{ column_name }} LIKE '%@%' THEN\n            CONCAT(\n                LEFT({{ column_name }}, 2), \n                '***', \n                SUBSTR({{ column_name }}, POSITION('@' IN {{ column_name }}))\n            )\n        ELSE '***INVALID_EMAIL***'\n    END\n{% endmacro %}"
    },
    {
        "name": "convert_timezone",
        "description": "Convert a timestamp column to a specific timezone. Defaults to UTC.",
        "sql_content": "{% macro convert_timezone(column_name, target_tz='UTC') %}\n    CONVERT_TIMEZONE('UTC', '{{ target_tz }}', {{ column_name }}::timestamp_ntz)\n{% endmacro %}"
    },
    {
        "name": "clean_boolean",
        "description": "Convert common string markers (Y/N, 1/0, Yes/No) into true/false booleans.",
        "sql_content": "{% macro clean_boolean(column_name) %}\n    CASE \n        WHEN UPPER(TRIM({{ column_name }})) IN ('Y', 'YES', '1', 'TRUE') THEN TRUE\n        WHEN UPPER(TRIM({{ column_name }})) IN ('N', 'NO', '0', 'FALSE') THEN FALSE\n        ELSE NULL\n    END\n{% endmacro %}"
    },
    {
        "name": "safe_divide",
        "description": "Perform division while protecting against division by zero errors.",
        "sql_content": "{% macro safe_divide(numerator, denominator) %}\n    IFF({{ denominator }} = 0, NULL, {{ numerator }} / {{ denominator }})\n{% endmacro %}"
    }
]

def seed_macros():
    # Ensure tables are created
    Base.metadata.create_all(engine)
    
    db = SessionLocal()
    try:
        print(f"Seeding {len(STANDARD_MACROS)} standard macros...")
        for macro_data in STANDARD_MACROS:
            existing = db.query(MacroLibrary).filter(MacroLibrary.name == macro_data["name"]).first()
            if existing:
                print(f"  Updating existing macro: {macro_data['name']}")
                existing.description = macro_data["description"]
                existing.sql_content = macro_data["sql_content"]
            else:
                print(f"  Creating new macro: {macro_data['name']}")
                new_macro = MacroLibrary(**macro_data)
                db.add(new_macro)
        db.commit()
        print("Seeding completed successfully.")
    except Exception as e:
        print(f"Error seeding macros: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_macros()
