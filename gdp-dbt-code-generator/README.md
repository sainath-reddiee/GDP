# DBT Code Generator

AI-powered dbt (data build tool) project generator that converts source-to-target mapping specifications into production-ready dbt code using Snowflake Cortex AI.

## Overview

Upload a CSV mapping file describing your source and target schemas, and the application generates a complete dbt project including:

- **Staging models** (Bronze layer) -- one per source table with type casting and standardization
- **Silver models** (Cleaned layer) -- with transformation logic, cleaning rules, and multi-source merging
- **Project configuration** -- `dbt_project.yml`, `profiles.yml`, `packages.yml`
- **Source and schema definitions** -- complete YAML documentation
- **Custom macros** -- from a reusable macro library stored in Snowflake

## Architecture

| Component | Technology |
|-----------|-----------|
| Frontend | React 18, TypeScript, Vite |
| Backend | FastAPI, Python |
| Database | Snowflake |
| AI Engine | Snowflake Cortex (configurable model) |
| Deployment | Docker, nginx reverse proxy |

## Features

- CSV-based source-to-target mapping upload and management
- AI-powered dbt SQL generation with Snowflake Cortex
- Support for multi-source column merging (COALESCE, CONCAT, UNION)
- Transformation and cleaning logic from mapping metadata
- AI-assisted file editing for generated dbt code
- Downloadable dbt project as ZIP archive
- Reusable macro library management
- Key-pair and password authentication for Snowflake

## Prerequisites

- Snowflake account with Cortex AI access
- Docker and Docker Compose
- OpenSSL (for key-pair authentication setup)

## Quick Start

1. **Set up Snowflake** -- Run `snowflake_complete_setup.sql` in your Snowflake account
2. **Configure environment** -- Copy `.env.example` to `.env` and fill in your Snowflake credentials
3. **Deploy** -- `docker compose up --build -d`
4. **Access** -- Open `http://localhost` in your browser

For detailed instructions, see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) or [QUICK_START.md](QUICK_START.md).

## Project Structure

```
.
├── frontend/              # React + TypeScript + Vite frontend
│   └── src/
│       ├── components/    # UI components
│       ├── pages/         # Application pages
│       └── utils/         # Storage and API utilities
├── backend/
│   └── app/
│       ├── config/        # Pydantic settings
│       ├── routers/       # FastAPI route handlers
│       ├── schemas/       # Response models
│       └── utils/         # Snowflake connection, Cortex client, logging
├── snowflake_complete_setup.sql  # One-time Snowflake setup script
├── docker-compose.yml     # Container orchestration
├── Dockerfile             # Multi-stage build (frontend + backend + nginx)
├── .env.example           # Environment variable template
└── sample_mapping.csv     # Example mapping file
```

## Configuration

All configuration is managed through environment variables. See `.env.example` for the complete list.

| Variable | Description |
|----------|-------------|
| `SNOWFLAKE_ACCOUNT` | Snowflake account identifier (e.g., `xy12345.us-east-1`) |
| `SNOWFLAKE_USER` | Service account or user name |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | Path to `.p8` private key (recommended auth) |
| `SNOWFLAKE_PASSWORD` | Password (alternative to key-pair auth) |
| `CORTEX_MODEL` | Snowflake Cortex model name (default: `openai-gpt-5`) |

## License

See LICENSE file for details.
