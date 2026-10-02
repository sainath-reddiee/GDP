import { Project, TableMapping } from '../types';
import { GetProjectResponse, MappingRow } from '../hooks';

/**
 * Converts backend API response to frontend Project format
 */
export function convertBackendProjectToFrontend(apiResponse: GetProjectResponse['payload']): Project {
  // Convert mapping_rows (grouped by table name) to TableMapping array
  const mappingData: TableMapping[] = Object.entries(apiResponse.mapping_rows).map(([tableName, rows]) => {
    // Type assertion for rows array
    const typedRows = rows as MappingRow[];

    // Use the first row to get schema info (all rows in a table have same schema)
    const firstRow = typedRows[0];

    return {
      id: `${apiResponse.id}_${tableName}`,
      sourceSchema: firstRow.source_schema,
      targetSchema: firstRow.target_schema,
      targetTable: firstRow.target_table_name,
      tableName: firstRow.source_table_name,
      columns: typedRows.map((row: MappingRow) => ({
        id: row.id,
        source: row.source_column,
        target: row.target_column,
        sourceTable: row.source_table_name,
        targetSchema: row.target_schema,
        targetTable: row.target_table_name,
        type: row.source_data_type,
        targetType: row.target_data_type,
        sourceSchema: row.source_schema,
        sourceDescription: row.source_description || '',
        targetDescription: row.target_description || '',
        similarityScore: row.mapping_similarity || 0,
        transformationLogic: row.transformation_logic || '',
        cleaningLogic: row.cleaning_logic || '',
        mergeStrategy: row.merge_strategy || 'UNION',
        macros: row.macros || '',
      })),
    };
  });

  // Map API status values to frontend status values
  let status: 'Draft' | 'Code Generated' | 'Completed' = 'Draft';
  const apiStatus = apiResponse.status?.toUpperCase();
  if (apiStatus === 'COMPLETED') {
    status = 'Completed';
  } else if (apiStatus === 'DRAFT' || apiStatus === 'NOT_GENERATED') {
    status = 'Draft';
  }

  return {
    projectId: apiResponse.id.toString(),
    projectName: apiResponse.project_name,
    description: apiResponse.description || '',
    createdDate: new Date().toISOString().split('T')[0], // API doesn't provide date
    status,
    csvFileName: undefined,
    mappingData,
    generatedCode: null, // API doesn't provide generated code in this endpoint
  };
}
