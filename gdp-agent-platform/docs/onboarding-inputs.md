# What data and material the platform accepts

A reference for questions such as "in what format do we need the client's data to onboard a domain or a source?".
Everything below is what the platform accepts today; sizes are per file.

## Onboarding a domain (the business knowledge)

A domain is the business area being modelled (for example Company, Property, Opportunity). Teams can start from any
of these, in any combination; each adds to the domain's knowledge, and people review what the platform proposes before
it is used.

| What the client has | Accepted formats | Limit | Where in the app |
|---|---|---|---|
| An existing domain definition (pack) | JSON | | Onboard, Import a domain |
| A data contract or model description | YAML, Markdown, text, SQL, CSV, JSON | 200,000 characters | Onboard, Import a domain (AI drafts the domain) |
| Business documents: requirements, glossaries, runbooks, policies | PDF, Word (docx), PowerPoint (pptx), HTML, Excel (xlsx, xlsm), text, Markdown, CSV, TSV, JSON, YAML, XML, SQL, DDL | 10 files at a time, 50 MB each | Domain, Sources tab |
| Scanned pages, diagrams, screenshots | PNG, JPG, TIFF, WEBP, GIF, BMP (read with OCR) | 50 MB | Domain, Sources tab |
| Confluence pages or a space | Link to the page or space | 20 pages per space | Domain, Sources tab (needs the Atlassian connection) |
| Data models | Erwin XML, DDL, data dictionary (xlsx, csv), domain pack JSON | 25 MB | Model Workbench, Import |
| Reference material of any kind | Any file: the platform recognises data models, MDM schemas, STTM sheets, dbt projects (zip or yml), sample rows and documents | 10 files, 50 MB each | Domain, References tab |
| A live Snowflake schema as reference | Database and schema name | | Domain, References tab |
| Examples of correct output | CSV, TSV, text, Excel (xlsx) | 10 MB | Domain, Data model, Examples |
| Existing dbt code | A connected Git repository | | Code (admin) |

Old Office formats (doc, xls, ppt) are stored but not read: ask for the modern format.

## Onboarding a source (the data)

| Source | How it arrives | Formats |
|---|---|---|
| Snowflake database in the same account | Registered directly | Tables and views |
| Snowflake data share | Previewed, mounted and onboarded | Tables and views |
| Files in cloud storage | Amazon S3, Azure Blob, Google Cloud Storage | CSV, Parquet, JSON |
| File upload | Through the app | CSV, TSV, TXT, Parquet, JSON, NDJSON, optionally gzip, up to 200 MB |
| Oracle | Platform extractor (Snowflake external access) | Tables |
| Other databases and applications through SnapLogic | Oracle, SQL Server, PostgreSQL, MySQL, Salesforce, REST APIs, S3, SFTP | Table, CSV, JSON, Parquet, Excel (xlsx) |
| PostgreSQL, SQL Server, MySQL, Salesforce, REST (direct) | Registered for documentation and lineage; data lands through SnapLogic or Oracle extraction | |

## What happens after onboarding

The platform profiles the landed data, proposes mappings to the domain model, writes the source-to-target mapping
(STTM), data quality checks, QA tests and dbt code, and stops at each review step for a person to approve. Nothing is
promoted without an approval.
