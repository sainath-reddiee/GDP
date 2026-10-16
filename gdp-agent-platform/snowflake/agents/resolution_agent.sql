-- The resolution agent (airflow-failure-resolution-architecture, slide 2: "SKILL: agent runs steps"): runs the steps
-- of a published multi-step fix in order. Its only tool runs the next step of that fix; the tool refuses repeats and
-- steps out of order, and every step is a reviewed CORE.FIX_* procedure. Whatever it does not finish, the platform
-- finishes deterministically (services/resolve/fixes.py).

CREATE OR REPLACE AGENT {{database}}.{{schema}}.RESOLUTION_AGENT
  COMMENT = 'Runs the steps of a published multi-step Airflow fix, in order, through one guarded tool'
  PROFILE = '{"display_name": "Airflow resolution agent"}'
  FROM SPECIFICATION
  $$
models:
  orchestration: claude-sonnet-4-5
instructions:
  orchestration: >
    You run one published fix for one Airflow failure. Call run_fix_step for each step number in the order given,
    with the FAILURE_ID and RESOLUTION_ID from the message. Stop at the first step whose status is not SUCCESS or that
    returns an error, and report it. Never skip, repeat or reorder steps, and never do anything else.
  response: >
    Say which steps ran and their status, in one short paragraph.
tools:
  - tool_spec:
      type: generic
      name: run_fix_step
      description: Run one step of the published fix for this failure
      input_schema:
        type: object
        properties:
          FAILURE_ID: {type: string, description: Failure id}
          RESOLUTION_ID: {type: string, description: Resolution id from the message}
          STEP_NO: {type: number, description: Step number}
        required: [FAILURE_ID, RESOLUTION_ID, STEP_NO]
tool_resources:
  run_fix_step:
    type: procedure
    identifier: "{{database}}.{{schema}}.SP_RUN_FIX_STEP"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  $$;

GRANT USAGE ON AGENT {{database}}.{{schema}}.RESOLUTION_AGENT TO DATABASE ROLE {{database}}.OPS_SERVICE;
