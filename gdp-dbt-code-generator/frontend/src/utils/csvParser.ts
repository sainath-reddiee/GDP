import { CSVMappingRow, TableMapping } from '../types';

// Helper function to parse entire CSV handling quoted fields with embedded newlines
const parseCSVRows = (csvContent: string): string[][] => {
  const rows: string[][] = [];
  const normalizedContent = csvContent.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  
  let currentRow: string[] = [];
  let currentValue = '';
  let insideQuotes = false;
  
  for (let i = 0; i < normalizedContent.length; i++) {
    const char = normalizedContent[i];
    
    if (char === '"') {
      // Handle escaped quotes ("")
      if (insideQuotes && normalizedContent[i + 1] === '"') {
        currentValue += '"';
        i++; // Skip next quote
      } else {
        insideQuotes = !insideQuotes;
      }
    } else if (char === ',' && !insideQuotes) {
      // End of field
      currentRow.push(currentValue.trim());
      currentValue = '';
    } else if (char === '\n' && !insideQuotes) {
      // End of row
      currentRow.push(currentValue.trim());
      if (currentRow.some(val => val !== '')) {
        rows.push(currentRow);
      }
      currentRow = [];
      currentValue = '';
    } else {
      // Handle newlines inside quoted fields - replace with space for cleaner data
      if (char === '\n' && insideQuotes) {
        currentValue += ' ';
      } else {
        currentValue += char;
      }
    }
  }
  
  // Push last value and row if any
  if (currentValue || currentRow.length > 0) {
    currentRow.push(currentValue.trim());
    if (currentRow.some(val => val !== '')) {
      rows.push(currentRow);
    }
  }
  
  return rows;
};

export const parseCSV = (csvContent: string): CSVMappingRow[] => {
  const allRows = parseCSVRows(csvContent);

  if (allRows.length < 2) {
    throw new Error('CSV file must contain a header row and at least one data row');
  }

  const headers = allRows[0].map(h => h.trim());

  // Required columns as per backend API specification
  // Note: cleaningLogic, mergeStrategy, macros, and TransformationLogic are optional (will default to empty string)
  const requiredHeaders = [
    'sourceSchema',
    'targetSchema',
    'sourcetableName',
    'sourceColumn',
    'SourceDescription',
    'targettableName',
    'targetColumn',
    'sourcedataType',
    'targetdataType',
    'TargetDescription',
    'mappingSimilarity',
  ];

  const missingHeaders = requiredHeaders.filter(h => !headers.includes(h));
  if (missingHeaders.length > 0) {
    throw new Error(`Missing required columns: ${missingHeaders.join(', ')}. Found columns: ${headers.join(', ')}`);
  }

  const rows: CSVMappingRow[] = [];

  for (let i = 1; i < allRows.length; i++) {
    const values = allRows[i];
    
    // Skip completely empty rows
    if (values.every(v => !v)) continue;

    if (values.length !== headers.length) {
      throw new Error(
        `Row ${i + 1} has ${values.length} columns but expected ${headers.length}. ` +
        `First few values: "${values.slice(0, 3).join('", "')}"`
      );
    }

    const row: any = {};
    headers.forEach((header, index) => {
      row[header] = values[index]?.trim() || '';
    });

    // Only field names (columns) are required, values can be empty

    // Map backend column names to frontend structure
    const mappedRow: CSVMappingRow = {
      sourceSchema: row.sourceSchema,
      targetSchema: row.targetSchema,
      tableName: row.sourcetableName, // Using source table name as primary
      sourceColumn: row.sourceColumn,
      targetColumn: row.targetColumn,
      dataType: row.sourcedataType, // Using source data type
      sourceDescription: row.SourceDescription || '',
      similarityScore: row.mappingSimilarity || '100',
      cleaningLogic: row.cleaningLogic || '',
      mergeStrategy: row.mergeStrategy || '', // Empty = auto-detect (COALESCE for multi-source, UNION for multi-table)
      macros: row.macros || '',
      transformationLogic: row.TransformationLogic || '',
    };

    rows.push(mappedRow);
  }

  return rows;
};

export const convertCSVToTableMappings = (csvRows: CSVMappingRow[]): TableMapping[] => {
  const tableMap = new Map<string, TableMapping>();
  const targetColumnTracking = new Map<string, Array<{ source: string; similarity: number }>>();

  csvRows.forEach((row) => {
    const key = `${row.sourceSchema}_${row.targetSchema}_${row.tableName}`;
    const targetKey = `${row.targetSchema}.${row.tableName}.${row.targetColumn}`;

    // Track sources for this target column
    if (!targetColumnTracking.has(targetKey)) {
      targetColumnTracking.set(targetKey, []);
    }
    targetColumnTracking.get(targetKey)!.push({
      source: `${row.tableName}.${row.sourceColumn}`,
      similarity: parseFloat(row.similarityScore) || 100
    });

    if (!tableMap.has(key)) {
      tableMap.set(key, {
        id: key,
        sourceSchema: row.sourceSchema,
        targetSchema: row.targetSchema,
        targetTable: row.tableName, // Defaulting to same as source
        tableName: row.tableName,
        columns: [],
      });
    }

    const table = tableMap.get(key)!;
    table.columns.push({
      id: 0,
      source: row.sourceColumn,
      target: row.targetColumn,
      sourceTable: row.tableName,
      targetTable: row.tableName,
      targetSchema: row.targetSchema,
      type: row.dataType,
      targetType: row.dataType,
      sourceSchema: row.sourceSchema,
      sourceDescription: row.sourceDescription,
      targetDescription: '', // Will be populated from backend
      similarityScore: parseFloat(row.similarityScore) || 100,
      transformationLogic: row.transformationLogic || '',
      cleaningLogic: row.cleaningLogic || '',
      mergeStrategy: row.mergeStrategy || '', // Empty = auto-detect based on context
      macros: row.macros || '',
    });
  });

  // Log warnings for multiple sources to single target
  let multiSourceCount = 0;
  targetColumnTracking.forEach((sources, targetKey) => {
    if (sources.length > 1) {
      multiSourceCount++;
      // Sort by similarity score for display
      sources.sort((a, b) => b.similarity - a.similarity);
      console.warn(
        `⚠️  Multiple source columns map to ${targetKey}:`,
        sources.map(s => `${s.source} (similarity: ${s.similarity})`),
        '\nAuto-detection: Will use COALESCE to select first non-null value (mergeStrategy can be left empty).'
      );
    }
  });

  if (multiSourceCount > 0) {
    console.info(
      `ℹ️  Detected ${multiSourceCount} target column(s) with multiple source mappings.\n` +
      '   Auto-detection: Will use COALESCE to select the first non-null value.\n' +
      '   Sources are prioritized by similarity score (highest first).\n' +
      '   No need to specify mergeStrategy - it will be handled automatically.'
    );
  }

  return Array.from(tableMap.values());
};

export const validateCSVFile = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    if (!file.name.endsWith('.csv')) {
      reject(new Error('Please upload a CSV file'));
      return;
    }

    if (file.size > 5 * 1024 * 1024) {
      reject(new Error('File size must be less than 5MB'));
      return;
    }

    const reader = new FileReader();

    reader.onload = (e) => {
      const content = e.target?.result as string;
      resolve(content);
    };

    reader.onerror = () => {
      reject(new Error('Failed to read file'));
    };

    reader.readAsText(file);
  });
};

export const generateSampleCSV = (): string => {
  return `sourceSchema,targetSchema,sourcetableName,sourceColumn,SourceDescription,targettableName,targetColumn,sourcedataType,targetdataType,TargetDescription,mappingSimilarity,cleaningLogic,mergeStrategy,macros,TransformationLogic
bronze_crm,silver_crm,customers,id,Unique customer ID - Primary key for customer table,customers,customer_id,INTEGER,INTEGER,A distinct identifier assigned to each customer record,98,TRIM,UNION,,
bronze_crm,silver_crm,customers,name,Customer full name - Full name of the customer,customers,customer_name,VARCHAR,VARCHAR,The complete name provided by the customer,95,UPPER,UNION,,
bronze_crm,silver_crm,customers,email,Customer email address - Email address for customer contact,customers,email_address,VARCHAR,VARCHAR,The official email address linked with the customer account,100,LOWER,UNION,,{{ validate_email(email_address) }}
bronze_crm,silver_crm,customers,created,Account creation date - Date when customer record was created,customers,created_date,TIMESTAMP,TIMESTAMP,The timestamp representing when the customer was first added,92,TO_DATE,UNION,,
bronze_crm,silver_crm,customers,status,Customer Status - Status of the customer account,customers,account_status,VARCHAR,VARCHAR,Current standing of the account,88,,UNION,,CASE WHEN status = 'ACT' THEN 'Active' ELSE 'Inactive' END
bronze_sales,silver_sales,orders,order_id,Unique order ID - Unique identifier for order,orders,order_id,INTEGER,INTEGER,A unique reference number for each order,100,,UNION,,
bronze_sales,silver_sales,orders,cust_id,Linked customer ID - Foreign key referencing the customer,orders,customer_id,INTEGER,INTEGER,A foreign key linking the order to the customer,85,,UNION,,
bronze_sales,silver_sales,orders,total,Total order amount - The total monetary value of the order,orders,order_total,DECIMAL,DECIMAL,The final billed amount for the order,90,ABS,UNION,,order_total * 1.05
bronze_sales,silver_sales,products,prod_id,Unique product ID - System-generated identifier for each product,products,product_id,INTEGER,INTEGER,A system-generated identifier for every product,98,,UNION,,
bronze_sales,silver_sales,products,prod_name,Product name - The official name of the product,products,product_name,VARCHAR,VARCHAR,The official title used to describe the product,88,TRIM,UNION,,
bronze_sales,silver_sales,products,price,Product price - The selling price of the product,products,unit_price,DECIMAL,DECIMAL,The cost of a single unit of the product,95,,UNION,,{{ calculate_margin(unit_price) }}`;
};
