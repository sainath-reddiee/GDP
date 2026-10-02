// LocalStorage-based implementation to replace Supabase
// This maintains the same API interface for compatibility

const STORAGE_KEYS = {
  PROJECTS: 'dbt_projects',
  TABLE_MAPPINGS: 'dbt_table_mappings',
  COLUMN_MAPPINGS: 'dbt_column_mappings',
};

// Helper functions for localStorage
function getFromStorage<T>(key: string): T[] {
  try {
    const data = localStorage.getItem(key);
    return data ? JSON.parse(data) : [];
  } catch (error) {
    console.error(`Error reading from localStorage (${key}):`, error);
    return [];
  }
}

function saveToStorage<T>(key: string, data: T[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(data));
  } catch (error) {
    console.error(`Error saving to localStorage (${key}):`, error);
    throw error;
  }
}

function generateId(): string {
  return `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
}

export interface ProjectRow {
  id: string;
  name: string;
  description: string;
  status: 'Draft' | 'Code Generated' | 'Completed';
  csv_file_name: string;
  generated_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface TableMappingRow {
  id: string;
  project_id: string;
  source_schema: string;
  target_schema: string;
  table_name: string;
  created_at: string;
}

export interface ColumnMappingRow {
  id: string;
  table_mapping_id: string;
  source_column: string;
  target_column: string;
  data_type: string;
  source_system: string;
  short_description: string;
  long_description: string;
  similarity_score?: number;
  created_at: string;
}

export const projectsApi = {
  async getAll() {
    const projects = getFromStorage<ProjectRow>(STORAGE_KEYS.PROJECTS);
    return projects.sort((a, b) => 
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
  },

  async getById(id: string) {
    const projects = getFromStorage<ProjectRow>(STORAGE_KEYS.PROJECTS);
    return projects.find(p => p.id === id) || null;
  },

  async create(project: Omit<ProjectRow, 'id' | 'created_at' | 'updated_at'>) {
    const projects = getFromStorage<ProjectRow>(STORAGE_KEYS.PROJECTS);
    const now = new Date().toISOString();
    const newProject: ProjectRow = {
      ...project,
      id: generateId(),
      created_at: now,
      updated_at: now,
    };
    projects.push(newProject);
    saveToStorage(STORAGE_KEYS.PROJECTS, projects);
    return newProject;
  },

  async update(id: string, project: Partial<ProjectRow>) {
    const projects = getFromStorage<ProjectRow>(STORAGE_KEYS.PROJECTS);
    const index = projects.findIndex(p => p.id === id);
    if (index === -1) {
      throw new Error(`Project with id ${id} not found`);
    }
    projects[index] = {
      ...projects[index],
      ...project,
      updated_at: new Date().toISOString(),
    };
    saveToStorage(STORAGE_KEYS.PROJECTS, projects);
    return projects[index];
  },

  async delete(id: string) {
    const projects = getFromStorage<ProjectRow>(STORAGE_KEYS.PROJECTS);
    const filtered = projects.filter(p => p.id !== id);
    saveToStorage(STORAGE_KEYS.PROJECTS, filtered);
    
    // Also delete related table and column mappings
    const tableMappings = getFromStorage<TableMappingRow>(STORAGE_KEYS.TABLE_MAPPINGS);
    const relatedTableMappings = tableMappings.filter(tm => tm.project_id === id);
    const tableMappingIds = relatedTableMappings.map(tm => tm.id);
    
    // Delete table mappings
    const filteredTableMappings = tableMappings.filter(tm => tm.project_id !== id);
    saveToStorage(STORAGE_KEYS.TABLE_MAPPINGS, filteredTableMappings);
    
    // Delete column mappings
    const columnMappings = getFromStorage<ColumnMappingRow>(STORAGE_KEYS.COLUMN_MAPPINGS);
    const filteredColumnMappings = columnMappings.filter(
      cm => !tableMappingIds.includes(cm.table_mapping_id)
    );
    saveToStorage(STORAGE_KEYS.COLUMN_MAPPINGS, filteredColumnMappings);
  },
};

export const tableMappingsApi = {
  async getByProjectId(projectId: string) {
    const tableMappings = getFromStorage<TableMappingRow>(STORAGE_KEYS.TABLE_MAPPINGS);
    return tableMappings.filter(tm => tm.project_id === projectId);
  },

  async createBatch(mappings: Omit<TableMappingRow, 'id' | 'created_at'>[]) {
    const tableMappings = getFromStorage<TableMappingRow>(STORAGE_KEYS.TABLE_MAPPINGS);
    const now = new Date().toISOString();
    const newMappings: TableMappingRow[] = mappings.map(m => ({
      ...m,
      id: generateId(),
      created_at: now,
    }));
    tableMappings.push(...newMappings);
    saveToStorage(STORAGE_KEYS.TABLE_MAPPINGS, tableMappings);
    return newMappings;
  },

  async deleteByProjectId(projectId: string) {
    const tableMappings = getFromStorage<TableMappingRow>(STORAGE_KEYS.TABLE_MAPPINGS);
    const relatedMappings = tableMappings.filter(tm => tm.project_id === projectId);
    const tableMappingIds = relatedMappings.map(tm => tm.id);
    
    const filtered = tableMappings.filter(tm => tm.project_id !== projectId);
    saveToStorage(STORAGE_KEYS.TABLE_MAPPINGS, filtered);
    
    // Also delete related column mappings
    const columnMappings = getFromStorage<ColumnMappingRow>(STORAGE_KEYS.COLUMN_MAPPINGS);
    const filteredColumnMappings = columnMappings.filter(
      cm => !tableMappingIds.includes(cm.table_mapping_id)
    );
    saveToStorage(STORAGE_KEYS.COLUMN_MAPPINGS, filteredColumnMappings);
  },
};

export const columnMappingsApi = {
  async getByTableMappingId(tableMappingId: string) {
    const columnMappings = getFromStorage<ColumnMappingRow>(STORAGE_KEYS.COLUMN_MAPPINGS);
    return columnMappings.filter(cm => cm.table_mapping_id === tableMappingId);
  },

  async createBatch(mappings: Omit<ColumnMappingRow, 'id' | 'created_at'>[]) {
    const columnMappings = getFromStorage<ColumnMappingRow>(STORAGE_KEYS.COLUMN_MAPPINGS);
    const now = new Date().toISOString();
    const newMappings: ColumnMappingRow[] = mappings.map(m => ({
      ...m,
      id: generateId(),
      created_at: now,
    }));
    columnMappings.push(...newMappings);
    saveToStorage(STORAGE_KEYS.COLUMN_MAPPINGS, columnMappings);
    return newMappings;
  },

  async deleteByTableMappingId(tableMappingId: string) {
    const columnMappings = getFromStorage<ColumnMappingRow>(STORAGE_KEYS.COLUMN_MAPPINGS);
    const filtered = columnMappings.filter(cm => cm.table_mapping_id !== tableMappingId);
    saveToStorage(STORAGE_KEYS.COLUMN_MAPPINGS, filtered);
  },
};

export async function createProjectWithMappings(
  projectData: Omit<ProjectRow, 'id' | 'created_at' | 'updated_at'>,
  tableMappings: Array<{
    sourceSchema: string;
    targetSchema: string;
    tableName: string;
    columns: Array<{
      sourceColumn: string;
      targetColumn: string;
      dataType: string;
      sourceSystem?: string;
      shortDescription?: string;
      longDescription?: string;
      similarityScore?: number;
    }>;
  }>
) {
  try {
    console.log('Creating project with data:', projectData);
    const project = await projectsApi.create(projectData);
    console.log('Project created:', project.id);

    const tableMappingRows = tableMappings.map(tm => ({
      project_id: project.id,
      source_schema: tm.sourceSchema,
      target_schema: tm.targetSchema,
      table_name: tm.tableName,
    }));

    console.log('Creating table mappings:', tableMappingRows.length);
    const createdTableMappings = await tableMappingsApi.createBatch(tableMappingRows);
    console.log('Table mappings created:', createdTableMappings.length);

    const columnMappingRows = createdTableMappings.flatMap((tm, idx) =>
      tableMappings[idx].columns.map(col => ({
        table_mapping_id: tm.id,
        source_column: col.sourceColumn,
        target_column: col.targetColumn,
        data_type: col.dataType,
        source_system: col.sourceSystem || '',
        short_description: col.shortDescription || '',
        long_description: col.longDescription || '',
        similarity_score: col.similarityScore,
      }))
    );

    console.log('Creating column mappings:', columnMappingRows.length);
    console.log('Sample column mapping:', columnMappingRows[0]);

    if (columnMappingRows.length > 0) {
      await columnMappingsApi.createBatch(columnMappingRows);
      console.log('Column mappings created successfully');
    }

    return project;
  } catch (error) {
    console.error('Error in createProjectWithMappings:', error);
    throw error;
  }
}

export async function getProjectWithMappings(projectId: string) {
  const project = await projectsApi.getById(projectId);
  // if (!project) return null;

  const tableMappings = await tableMappingsApi.getByProjectId(projectId);

  const mappingsWithColumns = await Promise.all(
    tableMappings.map(async (tm) => {
      const columns = await columnMappingsApi.getByTableMappingId(tm.id);
      return {
        id: tm.id,
        sourceSchema: tm.source_schema,
        targetSchema: tm.target_schema,
        tableName: tm.table_name,
        columns: columns.map(c => ({
          id: parseInt(c.id) || 0,
          source: c.source_column,
          target: c.target_column,
          sourceTable: tm.table_name,
          targetTable: tm.table_name,
          type: c.data_type,
          shortDescription: c.short_description || '',
          longDescription: c.long_description || '',
          similarityScore: c.similarity_score || 100,
          transformationRule: '',
        })),
      };
    })
  );

  return {
    projectId: project.id,
    projectName: project.name,
    description: project.description,
    createdDate: project.created_at.split('T')[0],
    status: project.status,
    csvFileName: project.csv_file_name,
    mappingData: mappingsWithColumns,
    generatedCode: project.generated_code,
  };
}
