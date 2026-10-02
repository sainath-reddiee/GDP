# Deployment Guide

## DBT Code Generator Application

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Snowflake Setup](#snowflake-setup)
3. [Service Account Authentication Setup](#service-account-authentication-setup)
4. [VM Preparation](#vm-preparation)
5. [Application Deployment](#application-deployment)
6. [Verification](#verification)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### What You Need:
- Ubuntu VM (20.04 or 22.04)
- SSH access to the VM
- Snowflake account with admin privileges
- VM network allows outbound HTTPS (443) to Snowflake
- Inbound access to port 80 (HTTP) for web interface

---

## Snowflake Setup

### Step 1: Run the Complete Setup Script

The easiest approach is to run the provided `snowflake_complete_setup.sql` script in Snowflake (via SnowSQL or Snowsight). This single script creates:

- Warehouse (`DBT_GENERATOR_WH`)
- Database and schema (`DBT_GENERATOR.APP`)
- Application tables (`PROJECTS`, `MAPPING_ROWS`, `DBT_FILES`, `MACRO_LIBRARY`)
- Service role (`DBT_SERVICE_ROLE`) with appropriate grants
- Service user (`DBT_SERVICE_USER`)
- Cortex AI access grants

```sql
-- Connect to Snowflake as ACCOUNTADMIN and run:
-- snowflake_complete_setup.sql
```

### Step 2 (Manual Alternative): Create Database and Schema

If you prefer to run steps individually:

```sql
USE ROLE ACCOUNTADMIN;

CREATE DATABASE IF NOT EXISTS DBT_GENERATOR;
CREATE SCHEMA IF NOT EXISTS DBT_GENERATOR.APP;
USE SCHEMA DBT_GENERATOR.APP;
```

Then create the tables as described in `snowflake_complete_setup.sql`.

---

## Service Account Authentication Setup

### Option A: Key-Pair Authentication (Recommended)

#### Step 1: Generate RSA Key Pair

```bash
# Generate private key (no passphrase)
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out snowflake_key.p8 -nocrypt

# Generate public key from private key
openssl rsa -in snowflake_key.p8 -pubout -out snowflake_key.pub
```

**With passphrase (more secure):**
```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out snowflake_key.p8
# You'll be prompted to enter a passphrase
```

#### Step 2: Assign Public Key to Snowflake User

```sql
USE ROLE ACCOUNTADMIN;

-- Get the public key content (remove header/footer and newlines)
-- Then set it on the user:
ALTER USER DBT_SERVICE_USER SET RSA_PUBLIC_KEY='<YOUR_PUBLIC_KEY_STRING>';
```

#### Step 3: Extract Public Key String

```bash
# On Linux/Mac or WSL
cat snowflake_key.pub | grep -v "BEGIN PUBLIC" | grep -v "END PUBLIC" | tr -d '\n'

# On Windows PowerShell
(Get-Content snowflake_key.pub) -replace '-----BEGIN PUBLIC KEY-----','' -replace '-----END PUBLIC KEY-----','' -replace "`n",'' -replace "`r",''
```

Copy the output and use it in the `ALTER USER` command above.

---

### Option B: Username/Password Authentication

If you prefer password authentication:

```sql
USE ROLE ACCOUNTADMIN;

CREATE USER IF NOT EXISTS DBT_APP_USER
    PASSWORD = '<YOUR_STRONG_PASSWORD>'
    DEFAULT_ROLE = DBT_SERVICE_ROLE
    DEFAULT_WAREHOUSE = DBT_GENERATOR_WH
    MUST_CHANGE_PASSWORD = FALSE;

GRANT ROLE DBT_SERVICE_ROLE TO USER DBT_APP_USER;
```

---

## VM Preparation

### Step 1: SSH into Your Ubuntu VM

```bash
ssh your-username@your-vm-ip
```

### Step 2: Install Docker and Docker Compose

```bash
# Update system
sudo apt-get update

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose
sudo apt-get install -y docker-compose-plugin

# Add your user to docker group
sudo usermod -aG docker $USER

# Apply group changes (logout and login again, or use)
newgrp docker

# Verify installation
docker --version
docker compose version
```

### Step 3: Create Application Directory

```bash
mkdir -p ~/dbt-code-generator
cd ~/dbt-code-generator
```

---

## Application Deployment

### Step 1: Transfer Application Files to VM

**Option A - Using SCP:**

```bash
# From your local machine, transfer the project directory
scp -r /path/to/dbt-code-generator your-username@your-vm-ip:~/dbt-code-generator/
```

**Option B - Using Git:**

```bash
# On the VM
cd ~/dbt-code-generator
git clone <your-repository-url> .
```

**Option C - Manual file transfer using WinSCP or FileZilla (GUI)**

### Step 2: Transfer Private Key (If Using Key-Pair Auth)

```bash
# Copy the private key to the project directory on the VM
scp snowflake_key.p8 your-username@your-vm-ip:~/dbt-code-generator/
```

On VM, secure the key:
```bash
cd ~/dbt-code-generator
chmod 600 snowflake_key.p8
```

### Step 3: Create Environment Configuration

Create `.env` file on the VM:

```bash
cd ~/dbt-code-generator
cp .env.example .env
nano .env
# Fill in your actual Snowflake credentials
```

**For Service Account (Private Key) Authentication:**

```env
# Snowflake Configuration
SNOWFLAKE_ACCOUNT=your_account.region
SNOWFLAKE_USER=DBT_SERVICE_USER
SNOWFLAKE_WAREHOUSE=DBT_GENERATOR_WH
SNOWFLAKE_DATABASE=DBT_GENERATOR
SNOWFLAKE_SCHEMA=APP
SNOWFLAKE_ROLE=DBT_SERVICE_ROLE

# Private Key Authentication
SNOWFLAKE_PRIVATE_KEY_PATH=/app/snowflake_key.p8
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=

# Leave password empty when using private key
SNOWFLAKE_PASSWORD=

# AI Settings
CORTEX_MODEL=openai-gpt-5

# App Config
APP_NAME=DBT Code Generator
ENVIRONMENT=production
DEBUG=false
```

**For Password Authentication:**

```env
# Snowflake Configuration
SNOWFLAKE_ACCOUNT=your_account.region
SNOWFLAKE_USER=DBT_APP_USER
SNOWFLAKE_WAREHOUSE=DBT_GENERATOR_WH
SNOWFLAKE_DATABASE=DBT_GENERATOR
SNOWFLAKE_SCHEMA=APP
SNOWFLAKE_ROLE=DBT_SERVICE_ROLE

# Password Authentication
SNOWFLAKE_PASSWORD=<YOUR_STRONG_PASSWORD>

# Leave these empty when using password
SNOWFLAKE_PRIVATE_KEY_PATH=
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=

# AI Settings
CORTEX_MODEL=openai-gpt-5

# App Config
APP_NAME=DBT Code Generator
ENVIRONMENT=production
DEBUG=false
```

Save with `Ctrl+X`, `Y`, `Enter`

### Step 4: Build and Start the Application

```bash
cd ~/dbt-code-generator
docker compose up --build -d

# Check if containers are running
docker compose ps

# View logs
docker compose logs -f
```

### Step 5: Configure Firewall (if needed)

```bash
# Check firewall status
sudo ufw status

# Allow HTTP traffic if firewall is active
sudo ufw allow 80/tcp
```

---

## Verification

### 1. Check Container Status

```bash
docker compose ps
# Should show container running on port 80
```

### 2. View Application Logs

```bash
docker compose logs -f app
# Look for successful Snowflake connection messages
```

### 3. Test from VM

```bash
curl http://localhost
# Should return HTML content
```

### 4. Test from Browser

Open your web browser and navigate to:
```
http://YOUR_VM_IP
```

You should see the DBT Code Generator interface.

### 5. Test API Endpoint

```
http://YOUR_VM_IP/api/docs
```

This should open the FastAPI Swagger documentation.

### 6. Verify Database Connection

Check the logs for Snowflake connection:
```bash
docker compose logs app | grep -i "snowflake\|authentication"
```

---

## Troubleshooting

### Container Won't Start

```bash
# Check logs for errors
docker compose logs

# Check if port 80 is already in use
sudo lsof -i :80

# Stop conflicting service
sudo systemctl stop apache2  # or nginx
```

### Snowflake Connection Issues

```bash
# Check environment variables
docker compose exec app env | grep SNOWFLAKE

# Test Snowflake connection from container
docker compose exec app python -c "
from app.utils.snowflake_connection import get_snowflake_engine
engine = get_snowflake_engine()
with engine.connect() as conn:
    print('Connection successful!')
"
```

### Private Key Issues

```bash
# Verify key file exists and has correct permissions
ls -la snowflake_key.p8
# Should show: -rw------- (600 permissions)

# Check if key is mounted in container
docker compose exec app ls -la /app/snowflake_key.p8
```

### Can't Access from Browser

```bash
# Check if VM firewall allows port 80
sudo ufw status

# Check VM security group/network ACLs (cloud provider)
# Ensure inbound rule for port 80 from your IP

# Test from VM
curl -v http://localhost
```

### View Detailed Logs

```bash
# Application logs
docker compose logs app

# Nginx logs
docker compose exec app cat /var/log/nginx/error.log
```

---

## Application Management

### Restart Application

```bash
docker compose restart
```

### Stop Application

```bash
docker compose down
```

### Update Application (after code changes)

```bash
# Pull latest code (if using git)
git pull

# Rebuild and restart
docker compose down
docker compose up --build -d
```

### View Resource Usage

```bash
docker stats
```

---

## Success Checklist

- [ ] Snowflake database and schema created
- [ ] Application tables created
- [ ] Service account/user configured with proper permissions
- [ ] VM has Docker and Docker Compose installed
- [ ] Application files transferred to VM
- [ ] Private key file transferred and secured (if using key-pair auth)
- [ ] `.env` file configured with correct credentials
- [ ] Container built and running
- [ ] Application accessible from browser
- [ ] API documentation loads at `/api/docs`
- [ ] Can upload mapping files
- [ ] Can generate dbt code

---

## Security Best Practices

1. **Use service account with key-pair authentication** instead of password
2. **Protect private key file** with 600 permissions
3. **Use strong passphrases** for encrypted private keys
4. **Enable IP whitelisting** in Snowflake network policies
5. **Set up HTTPS** with SSL certificate (Let's Encrypt)
6. **Rotate keys regularly** (every 90 days)
7. **Use secrets management** (AWS Secrets Manager, Azure Key Vault, etc.)
8. **Enable audit logging** in Snowflake
9. **Restrict VM access** to specific IPs
10. **Keep VM and Docker updated** regularly
