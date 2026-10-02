import { TableMapping, DBTCode } from '../types';

export const generateBronzeSilverDBT = (mapping: TableMapping): DBTCode => {
  const modelName = `${mapping.targetSchema}_${mapping.tableName}`;
  const fileName = `${modelName}.sql`;

  const columnMappings = mapping.columns
    .map(col => {
      if (col.source === col.target) {
        return `    ${col.source}`;
      }
      return `    ${col.source} as ${col.target}`;
    })
    .join(',\n');

  const hasTimestamp = mapping.columns.some(col =>
    col.type.includes('TIMESTAMP') && (col.target.includes('updated') || col.target.includes('modified'))
  );

  const incrementalKey = mapping.columns.find(col =>
    col.target.includes('_id') || col.target === 'id'
  )?.target || mapping.columns[0].target;

  const updateColumn = mapping.columns.find(col =>
    col.target.includes('updated') || col.target.includes('modified')
  )?.target;

  const content = `-- models/${mapping.targetSchema.replace('silver_', '')}/${fileName}
{{
  config(
    materialized='${hasTimestamp ? 'incremental' : 'table'}',
    unique_key='${incrementalKey}'${hasTimestamp ? ',\n    on_schema_change=\'fail\'' : ''}
  )
}}

SELECT
${columnMappings}
FROM {{ source('${mapping.sourceSchema}', '${mapping.tableName}') }}
${hasTimestamp && updateColumn ? `{% if is_incremental() %}
WHERE ${updateColumn} > (SELECT MAX(${updateColumn}) FROM {{ this }})
{% endif %}` : ''}`;

  return {
    fileName,
    content,
    modelName,
  };
};

export const generateSchemaYML = (codes: DBTCode[]): string => {
  const models = codes.map(code => `  - name: ${code.modelName}
    description: "Auto-generated silver layer model for ${code.modelName}"
    columns:
      - name: id
        description: "Primary key"
        tests:
          - unique
          - not_null`).join('\n');

  return `version: 2

models:
${models}`;
};

export const downloadFile = (content: string, fileName: string) => {
  const blob = new Blob([content], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

export const downloadAllFiles = (codes: DBTCode[], layer: 'silver') => {
  const timestamp = new Date().toISOString().split('T')[0];
  const schemaContent = generateSchemaYML(codes);

  codes.forEach(code => {
    downloadFile(code.content, code.fileName);
  });

  downloadFile(schemaContent, `schema_${layer}_${timestamp}.yml`);

  const readmeContent = `# DBT Models - ${layer.toUpperCase()} Layer
Generated on: ${new Date().toISOString()}

## Files Included
${codes.map(code => `- ${code.fileName}`).join('\n')}
- schema_${layer}_${timestamp}.yml

## Usage
1. Copy all .sql files to your dbt project's models/${layer}/ directory
2. Copy the schema.yml file to the same directory
3. Run \`dbt run --models ${layer}.*\`

## Notes
- Review and test all generated code before deployment
- Adjust materialization strategies as needed for your use case
- Ensure source configurations are properly set up
`;

  downloadFile(readmeContent, `README_${layer}_${timestamp}.md`);
};
