# Pipeline Audit Timeline Tool

Generates an audit timeline for a pipeline by querying both FizzyFaygoAuditLog and PipelineConfigurationAuditTable to show user-triggered activities and workflow details.

The script uses PipelineConfigurationAuditTable as the source of truth and displays two outputs:
1. **Primary Audit Timeline**: Clean audit table data showing actual user actions
2. **FizzyFaygo Audit Records**: All FizzyFaygo records with parsed argumentsPassed fields

## Basic Usage

```bash
python3 pipeline_audit_timeline.py --pipeline_arn <PIPELINE_ARN>
```

By default, the tool provides:
- Timeline of events in a pipeline
- Associated cluster ARNs
- Basic summary of events
- Action types and lifecycle status changes

## Options

- `--pipeline_arn` (required) - Pipeline ARN in format: `arn:aws:osis:region:account-id:pipeline/pipeline-name`
- `--workflowModel` - Show specific fields from FizzyFaygo workflow data (argumentsPassed field)
- `--pipelineConfigurationAuditTable_field` - Show specific fields from pipeline configuration audit table
- `--summary` - Show summary information only, skip detailed tables

## Field Options

### --workflowModel (FizzyFaygo argumentsPassed fields)
Extract specific fields from the argumentsPassed field in the FizzyFaygo audit table, which contains the workflow model data.

Common fields:
```bash
--workflowModel action accountId pipelineName clusterArn computeUnits minUnits maxUnits executionId internalId
```

### --pipelineConfigurationAuditTable_field (Configuration audit table fields)
Display specific fields from the PipelineConfigurationAuditTable records.

Common fields:
```bash
--pipelineConfigurationAuditTable_field minUnits maxUnits pipelineUnits version pipelineConfigurationBody lifecycleStatus
```

## Examples

```
python3 pipeline_audit_timeline.py --pipeline_arn arn:aws:osis:us-west-2:123123123123:pipeline/mypipeline --workflowModel maxUnits --pipelineConfigurationAuditTable_field pipelineConfigurationBody > out
```

## Output

The tool generates two main sections:

1. **Pipeline Configuration Audit Timeline** - Shows the authoritative timeline from PipelineConfigurationAuditTable with:
   - Timestamp of each event
   - Action type (CREATE, UPDATE, DELETE, START, STOP)
   - Lifecycle status
   - Internal ID
   - Any requested configuration fields

2. **FizzyFaygo Audit Records** - Shows all FizzyFaygo records with:
   - Timestamp of each workflow step
   - Action from argumentsPassed
   - Cluster ARN
   - Any requested workflow model fields

Each section includes a summary with event counts, unique identifiers, and time span information.


