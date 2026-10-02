import json
import csv
from pathlib import Path
from typing import Optional, List, Dict
from http import HTTPStatus
from io import StringIO
import traceback

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Response
from pydantic import BaseModel
from app.schemas.response_models import StandardResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, Float, Sequence
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from app.config import settings

Base = declarative_base()

# Define Snowflake sequences
projects_seq = Sequence('PROJECTS_SEQ')
mapping_rows_seq = Sequence('MAPPING_ROWS_SEQ')
dbt_files_seq = Sequence('DBT_FILES_SEQ')
macros_seq = Sequence('MACROS_SEQ')

# Table 1: Projects
class Project(Base):
    __tablename__ = 'PROJECTS'
    
    id = Column('ID', Integer, projects_seq, primary_key=True, server_default=projects_seq.next_value())
    project_name = Column('PROJECT_NAME', String(255), nullable=False)
    description = Column('DESCRIPTION', Text)
    
    dbt_status = Column('DBT_STATUS', String(50), default="NOT_GENERATED")  # NOT_GENERATED, GENERATING, COMPLETED, FAILED
    
    # Relationships
    mapping_rows = relationship("MappingRow", back_populates="project", cascade="all, delete-orphan")
    dbt_files = relationship("DbtFile", back_populates="project", cascade="all, delete-orphan")

# Table 2: CSV Mapping Rows
class MappingRow(Base):
    __tablename__ = 'MAPPING_ROWS'
    
    id = Column('ID', Integer, mapping_rows_seq, primary_key=True, server_default=mapping_rows_seq.next_value())
    project_id = Column('PROJECT_ID', Integer, ForeignKey('PROJECTS.ID'), nullable=False)
    source_schema = Column('SOURCE_SCHEMA', String(255), nullable=False)
    target_schema = Column('TARGET_SCHEMA', String(255), nullable=False)
    source_table_name = Column('SOURCE_TABLE_NAME', String(255), nullable=False)
    target_table_name = Column('TARGET_TABLE_NAME', String(255), nullable=False)
    source_column = Column('SOURCE_COLUMN', String(255), nullable=False)
    target_column = Column('TARGET_COLUMN', String(255), nullable=False)
    source_description = Column('SOURCE_DESCRIPTION', Text)
    target_description = Column('TARGET_DESCRIPTION', Text)
    source_data_type = Column('SOURCE_DATA_TYPE', String(100), nullable=False)
    target_data_type = Column('TARGET_DATA_TYPE', String(100), nullable=False)
    mapping_similarity = Column('MAPPING_SIMILARITY', Float)
    transformation_logic = Column('TRANSFORMATION_LOGIC', Text, nullable=False, default="")
    cleaning_logic = Column('CLEANING_LOGIC', Text, nullable=False, default="")
    merge_strategy = Column('MERGE_STRATEGY', String(100), nullable=False, default="UNION")
    macros = Column('MACROS', Text, nullable=False, default="")
    
    # Relationship to project
    project = relationship("Project", back_populates="mapping_rows")

class DbtFile(Base):
    __tablename__ = 'DBT_FILES'
    
    id = Column('ID', Integer, dbt_files_seq, primary_key=True, server_default=dbt_files_seq.next_value())
    project_id = Column('PROJECT_ID', Integer, ForeignKey('PROJECTS.ID'), nullable=False)
    file_path = Column('FILE_PATH', String(500), nullable=False)  # e.g., "models/stg_silver/stg_silver_users.sql"
    file_content = Column('FILE_CONTENT', Text, nullable=False)
    
    # Relationship to project
    project = relationship("Project", back_populates="dbt_files")

class MacroLibrary(Base):
    __tablename__ = 'MACRO_LIBRARY'
    
    id = Column('ID', Integer, macros_seq, primary_key=True, server_default=macros_seq.next_value())
    name = Column('NAME', String(255), nullable=False, unique=True)
    description = Column('DESCRIPTION', Text)
    sql_content = Column('SQL_CONTENT', Text, nullable=False)

class TransformationRequest(BaseModel):
    prompt: str

class BulkSuggestionRequest(BaseModel):
    mapping_row_ids: List[int]

class SuggestionResult(BaseModel):
    id: int
    cleaning_logic: str
    macros: str
    transformation_logic: str
    confidence: float
    reasoning: str

class RowUpdate(BaseModel):
    id: int
    cleaning_logic: Optional[str] = None
    macros: Optional[str] = None
    transformation_logic: Optional[str] = None

class BulkRowUpdateRequest(BaseModel):
    updates: List[RowUpdate]

class MacroCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sql_content: str

class MacroUpdate(BaseModel):
    description: Optional[str] = None
    sql_content: Optional[str] = None

class MacroResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    sql_content: str

    class Config:
        from_attributes = True

router = APIRouter()

# Create Snowflake engine with support for both password and private key auth
from app.utils.snowflake_connection import get_snowflake_engine

engine = get_snowflake_engine()
SessionLocal = sessionmaker(bind=engine)


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get(
    "/csv/sample",
    summary="Download sample CSV template",
    status_code=HTTPStatus.OK
)
def download_sample_csv():
    """Download a sample CSV file with the required column structure and sample data"""
    # Sample data embedded in code
    sample_data = [
        {
            'sourceSchema': 'bronze_crm',
            'targetSchema': 'silver_crm',
            'sourcetableName': 'customers',
            'sourceColumn': 'id',
            'SourceDescription': 'Unique customer ID - A system-generated unique identifier assigned to each customer in the database.',
            'targettableName': 'customers',
            'targetColumn': 'customer_id',
            'sourcedataType': 'INTEGER',
            'targetdataType': 'INTEGER',
            'TargetDescription': 'A distinct identifier assigned to each customer record, used to uniquely recognize and connect customer details.',
            'mappingSimilarity': '95',
            'cleaningLogic': 'TRIM',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': ''
        },
        {
            'sourceSchema': 'bronze_crm',
            'targetSchema': 'silver_crm',
            'sourcetableName': 'customers',
            'sourceColumn': 'name',
            'SourceDescription': 'Customer name - The full name of the customer, used for personalization and communication.',
            'targettableName': 'customers',
            'targetColumn': 'customer_name',
            'sourcedataType': 'VARCHAR',
            'targetdataType': 'VARCHAR',
            'TargetDescription': 'The complete name provided by the customer during registration or profile setup.',
            'mappingSimilarity': '95',
            'cleaningLogic': 'UPPER',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': ''
        },
        {
            'sourceSchema': 'bronze_crm',
            'targetSchema': 'silver_crm',
            'sourcetableName': 'customers',
            'sourceColumn': 'email',
            'SourceDescription': 'Customer email address - Used for login credentials, notifications, and marketing communications.',
            'targettableName': 'customers',
            'targetColumn': 'email_address',
            'sourcedataType': 'VARCHAR',
            'targetdataType': 'VARCHAR',
            'TargetDescription': 'The official email address linked with the customer account.',
            'mappingSimilarity': '95',
            'cleaningLogic': 'LOWER',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': '{{ validate_email(email_address) }}'
        },
        {
            'sourceSchema': 'bronze_crm',
            'targetSchema': 'silver_crm',
            'sourcetableName': 'customers',
            'sourceColumn': 'created',
            'SourceDescription': 'Account creation date - The date and time when the customer account was first registered.',
            'targettableName': 'customers',
            'targetColumn': 'created_date',
            'sourcedataType': 'TIMESTAMP',
            'targetdataType': 'TIMESTAMP',
            'TargetDescription': 'The timestamp representing when the customer was first added to the system.',
            'mappingSimilarity': '95',
            'cleaningLogic': 'TO_DATE',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': ''
        },
        {
            'sourceSchema': 'bronze_sales',
            'targetSchema': 'silver_sales',
            'sourcetableName': 'orders',
            'sourceColumn': 'order_id',
            'SourceDescription': 'Unique order ID - A system-generated unique identifier for each order.',
            'targettableName': 'orders',
            'targetColumn': 'order_id',
            'sourcedataType': 'INTEGER',
            'targetdataType': 'INTEGER',
            'TargetDescription': 'A unique reference number automatically generated for each order.',
            'mappingSimilarity': '95',
            'cleaningLogic': '',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': ''
        },
        {
            'sourceSchema': 'bronze_sales',
            'targetSchema': 'silver_sales',
            'sourcetableName': 'orders',
            'sourceColumn': 'cust_id',
            'SourceDescription': 'Linked customer ID - A foreign key referencing the customer unique ID.',
            'targettableName': 'orders',
            'targetColumn': 'customer_id',
            'sourcedataType': 'INTEGER',
            'targetdataType': 'INTEGER',
            'TargetDescription': 'A foreign key linking the order to the corresponding customer.',
            'mappingSimilarity': '95',
            'cleaningLogic': '',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': ''
        },
        {
            'sourceSchema': 'bronze_sales',
            'targetSchema': 'silver_sales',
            'sourcetableName': 'orders',
            'sourceColumn': 'total',
            'SourceDescription': 'Total order amount - The total monetary value of the order.',
            'targettableName': 'orders',
            'targetColumn': 'order_total',
            'sourcedataType': 'DECIMAL',
            'targetdataType': 'DECIMAL',
            'TargetDescription': 'The final billed amount for the order including all costs.',
            'mappingSimilarity': '80',
            'cleaningLogic': 'ABS',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': 'order_total * 1.05'
        },
        {
            'sourceSchema': 'bronze_sales',
            'targetSchema': 'silver_sales',
            'sourcetableName': 'products',
            'sourceColumn': 'prod_id',
            'SourceDescription': 'Unique product ID - A system-generated identifier assigned to each product.',
            'targettableName': 'products',
            'targetColumn': 'product_id',
            'sourcedataType': 'INTEGER',
            'targetdataType': 'INTEGER',
            'TargetDescription': 'A system-generated identifier assigned to every product.',
            'mappingSimilarity': '90',
            'cleaningLogic': '',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': ''
        },
        {
            'sourceSchema': 'bronze_sales',
            'targetSchema': 'silver_sales',
            'sourcetableName': 'products',
            'sourceColumn': 'prod_name',
            'SourceDescription': 'Product name - The official name of the product as listed in the catalog.',
            'targettableName': 'products',
            'targetColumn': 'product_name',
            'sourcedataType': 'VARCHAR',
            'targetdataType': 'VARCHAR',
            'TargetDescription': 'The official title used to describe the product in listings.',
            'mappingSimilarity': '90',
            'cleaningLogic': 'TRIM',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': ''
        },
        {
            'sourceSchema': 'bronze_sales',
            'targetSchema': 'silver_sales',
            'sourcetableName': 'products',
            'sourceColumn': 'price',
            'SourceDescription': 'Product price - The selling price of the product in the store default currency.',
            'targettableName': 'products',
            'targetColumn': 'unit_price',
            'sourcedataType': 'DECIMAL',
            'targetdataType': 'DECIMAL',
            'TargetDescription': 'The cost of a single unit of the product.',
            'mappingSimilarity': '85',
            'cleaningLogic': 'ROUND(price, 2)',
            'mergeStrategy': 'UNION',
            'macros': '',
            'TransformationLogic': '{{ calculate_margin(unit_price) }}'
        }
    ]
    
    # Define column order
    fieldnames = [
        'sourceSchema', 'targetSchema', 'sourcetableName', 'sourceColumn',
        'SourceDescription', 'targettableName', 'targetColumn',
        'sourcedataType', 'targetdataType', 'TargetDescription', 'mappingSimilarity',
        'cleaningLogic', 'mergeStrategy', 'macros', 'TransformationLogic'
    ]
    
    # Generate CSV content
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(sample_data)
    
    csv_content = output.getvalue()
    output.close()
    
    return Response(
        content=csv_content.encode('utf-8'),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=sample_mapping_template.csv"
        }
    )


@router.post(
    "/csv/upload",
    response_model=StandardResponse[dict],
    summary="Upload CSV file and store in PostgreSQL",
    status_code=HTTPStatus.CREATED
)
async def upload_csv(
    project_name: str = Form(...),
    description: Optional[str] = Form(None),
    csv_file: UploadFile = File(...)
):
    """
    Upload a CSV file and store its contents in PostgreSQL database.
    
    CSV should have columns: sourceSchema, targetSchema, sourcetableName, sourceColumn, 
    sourceShortdesc, sourceLongdesc, targettableName, targetColumn, sourcedataType, 
    targetdataType, targetDesc, mappingSimilarity
    
    - **project_name**: Name of the project (required)
    - **description**: Optional description of the project
    - **csv_file**: CSV file to upload and parse
    """
    # Validate file extension
    if not csv_file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="File must be a CSV file"
        )
    
    try:
        # Read CSV content
        contents = await csv_file.read()
        
        # Try multiple encodings to handle different file formats
        encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252', 'iso-8859-1']
        csv_str = None
        
        for encoding in encodings:
            try:
                csv_str = contents.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if csv_str is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Unable to decode file. Please ensure the file is encoded in UTF-8, Latin-1, or Windows-1252."
            )
        
        # Parse CSV - use StringIO to properly handle multi-line quoted fields
        from io import StringIO
        csv_file_io = StringIO(csv_str)
        reader = csv.DictReader(csv_file_io)
        rows = list(reader)
        
        if not rows:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="CSV file is empty"
            )
        
        # Validate CSV columns
        required_columns = {
            'sourceSchema', 'targetSchema', 'sourcetableName', 'sourceColumn',
            'SourceDescription', 'targettableName', 'targetColumn',
            'sourcedataType', 'targetdataType', 'TargetDescription', 'mappingSimilarity'
        }
        # Note: cleaningLogic, mergeStrategy, macros, and TransformationLogic are FULLY OPTIONAL
        # They can be omitted entirely or left empty - system will use smart defaults
        if not required_columns.issubset(set(rows[0].keys())):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"CSV must contain columns: {', '.join(sorted(required_columns))}"
            )
        
        # Get database session
        db = next(get_db())
        
        try:
            # Check if project already exists
            existing_project = db.query(Project).filter(Project.project_name == project_name).first()
            if existing_project:
                raise HTTPException(
                    status_code=HTTPStatus.CONFLICT,
                    detail=f"Project with name '{project_name}' already exists"
                )
            
            # Create project entry
            project = Project(project_name=project_name, description=description)
            db.add(project)
            db.commit()  # Commit to generate the ID
            db.refresh(project)  # Refresh to get the generated ID
            
            # Detect multiple source-to-target mappings for validation warnings
            target_tracking = {}
            multi_source_warnings = []
            
            for row in rows:
                # Skip rows without source columns (unmapped target columns)
                if not row.get('sourceColumn', '').strip() or not row.get('sourcetableName', '').strip():
                    continue
                
                target_key = f"{row['targetSchema']}.{row['targettableName']}.{row['targetColumn']}"
                source_info = {
                    'source': f"{row['sourcetableName']}.{row['sourceColumn']}",
                    'merge_strategy': row.get('mergeStrategy', '').strip() or 'AUTO',
                    'similarity': row.get('mappingSimilarity', '')
                }
                
                if target_key not in target_tracking:
                    target_tracking[target_key] = []
                target_tracking[target_key].append(source_info)
            
            # Check for multiple sources and generate warnings
            for target_key, sources in target_tracking.items():
                if len(sources) > 1:
                    merge_strategies = set(s['merge_strategy'] for s in sources)
                    multi_source_warnings.append({
                        "target": target_key,
                        "sources": [s['source'] for s in sources],
                        "count": len(sources),
                        "merge_strategies": list(merge_strategies),
                        "info": "Auto-detected multi-source mapping. Will use COALESCE to select first non-null value."
                    })
            
            # Insert mapping rows
            inserted_count = 0
            unmapped_columns_count = 0
            for row in rows:
                # Skip rows with no target column defined
                if not row.get('targetColumn', '').strip():
                    continue
                
                # Parse mapping_similarity, default to None if empty or invalid
                mapping_similarity = None
                if row.get('mappingSimilarity'):
                    try:
                        mapping_similarity = float(row['mappingSimilarity'])
                    except (ValueError, TypeError):
                        mapping_similarity = None
                
                # Normalize empty strings to actual empty strings (not whitespace)
                source_table = (row.get('sourcetableName') or '').strip()
                source_column = (row.get('sourceColumn') or '').strip()
                source_data_type = (row.get('sourcedataType') or '').strip()
                target_table = (row.get('targettableName') or '').strip()
                target_column = (row.get('targetColumn') or '').strip()
                target_data_type = (row.get('targetdataType') or '').strip()
                
                # Track unmapped target columns (no source)
                if not source_column:
                    unmapped_columns_count += 1
                
                mapping_row = MappingRow(
                    project_id=project.id,
                    source_schema=row['sourceSchema'],
                    target_schema=row['targetSchema'],
                    source_table_name=source_table,
                    target_table_name=target_table,
                    source_column=source_column,
                    target_column=target_column,
                    source_description=row.get('SourceDescription', ''),
                    target_description=row.get('TargetDescription', ''),
                    source_data_type=source_data_type,
                    target_data_type=target_data_type,
                    mapping_similarity=mapping_similarity,
                    cleaning_logic=row.get('cleaningLogic', '').strip() if row.get('cleaningLogic') else '',
                    merge_strategy=row.get('mergeStrategy', '').strip() if row.get('mergeStrategy') else '',
                    macros=row.get('macros', '').strip() if row.get('macros') else '',
                    transformation_logic=row.get('TransformationLogic', '').strip() if row.get('TransformationLogic') else ''
                )
                db.add(mapping_row)
                inserted_count += 1
            
            db.commit()
            
            # Build response with info if applicable
            response_payload = {
                "message": f"Successfully uploaded {inserted_count} mapping rows",
                "project_id": project.id,
                "project_name": project_name,
                "rows_count": inserted_count
            }
            
            info_messages = []
            
            if multi_source_warnings:
                response_payload["multi_source_info"] = {
                    "mappings": multi_source_warnings,
                    "count": len(multi_source_warnings)
                }
                info_messages.append(f"{len(multi_source_warnings)} target column(s) with multiple source mappings detected")
            
            if unmapped_columns_count > 0:
                response_payload["unmapped_columns_info"] = {
                    "count": unmapped_columns_count,
                    "note": "These target columns have no source mapping and will be populated as NULL in generated DBT models"
                }
                info_messages.append(f"{unmapped_columns_count} unmapped target column(s) will be populated as NULL")
            
            if info_messages:
                response_payload["info_summary"] = " | ".join(info_messages)
            
            return StandardResponse(
                success=True,
                payload=response_payload
            )
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            import traceback
            error_details = traceback.format_exc()
            print(f"ERROR in CSV upload: {str(e)}")
            print(f"Full traceback:\n{error_details}")
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail=f"Error storing data: {str(e)}"
            )
        finally:
            db.close()
            
    except csv.Error as e:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Invalid CSV format: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Error processing file: {str(e)}"
        )


@router.get(
    "/csv/projects",
    response_model=StandardResponse[list],
    summary="Get all projects",
    status_code=HTTPStatus.OK
)
def get_all_projects():
    """Get list of all projects"""
    db = next(get_db())
    try:
        projects = db.query(Project).all()
        projects_list = [
            {
                "id": p.id,
                "project_name": p.project_name,
                "description": p.description,
                "status": "completed" if (p.dbt_status or "").upper() == "COMPLETED" else "draft"
            }
            for p in projects
        ]
        return StandardResponse(success=True, payload=projects_list)
    finally:
        db.close()


@router.get(
    "/csv/projects/{project_id}",
    response_model=StandardResponse[dict],
    summary="Get project data by project ID",
    status_code=HTTPStatus.OK
)
def get_project_data(project_id: int):
    """Get project and all its mapping rows grouped by source table name"""
    db = next(get_db())
    try:
        # Check if project exists
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )
        
        # Get all mapping rows for this project
        mapping_rows = db.query(MappingRow).filter(
            MappingRow.project_id == project_id
        ).all()
        
        grouped_rows = {}

        for row in mapping_rows:
            payload_row = {
                "id": row.id,
                "source_schema": row.source_schema,
                "target_schema": row.target_schema,
                "source_table_name": row.source_table_name,
                "target_table_name": row.target_table_name,
                "source_column": row.source_column,
                "target_column": row.target_column,
                "source_description": row.source_description,
                "target_description": row.target_description,
                "source_data_type": row.source_data_type,
                "target_data_type": row.target_data_type,
                "mapping_similarity": row.mapping_similarity,
                "transformation_logic": row.transformation_logic,
                "cleaning_logic": row.cleaning_logic,
                "merge_strategy": row.merge_strategy,
                "macros": row.macros,
            }

            # Only add rows that have a target table; skip others entirely, or
            # group them under a fallback key if you want to track them.
            if row.target_table_name:
                grouped_rows.setdefault(row.target_table_name, []).append(payload_row)
            # else: skip (do nothing)

        unique_source_schemas = set(row.source_schema for row in mapping_rows)
        source_schema_count = len(unique_source_schemas)
        project_status = (project.dbt_status or "NOT_GENERATED").upper()
        
        return StandardResponse(
            success=True,
            payload={
                "id": project.id,
                "project_name": project.project_name,
                "description": project.description,
                "status": project_status,
                "mapping_rows": grouped_rows,
                "rows_count": len(mapping_rows),
                "source_tables_count": len(grouped_rows),
                "source_schema_count": source_schema_count,
            }
        )
    finally:
        db.close()


@router.delete(
    "/csv/projects/{project_id}",
    response_model=StandardResponse[dict],
    summary="Delete a project and all its mapping rows",
    status_code=HTTPStatus.OK
)
def delete_project(project_id: int):
    """Delete a project and all its associated mapping rows"""
    db = next(get_db())
    try:
        # Check if project exists
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"Project with ID {project_id} not found"
            )
        
        # Delete project (cascade will delete mapping rows)
        db.delete(project)
        db.commit()
        
        return StandardResponse(
            success=True,
            payload={"message": f"Project '{project.project_name}' deleted successfully"}
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Error deleting project: {str(e)}"
        )
    finally:
        db.close()


@router.post("/csv/projects/{project_id}/dbt/transform/{mapping_row_id}", response_model=StandardResponse[dict])
def transform_mapping_row(project_id: int, mapping_row_id: int, request: TransformationRequest):
    db = next(get_db())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Project not found")

        # Get the specific mapping row
        row = (
            db.query(MappingRow)
            .filter(MappingRow.project_id == project_id, MappingRow.id == mapping_row_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Mapping row not found")

        # Get ALL mapping rows for this project to provide full context
        all_mapping_rows = (
            db.query(MappingRow)
            .filter(MappingRow.project_id == project_id)
            .all()
        )

        # Build comprehensive project context
        project_context = {
            "project_id": project.id,
            "project_name": project.project_name,
            "description": project.description,
            "total_mappings": len(all_mapping_rows),
            "source_tables": {},
            "target_tables": {},
            "current_mapping": {
                "id": row.id,
                "source_schema": row.source_schema,
                "source_table": row.source_table_name,
                "source_column": row.source_column,
                "source_data_type": row.source_data_type,
                "source_description": row.source_description or "",
                "target_schema": row.target_schema,
                "target_table": row.target_table_name,
                "target_column": row.target_column,
                "target_data_type": row.target_data_type,
                "target_description": row.target_description or "",
                "mapping_similarity": row.mapping_similarity,
                "transformation_logic": row.transformation_logic or ""
            }
        }

        # Group by source tables
        for mapping_row in all_mapping_rows:
            if mapping_row.source_schema and mapping_row.source_table_name:
                source_key = f"{mapping_row.source_schema}.{mapping_row.source_table_name}"
                if source_key not in project_context["source_tables"]:
                    project_context["source_tables"][source_key] = {
                        "schema": mapping_row.source_schema,
                        "table": mapping_row.source_table_name,
                        "columns": []
                    }
                
                # Add column info
                col_info = {
                    "column_name": mapping_row.source_column,
                    "data_type": mapping_row.source_data_type,
                    "description": mapping_row.source_description or ""
                }
                if col_info not in project_context["source_tables"][source_key]["columns"]:
                    project_context["source_tables"][source_key]["columns"].append(col_info)

        # Group by target tables
        for mapping_row in all_mapping_rows:
            if mapping_row.target_schema and mapping_row.target_table_name:
                target_key = f"{mapping_row.target_schema}.{mapping_row.target_table_name}"
                if target_key not in project_context["target_tables"]:
                    project_context["target_tables"][target_key] = {
                        "schema": mapping_row.target_schema,
                        "table": mapping_row.target_table_name,
                        "columns": []
                    }
                
                # Add column info
                col_info = {
                    "column_name": mapping_row.target_column,
                    "data_type": mapping_row.target_data_type,
                    "description": mapping_row.target_description or "",
                    "mapped_from": {
                        "source_schema": mapping_row.source_schema,
                        "source_table": mapping_row.source_table_name,
                        "source_column": mapping_row.source_column
                    } if mapping_row.source_column else None
                }
                if col_info not in project_context["target_tables"][target_key]["columns"]:
                    project_context["target_tables"][target_key]["columns"].append(col_info)

        # GET MACROS from library
        macros = db.query(MacroLibrary).all()
        macro_defs = {m.name: m.description for m in macros}

        system_prompt = f"""You are an expert dbt (data build tool) SQL transformation assistant. 
Your task is to generate SQL transformation expressions based on source column specifications.

AVAILABLE CUSTOM MACROS (Use these if applicable to the user request):
{json.dumps(macro_defs, indent=2)}

CRITICAL REQUIREMENTS:
1. You will receive a complete project context including all source and target tables and their columns
2. You will receive a specific mapping row that needs transformation
3. You MUST write the transformation SQL using the SOURCE column name, not the target column name
4. If the source column is not present (empty/null), you can reference other columns from the project context
5. The transformation should be written as a SQL expression that can be used in a SELECT statement
6. Return ONLY the SQL expression - no SELECT, FROM, WHERE, or other clauses
7. No markdown code fences, no explanations - just the SQL expression
8. Use proper SQL syntax compatible with Snowflake/dbt
9. Consider the source column's data type when writing transformations
10. The expression should transform the source column value (or referenced columns) to produce the desired result for the target column
11. You can reference any column from the source_tables or target_tables in the project context if needed"""

        user_prompt = f"""PROJECT CONTEXT:
{json.dumps(project_context, indent=2)}

CURRENT MAPPING TO TRANSFORM:
- Source Schema: {row.source_schema}
- Source Table: {row.source_table_name}
- Source Column: {row.source_column or 'NOT PROVIDED'}
- Source Data Type: {row.source_data_type or 'N/A'}
- Source Description: {row.source_description or 'No description'}
- Target Schema: {row.target_schema}
- Target Table: {row.target_table_name}
- Target Column: {row.target_column}
- Target Data Type: {row.target_data_type}
- Target Description: {row.target_description or 'No description'}
- Current Transformation Logic: {row.transformation_logic or 'None'}

USER REQUEST:
{request.prompt}

INSTRUCTIONS:
1. Write the SQL transformation expression using the SOURCE column name: {row.source_column or 'N/A'}
2. If source column is NOT PROVIDED or empty:
   - You can reference other columns from the project context (check source_tables and target_tables structures)
   - Look for relevant columns that might be used for this transformation
   - The transformation_logic field may already contain a reference to another column (e.g., "CAST(SOP_LOCAL AS NUMBER(38,2))")
   - If transformation_logic exists, you can use it as a starting point or reference
3. The expression should be written as if it appears in a SELECT statement like: SELECT <your_expression> AS {row.target_column}
4. Consider the source data type ({row.source_data_type or 'N/A'}) and target data type ({row.target_data_type})
5. Apply any necessary type casting or conversions
6. Return ONLY the SQL expression - no SELECT, FROM, or other SQL keywords
7. Example formats:
8. Use the actual column name '{row.source_column or 'columns from context'}' directly in your expression. Do NOT use generic placeholders like 'source_column'.
9. If you must reference other columns from the PROJECT CONTEXT, use their exact names as listed.
10. You have access to all columns in the project context - check source_tables and target_tables for available columns

Generate the SQL transformation expression now:"""

        # Combine system and user prompts for Cortex
        full_prompt = f"""{system_prompt}

{user_prompt}"""
        
        # Call Snowflake Cortex
        from app.utils.cortex_client import cortex_complete
        sql = cortex_complete(full_prompt).strip()
        
        # Clean up any markdown code fences if present
        sql = sql.replace("```sql", "").replace("```", "").strip()

        return StandardResponse(success=True, payload={
            "mapping_row_id": mapping_row_id,
            "source_column": row.source_column,
            "target_column": row.target_column,
            "source_table": row.source_table_name,
            "target_table": row.target_table_name,
            "sql": sql,
            "project_context_summary": {
                "total_mappings": project_context["total_mappings"],
                "source_tables_count": len(project_context["source_tables"]),
                "target_tables_count": len(project_context["target_tables"])
            }
        })
    finally:
        db.close()


class TransformationLogicUpdateRequest(BaseModel):
    transformation_logic: str

@router.put("/csv/projects/{project_id}/mapping_rows/{mapping_row_id}/transformation", response_model=StandardResponse[dict])
def update_transformation_logic(project_id: int, mapping_row_id: int, payload: TransformationLogicUpdateRequest):
    db = next(get_db())
    try:
        row = (
            db.query(MappingRow)
            .filter(MappingRow.project_id == project_id, MappingRow.id == mapping_row_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Mapping row not found")

        row.transformation_logic = payload.transformation_logic or ""
        db.commit()
        db.refresh(row)

        return StandardResponse(success=True, payload={"mapping_row_id": row.id, "transformation_logic": row.transformation_logic})
    finally:
        db.close()

# --- Macro Library Endpoints ---

@router.get("/macros", response_model=StandardResponse[list[MacroResponse]])
def get_macros():
    db = next(get_db())
    try:
        macros = db.query(MacroLibrary).all()
        return StandardResponse(success=True, payload=macros)
    finally:
        db.close()

@router.post("/macros", response_model=StandardResponse[MacroResponse])
def create_macro(macro: MacroCreate):
    db = next(get_db())
    try:
        db_macro = db.query(MacroLibrary).filter(MacroLibrary.name == macro.name).first()
        if db_macro:
            # Update existing if name matches
            db_macro.description = macro.description
            db_macro.sql_content = macro.sql_content
        else:
            db_macro = MacroLibrary(
                name=macro.name,
                description=macro.description,
                sql_content=macro.sql_content
            )
            db.add(db_macro)
        
        db.commit()
        db.refresh(db_macro)
        return StandardResponse(success=True, payload=db_macro)
    finally:
        db.close()

from app.utils.cortex_client import cortex_complete
import traceback

@router.post("/macros/ai-generate", response_model=StandardResponse[dict])
# Trigger reload
def ai_generate_macro(request: TransformationRequest):
    """Generate a dbt macro SQL based on a business description"""
    
    system_prompt = """You are an expert dbt and Snowflake SQL engineer.
Your task is to generate a reusable dbt macro based on the user's business requirements.

CRITICAL REQUIREMENTS:
1. Return a JSON object with THREE keys: 
   - "name": the suggested macro name (snake_case)
   - "description": a concise explanation of what the macro does (1-2 sentences)
   - "sql": the complete jinja macro source
2. The macro should be compatible with Snowflake.
3. Use proper dbt macro syntax: {% macro name(col, ...) %} ... {% endmacro %}.
4. Return ONLY the JSON object. No markdown, no explanations.
"""

    full_prompt = f"{system_prompt}\n\nUser Request: Create a dbt macro for: {request.prompt}"

    try:
        # Call Cortex
        content = cortex_complete(full_prompt)
        
        # Clean response (remove markdown code blocks if any)
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        # Remove invalid control characters (newlines inside strings can cause issues if not escaped)
        import re
        content = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', content)
        
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Fallback: try to manually extract keys if JSON is malformed
            name_match = re.search(r'"name":\s*"([^"]+)"', content)
            desc_match = re.search(r'"description":\s*"([^"]+)"', content)
            sql_match = re.search(r'"sql":\s*"(.*?)"', content, re.DOTALL)
            
            if name_match and sql_match:
                result = {
                    "name": name_match.group(1),
                    "description": desc_match.group(1) if desc_match else "",
                    "sql": sql_match.group(1).replace("\\n", "\n")
                }
            else:
               raise ValueError("Failed to parse AI response as JSON")

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = {
                    "name": "generated_macro",
                    "description": "AI returned raw text; wrapped to satisfy response schema.",
                    "sql": result
                }

        return StandardResponse(success=True, payload=result)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e))
@router.post("/transformations/ai-generate", response_model=StandardResponse[dict])
# Trigger reload 2
def ai_generate_transformation(request: TransformationRequest):
    """Generate a valid Snowflake SQL transformation rule (snippet)"""
    
    system_prompt = """You are an expert Snowflake SQL engineer.
Your task is to generate a SINGLE line SQL expression (snippet) to transform a column.

CRITICAL GUIDELINES:
1. Identify the column name being transformed from the User Request. 
2. If the prompt says "For column 'XYZ': ...", you MUST use 'XYZ' in the SQL.
3. If no specific column name is provided, you may use 'source_column' as a generic placeholder.
4. Return ONLY the SQL expression. Do not write full SELECT statements.
5. Example: "For column 'email': convert to lower" -> LOWER(email)
6. Example: "For column 'id': mask value" -> REGEXP_REPLACE(id, '(?<=.{2}).(?=.*@)', '*')
7. Do NOT use markdown. Do NOT use explanations.
8. Return a JSON object with one key: "sql".
"""

    full_prompt = f"{system_prompt}\n\nUser Request: {request.prompt}"

    try:
        content = cortex_complete(full_prompt)
        
        # Clean response
        content = content.replace("```json", "").replace("```", "").strip()
        import re
        content = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', content)

        try:
            result = json.loads(content)
            # Some models return a JSON string ("UPPER(col)"). Wrap it so the
            # response model always receives a dictionary payload.
            if isinstance(result, str):
                result = {"sql": result}
        except json.JSONDecodeError:
             # Fallback if AI just returned the SQL string directly
             result = {"sql": content}

        return StandardResponse(success=True, payload=result)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/csv/projects/{project_id}/suggest-bulk", response_model=StandardResponse[List[SuggestionResult]])
def suggest_bulk_transformations(project_id: int, request: BulkSuggestionRequest):
    """Analyze multiple mapping rows and suggest transformations using AI"""
    db = SessionLocal()
    try:
        # 1. Fetch mapping rows
        rows = db.query(MappingRow).filter(
            MappingRow.project_id == project_id,
            MappingRow.id.in_(request.mapping_row_ids)
        ).all()
        
        if not rows:
            return StandardResponse(success=True, payload=[])

        # 2. Fetch Macro Library for context
        macros = db.query(MacroLibrary).all()
        macro_context = "\n".join([f"- {m.name}: {m.description}" for m in macros])

        # 3. Build Prompt context
        rows_data = []
        for r in rows:
            rows_data.append({
                "id": r.id,
                "source_col": r.source_column,
                "target_col": r.target_column,
                "source_desc": r.source_description or "",
                "target_desc": r.target_description or "",
                "source_type": r.source_data_type,
                "target_type": r.target_data_type
            })

        system_prompt = f"""You are an expert data build tool (dbt) engineer. 
Your task is to analyze source-to-target column mappings and suggest the best transformations.

AVAILABLE MACROS (ONLY use these names if suggesting a macro):
{macro_context}

TRANSFORMATION COMPONENTS TO SUGGEST:
1. cleaning_logic: Standard SQL functions like TRIM, UPPER, LOWER, COALESCE(col, 'N/A'), NULLIF(col, ''). Use these for basic standardization.
2. macros: One or more macro names from the list above (comma separated).
3. transformation_logic: More complex SQL logic like CASE statements, type casts, or string manipulations that go beyond basic cleaning.

OUTPUT REQUIREMENTS:
- Return a JSON array of objects.
- Each object MUST have: "id", "cleaning_logic", "macros", "transformation_logic", "confidence" (0-1), and "reasoning".
- Suggest "cleaning_logic" for almost everything (TRIM is highly recommended).
- Only suggest "macros" if they clearly fit the column's purpose (e.g., email_mask for email columns).
- Return ONLY the raw JSON array. Start with [ and end with ]. No markdown fences.
"""

        user_prompt = f"Suggest best-practice transformations for these mappings:\n{json.dumps(rows_data, indent=2)}"
        
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        
        # 4. Call Snowflake Cortex (using default model from settings)
        from app.utils.cortex_client import cortex_complete
        response = cortex_complete(full_prompt).strip()
        
        # 5. Clean and Parse JSON
        content = response.replace("```json", "").replace("```", "").strip()
        import re
        content = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', content)
        
        try:
            suggestions = json.loads(content)
            if not isinstance(suggestions, list):
                suggestions = [suggestions]

            normalized_suggestions = []
            for idx, suggestion in enumerate(suggestions):
                if isinstance(suggestion, str):
                    normalized_suggestions.append({
                        "id": rows_data[idx]["id"] if idx < len(rows_data) else -1,
                        "cleaning_logic": "",
                        "macros": "",
                        "transformation_logic": suggestion,
                        "confidence": 0.5,
                        "reasoning": "AI returned raw SQL string; wrapped for response validation."
                    })
                elif isinstance(suggestion, dict):
                    normalized_suggestions.append(suggestion)
                else:
                    continue

            payload = []
            for s in normalized_suggestions:
                payload.append(SuggestionResult(
                    id=s.get("id"),
                    cleaning_logic=s.get("cleaning_logic", ""),
                    macros=s.get("macros", ""),
                    transformation_logic=s.get("transformation_logic", ""),
                    confidence=s.get("confidence", 0.5),
                    reasoning=s.get("reasoning", "Suggested by AI based on column metadata.")
                ))
            
            return StandardResponse(success=True, payload=payload)
            
        except Exception as e:
            traceback.print_exc()
            # If JSON parsing fails, return empty logic but don't crash
            return StandardResponse(success=False, payload=[], message=f"AI response parsing failed: {str(e)}")

    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        db.close()

@router.post("/csv/projects/{project_id}/bulk-update", response_model=StandardResponse[dict])
def bulk_update_mapping_rows(project_id: int, request: BulkRowUpdateRequest):
    """Update multiple mapping rows in a single transaction"""
    db = SessionLocal()
    try:
        updated_count = 0
        for update in request.updates:
            row = db.query(MappingRow).filter(
                MappingRow.project_id == project_id,
                MappingRow.id == update.id
            ).first()
            
            if row:
                if update.cleaning_logic is not None:
                    row.cleaning_logic = update.cleaning_logic
                if update.macros is not None:
                    row.macros = update.macros
                if update.transformation_logic is not None:
                    row.transformation_logic = update.transformation_logic
                updated_count += 1
        
        db.commit()
        return StandardResponse(success=True, payload={"updated_count": updated_count})
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        db.close()

@router.delete("/macros/{macro_id}", response_model=StandardResponse[dict])
def delete_macro(macro_id: int):
    db = next(get_db())
    try:
        macro = db.query(MacroLibrary).filter(MacroLibrary.id == macro_id).first()
        if not macro:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Macro not found")
        
        db.delete(macro)
        db.commit()
        return StandardResponse(success=True, payload={"message": "Macro deleted", "macro_id": macro_id})
    finally:
        db.close()
