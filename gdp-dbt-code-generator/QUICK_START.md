# Quick Deployment Reference

## Prerequisites Checklist
```
- Ubuntu VM with SSH access
- Snowflake account with admin rights
- Docker installed on VM
- Port 80 accessible on VM
```

## Authentication Setup Quick Commands

### Generate Key Pair (Windows/Linux):
```bash
# Private key (no passphrase)
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out snowflake_key.p8 -nocrypt

# Public key
openssl rsa -in snowflake_key.p8 -pubout -out snowflake_key.pub

# Get public key string (remove headers/footers)
cat snowflake_key.pub | grep -v "BEGIN PUBLIC" | grep -v "END PUBLIC" | tr -d '\n'
```

### Snowflake User Setup:
```sql
USE ROLE ACCOUNTADMIN;

CREATE USER DBT_SERVICE_USER
    DEFAULT_ROLE = DBT_SERVICE_ROLE
    DEFAULT_WAREHOUSE = DBT_GENERATOR_WH;

ALTER USER DBT_SERVICE_USER SET RSA_PUBLIC_KEY='<YOUR_PUBLIC_KEY_STRING>';

CREATE ROLE DBT_SERVICE_ROLE;
GRANT ROLE DBT_SERVICE_ROLE TO USER DBT_SERVICE_USER;
GRANT USAGE ON WAREHOUSE DBT_GENERATOR_WH TO ROLE DBT_SERVICE_ROLE;
GRANT USAGE ON DATABASE DBT_GENERATOR TO ROLE DBT_SERVICE_ROLE;
GRANT ALL ON SCHEMA DBT_GENERATOR.APP TO ROLE DBT_SERVICE_ROLE;
GRANT ALL ON ALL TABLES IN SCHEMA DBT_GENERATOR.APP TO ROLE DBT_SERVICE_ROLE;
```

## Snowflake Database Setup (One-Time):
```sql
-- Run snowflake_complete_setup.sql, or manually:
CREATE DATABASE DBT_GENERATOR;
CREATE SCHEMA DBT_GENERATOR.APP;
USE SCHEMA DBT_GENERATOR.APP;

-- Create application tables (see snowflake_complete_setup.sql for full DDL)
```

## VM Deployment Commands

### 1. Install Docker:
```bash
sudo apt-get update
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Transfer Files:
```bash
# From your local machine
scp -r /path/to/dbt-code-generator user@vm-ip:~/

# Copy private key
scp snowflake_key.p8 user@vm-ip:~/dbt-code-generator/
```

### 3. Configure Environment:
```bash
cd ~/dbt-code-generator
cp .env.example .env
nano .env
# Fill in your Snowflake credentials
chmod 600 snowflake_key.p8
```

### 4. Deploy:
```bash
docker compose up --build -d
```

## Essential Commands

```bash
# View logs
docker compose logs -f

# Check status
docker compose ps

# Restart
docker compose restart

# Stop
docker compose down

# Rebuild after changes
docker compose up --build -d

# Test connection
curl http://localhost

# View errors
docker compose logs app | grep -i error
```

## Verification URLs

- **Web Interface**: `http://YOUR_VM_IP`
- **API Docs**: `http://YOUR_VM_IP/api/docs`
- **Health Check**: `http://YOUR_VM_IP/api/`

## Common Issues

| Issue | Solution |
|-------|----------|
| Port 80 in use | `sudo lsof -i :80` then stop conflicting service |
| Can't connect to Snowflake | Check `.env` credentials, verify Snowflake user |
| Container won't start | `docker compose logs` for errors |
| Private key error | Verify path `/app/snowflake_key.p8` and permissions |
| Can't access from browser | Check VM firewall: `sudo ufw allow 80/tcp` |

## Environment Variables Quick Reference

**Service Account Auth:**
```env
SNOWFLAKE_ACCOUNT=your_account.region
SNOWFLAKE_USER=DBT_SERVICE_USER
SNOWFLAKE_PRIVATE_KEY_PATH=/app/snowflake_key.p8
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=
SNOWFLAKE_PASSWORD=
# ... (see .env.example for full template)
```

**Password Auth:**
```env
SNOWFLAKE_ACCOUNT=your_account.region
SNOWFLAKE_USER=DBT_APP_USER
SNOWFLAKE_PASSWORD=<YOUR_STRONG_PASSWORD>
SNOWFLAKE_PRIVATE_KEY_PATH=
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=
# ... (see .env.example for full template)
```

## Security Reminders

- Use service account with private key (not password)
- Set private key permissions: `chmod 600 snowflake_key.p8`
- Never commit `.env` or private keys to git
- Rotate keys every 90 days
- Use firewall rules to restrict access

---

**For detailed instructions, see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)**
