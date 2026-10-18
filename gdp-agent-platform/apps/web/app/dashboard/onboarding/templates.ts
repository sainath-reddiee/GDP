// Starter files for domain onboarding, shown on the Onboarding page and offered as downloads.
// tests/unit/test_onboarding_templates.py checks both against the real validators (services/knowledge/packs.prepare,
// services/knowledge/validate.validate_pack, services/catalog/quality.validate_contract_yaml), so keep them valid.

export const PACK_TEMPLATE = `{
  "domain": {
    "name": "CUSTOMER",
    "description": "Customers and their contact points",
    "owner": "Customer Data Office"
  },
  "targets": [
    {
      "table": "CUSTOMER_CORE",
      "grain": "One row per customer",
      "business_keys": ["CUSTOMER_ID"],
      "columns": [
        { "name": "CUSTOMER_ID", "type": "VARCHAR(64)", "nullable": false, "business_key": true, "definition": "Customer number in the system of record" },
        { "name": "CUSTOMER_NAME", "type": "VARCHAR(255)", "definition": "Legal or trading name" },
        { "name": "EMAIL", "type": "VARCHAR(320)", "pii": true },
        { "name": "STATUS", "type": "VARCHAR(16)", "accepted_values": ["ACTIVE", "INACTIVE"] }
      ]
    }
  ],
  "knowledge": [
    {
      "key": "glossary.customer_name",
      "type": "GLOSSARY",
      "title": "CUSTOMER_CORE.CUSTOMER_NAME",
      "content_json": { "target_table": "CUSTOMER_CORE", "target_column": "CUSTOMER_NAME", "synonyms": ["CUST_NM", "ACCOUNT_NAME"] }
    },
    {
      "key": "rule.status_codes",
      "type": "BUSINESS_RULE",
      "title": "Status codes",
      "content_json": { "target_column": "STATUS", "source_values": { "A": "ACTIVE", "I": "INACTIVE" } }
    }
  ]
}
`;

export const CONTRACT_TEMPLATE = `# Data contract: what each table must contain
domain: CUSTOMER
version: "1.0"
owner: Customer Data Office
tables:
  - name: CUSTOMER_CORE
    description: One row per customer
    grain: customer
    business_keys: [CUSTOMER_ID]
    columns:
      - name: CUSTOMER_ID
        type: VARCHAR(64)
        required: true
        unique: true
      - name: CUSTOMER_NAME
        type: VARCHAR(255)
        required: true
      - name: EMAIL
        type: VARCHAR(320)
        description: Primary contact email (PII)
      - name: STATUS
        type: VARCHAR(16)
        accepted_values: [ACTIVE, INACTIVE]
`;
