export type DatabaseRow = { database_name: string; type: string; comment: string | null };
export type SchemaRow = { schema_name: string };
export type TableRow = { table_name: string; table_type: string; row_count: number | null; comment: string | null };
export type TargetRow = {
  target_table_id: string; domain_name: string; target_database: string; target_schema: string;
  target_table: string; table_type: string | null; grain: string | null; columns: number;
};
