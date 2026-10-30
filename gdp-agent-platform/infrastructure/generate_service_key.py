"""
Generate Snowflake RSA Key-Pair for GDP Agent Platform Service User.
Creates:
  - docker/secrets/aip_service_key.p8 (PKCS#8 AES-256 encrypted private key)
  - docker/secrets/aip_service_key.pub (PEM public key)
Outputs the Snowflake SQL command to create/update AIP_SERVICE.
"""

import argparse
import os
import secrets
import string
import sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend


def generate_passphrase(length=24) -> str:
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def main():
    parser = argparse.ArgumentParser(description="Generate Snowflake service user RSA key-pair.")
    parser.add_argument("--out-dir", default="docker/secrets", help="Directory to save keys (default: docker/secrets)")
    parser.add_argument("--passphrase", help="Passphrase for the private key (auto-generated if omitted)")
    parser.add_argument("--user", default="AIP_SERVICE", help="Snowflake service username (default: AIP_SERVICE)")
    parser.add_argument("--role", default="AODS_OPS_SERVICE", help="Snowflake service role (default: AODS_OPS_SERVICE)")
    parser.add_argument("--warehouse", default="DBT_WH", help="Snowflake warehouse (default: DBT_WH)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    passphrase = args.passphrase or generate_passphrase()
    passphrase_bytes = passphrase.encode("utf-8")

    # 1. Generate 2048-bit RSA Private Key
    print("Generating 2048-bit RSA key pair...")
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    # 2. Serialize Private Key (PKCS#8 with AES-256)
    p8_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase_bytes)
    )
    p8_file = out_dir / "aip_service_key.p8"
    p8_file.write_bytes(p8_bytes)
    print(f"Private key saved to: {p8_file}")

    # 3. Serialize Public Key (PEM)
    public_key = private_key.public_key()
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub_file = out_dir / "aip_service_key.pub"
    pub_file.write_bytes(pub_bytes)
    print(f"Public key saved to:  {pub_file}")

    # 4. Extract public key body without headers/footers for Snowflake SQL
    pub_lines = pub_bytes.decode("utf-8").strip().splitlines()
    key_body_lines = [l for l in pub_lines if not l.startswith("-----")]
    raw_public_key = "".join(key_body_lines).strip()

    print("\n" + "=" * 70)
    print("KEY DETAILS:")
    print("=" * 70)
    print(f"Passphrase:              {passphrase}")
    print(f"Private Key File:        {p8_file.resolve()}")
    print("\nAdd this passphrase to docker/api.env:")
    print(f"AIP_SERVICE_KEY_PASSPHRASE={passphrase}")

    print("\n" + "=" * 70)
    print("SNOWFLAKE SQL (Run in Snowsight with ACCOUNTADMIN or USERADMIN):")
    print("=" * 70)
    sql = f"""-- Create or update the AIP_SERVICE user
CREATE USER IF NOT EXISTS {args.user}
  TYPE = SERVICE
  DEFAULT_ROLE = {args.role}
  DEFAULT_WAREHOUSE = {args.warehouse}
  RSA_PUBLIC_KEY = '{raw_public_key}';

-- If the user already exists, update the public key and defaults:
ALTER USER {args.user} SET
  TYPE = SERVICE,
  DEFAULT_ROLE = {args.role},
  DEFAULT_WAREHOUSE = {args.warehouse},
  RSA_PUBLIC_KEY = '{raw_public_key}';

-- Grant required roles and privileges:
GRANT ROLE {args.role} TO USER {args.user};
GRANT USAGE ON WAREHOUSE {args.warehouse} TO ROLE {args.role};
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE {args.role};
"""
    print(sql)


if __name__ == "__main__":
    main()

