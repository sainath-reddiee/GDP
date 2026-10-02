# Deployment Guide: Data Modeler Agent

This guide provides end-to-end steps to launch and deploy the Data Modeler Agent Streamlit application.

## 1. Streamlit in Snowflake (SiS) - [RECOMMENDED]

Snowflake can host Streamlit applications natively. This is the most secure and easiest way to deploy this app.

### Advantages:
- **No Servers**: No need for Azure DevOps, Docker, or external containers.
- **Integrated Security**: Uses Snowflake's native Role Based Access Control (RBAC).
- **Direct Data Access**: No need to manage credentials in `.env` files.
- **Native Cortex AI**: AI_COMPLETE and AI_EMBED functions are available without external API calls.

### Prerequisites:
- Snowflake account with **Cortex AI** enabled
- Warehouse with Cortex User role permissions
- The `environment.yml` file (already included in the project)

### Stage Structure:
Upload the `src/` directory contents to your stage with this structure:
```
@STREAMLIT_STAGE/
  streamlit_app.py           <-- copied from src/ui/streamlit_app.py (main entry)
  environment.yml            <-- from project root
  extract/
    profile_real_data.py
    metadata_extractor.py
    fetch_snowflake_ddls.py
  ai/
    bronze_silver_mapper.py
    iterative_mapper.py
    semantic_clustering.py
    silver_model_generator.py
    generate_silver_snowflake_ddl.py
    data_quality_companion.py
    code_validator.py
    mapping_validator.py
    business_rules_engine.py
    approval_manager.py
    nl_mapping_interface.py
    refinement_engine.py
    learning_engine.py
    pattern_library.py
  profiling/
    generate_long_descriptions.py
  validation/
    bronze_to_silver_metrics.py
    compute_extended_metrics.py
    compare_silver_ddls.py
  utils/
    __init__.py
    connection.py
      stage_exporter.py

### Export Stage Configuration
- Set the environment variable `EXPORT_STAGE_NAME` (defaults to `YOUR_DB.YOUR_SCHEMA.YOUR_EXPORT_STAGE`) for Snowsight deployments.
- Grant `PUT` and `GET` permissions on that stage/object to the Streamlit execution role.
- The app uploads every CSV/JSON/DOCX export into `@EXPORT_STAGE_NAME/<session_folder>/` and surfaces a presigned link using `GET_PRESIGNED_URL`.
```

### Steps to Deploy:

1. **Create Stage**:
   ```sql
   USE DATABASE YOUR_DATABASE;
   CREATE SCHEMA IF NOT EXISTS STREAMLIT_APPS;
   USE SCHEMA STREAMLIT_APPS;

   CREATE STAGE IF NOT EXISTS STREAMLIT_STAGE
     DIRECTORY = (ENABLE = TRUE);
   ```

2. **Upload Files** (via Snowsight UI or PUT commands):
   ```sql
   -- Upload main app (note: copy streamlit_app.py to stage root)
   PUT file://src/ui/streamlit_app.py @STREAMLIT_STAGE/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   PUT file://environment.yml @STREAMLIT_STAGE/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

   -- Upload module directories
   PUT file://src/extract/*.py @STREAMLIT_STAGE/extract/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   PUT file://src/ai/*.py @STREAMLIT_STAGE/ai/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   PUT file://src/profiling/*.py @STREAMLIT_STAGE/profiling/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   PUT file://src/validation/*.py @STREAMLIT_STAGE/validation/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   PUT file://src/utils/*.py @STREAMLIT_STAGE/utils/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   ```

3. **Create Streamlit App**:
   ```sql
   CREATE STREAMLIT IF NOT EXISTS DATA_MODELER_AGENT
     ROOT_LOCATION = '@YOUR_DATABASE.STREAMLIT_APPS.STREAMLIT_STAGE'
     MAIN_FILE = 'streamlit_app.py'
     QUERY_WAREHOUSE = 'YOUR_WAREHOUSE';
   ```

4. **Grant Access**:
   ```sql
   GRANT USAGE ON STREAMLIT DATA_MODELER_AGENT TO ROLE YOUR_APP_ROLE;
   ```

5. **Verify**: Open the app in Snowsight and confirm:
   - Sidebar shows "Connected natively to Snowflake"
   - Table listing works
   - Profiling completes successfully
   - AI descriptions generate (AI_COMPLETE)
   - Semantic clustering works (AI_EMBED)

### Git Integration:
If you have Snowflake Git integration configured:
```sql
-- Create git repository integration
CREATE GIT REPOSITORY IF NOT EXISTS DATA_MODELER_REPO
  API_INTEGRATION = 'YOUR_GIT_INTEGRATION'
  ORIGIN = 'https://your-git-provider.com/your-org/your-project/_git/your-repo';

-- Fetch latest
ALTER GIT REPOSITORY DATA_MODELER_REPO FETCH;

-- Deploy from git to stage
COPY FILES INTO @STREAMLIT_STAGE
  FROM @DATA_MODELER_REPO/branches/main/ai_silver_modeler/src/;
```

---

## 2. Local Launch (Native Streamlit)

### Prerequisites
- Python 3.9 or higher.
- A Snowflake account with Cortex AI permissions.

### Steps
1. **Clone/Download the repository**.
2. **Setup virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure Environment Variables**:
   - Create/Update `src/.env` with your Snowflake credentials:
     ```env
     SNOWFLAKE_ACCOUNT="your_account"
     SNOWFLAKE_USER="your_user"
     SNOWFLAKE_PASSWORD="your_password"
     SNOWFLAKE_DATABASE="your_db"
     SNOWFLAKE_SCHEMA="your_schema"
     SNOWFLAKE_WAREHOUSE="your_wh"
     SNOWFLAKE_ROLE="your_role"
     ```
5. **Run the App**:
   ```bash
   streamlit run src/ui/streamlit_app.py
   ```

---

## 3. Azure DevOps Integration

Since your code is in Azure DevOps, you can use the following options:

### Option A: Azure App Service (Recommended)
You can deploy your Streamlit app directly to Azure App Service as a Web App.
1. **Azure Pipeline**: Create a `azure-pipelines.yml` to build and deploy your code.
2. **Startup Command**: Set the startup command in Azure App Service to:
   ```bash
   python -m streamlit run src/ui/streamlit_app.py --server.port 8080 --server.address 0.0.0.0
   ```

### Option B: Docker Container
1. **Dockerfile**: Create a Dockerfile in the root:
   ```dockerfile
   FROM python:3.9-slim
   WORKDIR /app
   COPY . .
   RUN pip install -r requirements.txt
   EXPOSE 8501
   CMD ["streamlit", "run", "src/ui/streamlit_app.py"]
   ```
2. **Azure Container Registry**: Push your image and deploy to Azure Container Instances or App Service for Containers.

---

## 4. Streamlit Community Cloud

**Can I connect same repo to Streamlit?**
Yes, but Streamlit Community Cloud currently supports **GitHub** directly.

### Workaround for Azure DevOps:
1. **Mirror to GitHub**: Create a private GitHub repository and mirror your Azure DevOps repo to it.
2. **Connect GitHub to Streamlit**: Log into [share.streamlit.io](https://share.streamlit.io) and select your GitHub repo.
3. **Secrets Management**: Instead of a `.env` file, use the "Secrets" section in Streamlit Cloud to add your Snowflake credentials.

---

## Verification
After launching, navigate to the URL provided (default: `http://localhost:8501`).
- Test connectivity using the sidebar button.
- Verify that table profiling and AI mapping flows work as expected.

## SiS-Specific Notes
- **No `.env` file needed**: Connection is handled natively via `get_active_session()`
- **Read-only filesystem**: All file writes use `/tmp` (ephemeral) or `st.session_state` (in-memory)
- **Packages**: Only Anaconda-channel packages are available (see `environment.yml`)
- **No `python-dotenv`**: All modules gracefully handle its absence
- **Business rules & approvals**: Changes are session-scoped in SiS (not persisted to disk)
