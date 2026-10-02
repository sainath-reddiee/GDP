export interface CSVMappingRow {
  sourceSchema: string;
  targetSchema: string;
  tableName: string;
  sourceColumn: string;
  targetColumn: string;
  dataType: string;
  sourceDescription: string;
  similarityScore: string;
  cleaningLogic: string;
  mergeStrategy: string;
  macros: string;
  transformationLogic: string;
}

export interface ColumnMapping {
  id: number;
  source: string;
  targetSchema: string;
  target: string;
  sourceTable: string;
  targetTable: string;
  type: string;
  targetType: string;
  sourceSchema: string;
  sourceDescription: string;
  targetDescription: string;
  similarityScore: number;
  transformationLogic: string;
  cleaningLogic: string;
  mergeStrategy: string;
  macros: string;
}

export interface TableMapping {
  id: string;
  sourceSchema: string;
  targetSchema: string;
  targetTable: string;
  tableName: string;
  columns: ColumnMapping[];
}

export interface Project {
  projectId: string;
  projectName: string;
  description: string;
  createdDate: string;
  status: 'Draft' | 'Code Generated' | 'Completed';
  csvFileName?: string;
  mappingData: TableMapping[];
  generatedCode: string | null;
}

export interface DBTCode {
  id?: number;
  fileName: string;
  content: string;
  modelName: string;
  filePath: string;
}
