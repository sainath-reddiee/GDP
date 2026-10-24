-- V077: the Data Designer persona works with its own design role (DATA_DESIGNER, a system role the API bootstraps)
-- instead of DATA_ENGINEER, so working as a designer no longer opens code, operations, QA, Jira or skills.
-- Only a bundle still exactly as V076 seeded it is changed: a bundle a super admin edited stays as they left it.
-- People who hold the persona keep their roles; the persona narrows what they may do while they work as it.
-- The role itself is not created here: the API's governance bootstrap creates it with its privileges on start (it
-- seeds privileges only for a role it creates, so creating it here would leave it empty).

UPDATE {{database}}.CORE.PERSONA_ROLE SET ROLE_NAME = 'DATA_DESIGNER'
 WHERE PERSONA_ID = 'designer' AND ROLE_NAME = 'DATA_ENGINEER'
   AND (SELECT LISTAGG(ROLE_NAME, ',') WITHIN GROUP (ORDER BY ROLE_NAME)
          FROM {{database}}.CORE.PERSONA_ROLE WHERE PERSONA_ID = 'designer')
       = 'DATA_ENGINEER,DQ_APPROVER,MODEL_APPROVER,STTM_APPROVER';
