# Snowflake Authentication Setup Guide

This application supports two authentication methods for Snowflake:
1. **Password Authentication** (Traditional)
2. **Private Key Authentication** (Service Account/Key-Pair - Recommended for Production)

## Option 1: Password Authentication

This is the simpler method, suitable for development environments.

### Setup Steps:

1. **Create/Update `.env` file** in the project root:
```bash
SNOWFLAKE_ACCOUNT=your_account
SNOWFLAKE_USER=your_username
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=your_warehouse
SNOWFLAKE_DATABASE=your_database
SNOWFLAKE_SCHEMA=your_schema
SNOWFLAKE_ROLE=your_role
```

2. **Start the application**:
```bash
sudo docker compose up -d
```

---

## Option 2: Private Key Authentication (Recommended)

This method uses key-pair authentication, which is more secure and recommended for production environments.

### Prerequisites:

You need a private key file (`.p8` or `.pem` format) for your Snowflake service account.

#### Generate a Private Key (if you don't have one):

1. **Generate an RSA private key** (with passphrase):
```bash
openssl genrsa -out snowflake_key.pem -aes256 2048
```

Or without passphrase:
```bash
openssl genrsa -out snowflake_key.pem 2048
```

2. **Generate the public key**:
```bash
openssl rsa -in snowflake_key.pem -pubout -out snowflake_key.pub
```

3. **Convert to PKCS8 format** (required for Snowflake):
```bash
openssl pkcs8 -topk8 -inform PEM -outform PEM -in snowflake_key.pem -out snowflake_key.p8
```

4. **Register the public key with Snowflake**:
```sql
-- Get the public key content (remove header/footer and join lines)
ALTER USER your_username SET RSA_PUBLIC_KEY='MIIBIjANBgkqhki...';
```

### Setup Steps:

1. **Place your private key file** in the project root with the name `snowflake_key.p8`

2. **Create/Update `.env` file**:
```bash
SNOWFLAKE_ACCOUNT=your_account
SNOWFLAKE_USER=your_username
# Comment out or remove password
# SNOWFLAKE_PASSWORD=

# Add private key configuration
SNOWFLAKE_PRIVATE_KEY_PATH=/app/snowflake_key.p8
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=your_passphrase_if_encrypted

SNOWFLAKE_WAREHOUSE=your_warehouse
SNOWFLAKE_DATABASE=your_database
SNOWFLAKE_SCHEMA=your_schema
SNOWFLAKE_ROLE=your_role
```

**Note:** If your private key is not encrypted, omit the `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` variable or leave it empty.

3. **Verify the docker-compose.yml** has the volume mount (already configured):
```yaml
volumes:
  - ./snowflake_key.p8:/app/snowflake_key.p8:ro
```

4. **Start the application**:
```bash
sudo docker compose down
sudo docker compose up -d
```

5. **Check logs** to verify authentication:
```bash
sudo docker compose logs -f app
```

You should see: `Using private key authentication for Snowpark session`

---

## Troubleshooting

### Error: "Password is empty"
- Make sure you've set either `SNOWFLAKE_PASSWORD` or `SNOWFLAKE_PRIVATE_KEY_PATH`
- If using private key, ensure the path in `.env` matches the mounted path in the container

### Error: "Failed to load private key"
- Verify the private key file exists in the project root
- Check the file permissions: `chmod 600 snowflake_key.p8`
- Verify the private key format (should be PKCS8 PEM format)
- If encrypted, ensure the passphrase is correct

### Error: "JWT token is invalid"
- Verify the public key is correctly registered with your Snowflake user
- Ensure the username matches the user with the registered public key
- Check that the private key corresponds to the registered public key

### Verify Authentication Method
Check the application logs to see which authentication method is being used:
```bash
sudo docker compose logs app | grep "authentication"
```

---

## Security Best Practices

1. **Never commit private keys** to version control
   - Add `*.p8`, `*.pem`, `snowflake_key.*` to `.gitignore`

2. **Use encrypted private keys** in production environments

3. **Restrict file permissions**:
```bash
chmod 600 snowflake_key.p8
```

4. **Use separate service accounts** for different environments

5. **Rotate keys regularly** according to your security policy

6. **Consider using secrets management** tools (AWS Secrets Manager, Azure Key Vault, etc.) for production deployments
