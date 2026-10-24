-- V078: new or enhance on onboarding requests (apps/api/app/requests_api.py, services/onboarding/requests.py). Home
-- starts every workflow as New or Enhance: a new domain, source or data product, or more from one already on the
-- platform (an existing domain's model extended, an existing source's scope widened).
--   INTENT               NEW | ENHANCE; existing requests are NEW
--   BASE_MODEL_VERSION   for an Enhance domain request, the domain's approved model version when it was filed: the
--                        request waits on the Data Designer until a newer version is approved
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

ALTER TABLE {{database}}.CORE.ONBOARDING_REQUEST ADD COLUMN IF NOT EXISTS INTENT VARCHAR(8) DEFAULT 'NEW';
ALTER TABLE {{database}}.CORE.ONBOARDING_REQUEST ADD COLUMN IF NOT EXISTS BASE_MODEL_VERSION NUMBER(10,0);
