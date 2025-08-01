#!/usr/bin/env python3
"""
This script generates an audit timeline for a pipeline by querying both FizzyFaygoAuditLog 
and PipelineConfigurationAuditTable to show user-triggered activities and workflow details.

The script uses PipelineConfigurationAuditTable as the source of truth and displays two outputs:
1. Primary Audit Timeline: Clean audit table data showing actual user actions
2. FizzyFaygo Audit Records: All FizzyFaygo records with parsed argumentsPassed fields

Usage:
python3 pipeline_audit_timeline.py --pipeline_arn "arn:aws:osis:us-east-1:123456789012:pipeline/my-pipeline" --workflowModel argumentsPassed executionId --pipelineConfigurationAuditTable_field minUnits maxUnits pipelineUnits
"""

import argparse
import boto3
import re
import subprocess
from datetime import datetime, timezone
from tabulate import tabulate
from typing import Dict, List, Optional

# Control plane account mapping
CONTROL_PLANE_ACCOUNT_MAPPING = {
    "us-east-1": "650455509444",
    "us-east-2": "642133779026",
    "us-west-1": "110293142570",
    "us-west-2": "647705926003",
    "eu-west-1": "321843683841",
    "eu-west-2": "494103247145",
    "eu-west-3": "958100456306",
    "eu-north-1": "766161543942",
    "eu-south-1": "483623536314",
    "eu-central-1": "793214851599",
    "ap-northeast-1": "488939961373",
    "ap-northeast-2": "445771684395",
    "ap-southeast-1": "250245820245",
    "ap-southeast-2": "024043984225",
    "ap-south-1": "008739254458",
    "sa-east-1": "706976067050",
    "ca-central-1": "059841243330"
}


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Generate pipeline audit timeline from audit tables.')
    parser.add_argument('--pipeline_arn', required=True, 
                       help='Pipeline ARN (e.g., arn:aws:osis:us-east-1:123456789012:pipeline/my-pipeline)')
    parser.add_argument('--region', 
                       help='AWS region (will be extracted from ARN if not provided)')
    parser.add_argument('--workflowModel', nargs='*', default=[], 
                       help='Fields to extract from argumentsPassed in FizzyFaygoAuditLog (e.g., executionId pipelineArn accountId internalId)')
    parser.add_argument('--pipelineConfigurationAuditTable_field', nargs='*', default=[], 
                       help='Fields to display from PipelineConfigurationAuditTable (e.g., minUnits maxUnits pipelineUnits version)')
    parser.add_argument('--summary', action='store_true',
                       help='Only display summary information, skip detailed tables')
    
    return parser.parse_args()


def validate_and_extract_arn_components(pipeline_arn: str) -> Dict[str, str]:
    """
    Parse pipeline ARN and extract components.
    
    Expected format: arn:aws:osis:region:account-id:pipeline/pipeline-name
    """
    arn_pattern = r'^arn:aws:osis:([^:]+):(\d+):pipeline/(.+)$'
    match = re.match(arn_pattern, pipeline_arn)
    
    if not match:
        raise ValueError(f"Invalid pipeline ARN format: {pipeline_arn}")
    
    region, account_id, pipeline_name = match.groups()
    
    return {
        'region': region,
        'account_id': account_id,
        'pipeline_name': pipeline_name,
        'full_arn': pipeline_arn
    }


def get_credentials(account: str):
    """Get credentials for the specified account using ada."""
    command = [
        'ada', 'credentials', 'update',
        '--account', account,
        '--role', 'ReadOnly',
        '--provider', 'isengard',
        '--once'
    ]
    print(f"Getting credentials for account {account}")
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Successfully updated credentials for account {account}")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to update credentials for account {account}")
        print(f"Command output: {e.stdout}")
        print(f"Command error: {e.stderr}")
        print("Continuing with existing credentials...")


def get_control_plane_session(region: str) -> boto3.Session:
    """Get boto3 session for the control plane account."""
    cp_account = CONTROL_PLANE_ACCOUNT_MAPPING.get(region)
    if not cp_account:
        raise ValueError(f"No control plane account mapping found for region: {region}")
    
    try:
        get_credentials(cp_account)
    except Exception as e:
        print(f"Warning: Could not update credentials for account {cp_account}: {e}")
        print("Attempting to use existing credentials...")
    
    return boto3.Session()


def query_fizzy_faygo_audit_table(account_id: str, pipeline_name: str, 
                                 dynamodb_client) -> List[Dict]:
    """
    Query FizzyFaygoAuditLog table for all records of the specified pipeline with pagination.
    
    Table structure:
    - Partition key: accountIdAndPipelineName (format: "accountId|pipelineName")
    - Sort key: requestTimestamp
    """
    partition_key = f"{account_id}|{pipeline_name}"
    all_items = []
    
    try:
        # Paginate through all results
        last_evaluated_key = None
        
        while True:
            query_params = {
                'TableName': 'FizzyFaygoAuditLog',
                'KeyConditionExpression': 'accountIdAndPipelineName = :pk',
                'ExpressionAttributeValues': {
                    ':pk': {'S': partition_key}
                }
            }
            
            if last_evaluated_key:
                query_params['ExclusiveStartKey'] = last_evaluated_key
            
            response = dynamodb_client.query(**query_params)
            items = response.get('Items', [])
            all_items.extend(items)
            
            # Check if there are more pages
            last_evaluated_key = response.get('LastEvaluatedKey')
            if not last_evaluated_key:
                break
        
        print(f"Retrieved {len(all_items)} total FizzyFaygo records with pagination")
        return all_items
        
    except Exception as e:
        print(f"Error querying FizzyFaygoAuditLog: {e}")
        return []


def query_pipeline_configuration_audit_table(internal_id: str, dynamodb_client) -> List[Dict]:
    """
    Query PipelineConfigurationAuditTable for all user-triggered activities with pagination.
    
    Table structure:
    - Partition key: internalId
    - Sort key: changedTimestamp
    """
    all_items = []
    
    try:
        # Paginate through all results
        last_evaluated_key = None
        
        while True:
            query_params = {
                'TableName': 'PipelineConfigurationAuditTable',
                'KeyConditionExpression': 'internalId = :pk',
                'ExpressionAttributeValues': {
                    ':pk': {'S': internal_id}
                }
            }
            
            if last_evaluated_key:
                query_params['ExclusiveStartKey'] = last_evaluated_key
            
            response = dynamodb_client.query(**query_params)
            items = response.get('Items', [])
            all_items.extend(items)
            
            # Check if there are more pages
            last_evaluated_key = response.get('LastEvaluatedKey')
            if not last_evaluated_key:
                break
        
        return all_items
        
    except Exception as e:
        print(f"Error querying PipelineConfigurationAuditTable: {e}")
        return []


def dynamodb_to_dict(item: Dict) -> Dict:
    """Convert DynamoDB item format to regular Python dict."""
    result = {}
    for key, value in item.items():
        if 'S' in value:
            result[key] = value['S']
        elif 'N' in value:
            result[key] = int(value['N']) if '.' not in value['N'] else float(value['N'])
        elif 'BOOL' in value:
            result[key] = value['BOOL']
        elif 'NULL' in value:
            result[key] = None
        else:
            # Handle other types as needed
            result[key] = str(value)
    return result


def parse_arguments_passed_field(arguments_passed_str: str) -> Dict:
    """
    Parse the argumentsPassed field using simple field=value extraction.
    
    Simple approach: find "field=" patterns and read until the next comma.
    This works for most cases and is easy to understand and maintain.
    
    Example format: WorkflowDataModel(action=CREATE, accountId=841692598829, pipelineName=agg-proc-arkwftkh, ...)
    """
    if not arguments_passed_str:
        return {}
    
    try:
        result = {}
        
        # Find all field=value patterns using regex
        # This pattern matches: fieldName=value where value continues until comma or end of object
        pattern = r'(\w+)=([^,)]+)'
        matches = re.findall(pattern, arguments_passed_str)
        
        for field_name, value in matches:
            # Clean up the value
            value = value.strip()
            
            # Skip null values and empty strings
            if value and value != 'null':
                result[field_name] = value
                
                # Add common backward compatibility aliases
                if field_name == 'internalId':
                    result['internal_id'] = value
                elif field_name == 'clusterArn':
                    result['cluster_arn'] = value
        
        return result
        
    except Exception as e:
        print(f"Warning: Failed to parse argumentsPassed field: {e}")
        return {'action': 'PARSE_ERROR'}


def determine_action_from_config_record(config_record: Dict) -> str:
    """
    Determine the action type based on the config record's action field or lifecycle status.
    """
    # First check if there's an explicit action field
    if 'action' in config_record:
        action = config_record['action'].upper()
        if action == 'INSERT':
            return 'CREATE'
        elif action == 'MODIFY':
            return 'UPDATE'
        elif action == 'REMOVE':
            return 'DELETE'
    
    # Fallback to inferring from lifecycle status changes
    lifecycle_status = config_record.get('lifecycleStatus', '').upper()
    if lifecycle_status in ['CREATING']:
        return 'CREATE'
    elif lifecycle_status in ['UPDATING', 'SCALING']:
        return 'UPDATE'
    elif lifecycle_status in ['DELETING']:
        return 'DELETE'
    elif lifecycle_status in ['STARTING']:
        return 'START'
    elif lifecycle_status in ['STOPPING']:
        return 'STOP'
    else:
        return 'UPDATE'  # Default assumption


def format_timestamp(timestamp_ms: int) -> str:
    """
    Format timestamp from milliseconds to readable format.
    """
    if not timestamp_ms:
        return "N/A"
    
    try:
        # Handle both milliseconds and seconds timestamps
        if timestamp_ms > 10**10:  # Likely milliseconds
            timestamp_s = timestamp_ms / 1000
        else:
            timestamp_s = timestamp_ms
        
        dt = datetime.fromtimestamp(timestamp_s, tz=timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    except (ValueError, OSError):
        return str(timestamp_ms)


def process_pipeline_config_records(pipeline_config_records: List[Dict]) -> List[Dict]:
    """
    Process PipelineConfigurationAuditTable records into clean audit entries.
    Use PipelineConfigurationAuditTable as the source of truth for actions and timing.
    
    Returns:
        List of processed audit entries from the configuration table
    """
    if not pipeline_config_records:
        print("No PipelineConfigurationAuditTable records found")
        return []
    
    # Convert and sort pipeline config records by timestamp
    config_records = [dynamodb_to_dict(item) for item in pipeline_config_records]
    config_records.sort(key=lambda x: x.get('changedTimestamp', 0))
    
    # Create primary audit entries (source of truth)
    primary_audit_entries = []
    
    for config_record in config_records:
        config_timestamp = config_record.get('changedTimestamp', 0)
        
        # Determine the actual action from the config record
        action = determine_action_from_config_record(config_record)
        
        # Create primary entry (clean audit table data)
        primary_entry = {
            'timestamp': config_timestamp,
            'action': action,
            'lifecycle_status': config_record.get('lifecycleStatus', 'UNKNOWN'),
            'internal_id': config_record.get('internalId', 'UNKNOWN'),
            'pipeline_arn': config_record.get('pipelineArn', 'UNKNOWN'),
            'source_table': 'PipelineConfigurationAuditTable'
        }
        
        # Add ALL additional fields from config record (don't hardcode field names)
        for field, value in config_record.items():
            # Skip fields we've already added to avoid overwriting
            if field not in ['changedTimestamp', 'lifecycleStatus', 'internalId', 'pipelineArn']:
                primary_entry[field] = value
        
        primary_audit_entries.append(primary_entry)
    
    print(f"Processed {len(primary_audit_entries)} audit entries")
    
    return primary_audit_entries


def display_primary_audit_timeline(primary_entries: List[Dict], pipeline_arn: str, 
                                 config_fields: List[str] = None, summary_only: bool = False):
    """
    Display the primary audit timeline (source of truth from PipelineConfigurationAuditTable).
    """
    if not primary_entries:
        print("No primary audit entries found to display")
        return
    
    if config_fields is None:
        config_fields = []
    
    # Sort entries by timestamp
    sorted_entries = sorted(primary_entries, key=lambda x: x.get('timestamp', 0))
    
    print(f"\n=== PIPELINE CONFIGURATION AUDIT TIMELINE ===")
    print(f"Pipeline ARN: {pipeline_arn}")
    print("=" * 80)
    
    if not summary_only:
        # Prepare table headers - core fields plus user-requested fields
        headers = ['Timestamp', 'Action', 'Status', 'Internal ID']
        
        # Add user-requested fields from PipelineConfigurationAuditTable
        for field in config_fields:
            headers.append(field)
        
        # Prepare table data
        table_data = []
        
        for entry in sorted_entries:
            timestamp = entry.get('timestamp', 0)
            formatted_timestamp = format_timestamp(timestamp)
            
            action = entry.get('action', 'UNKNOWN')
            status = entry.get('lifecycle_status', 'UNKNOWN')
            internal_id = entry.get('internal_id', 'UNKNOWN')
            
            row = [formatted_timestamp, action, status, internal_id]
            
            # Add user-requested config fields
            for field in config_fields:
                value = entry.get(field, 'N/A')
                row.append(str(value) if value != 'N/A' else 'N/A')
            
            table_data.append(row)
        
        # Display table
        print(tabulate(table_data, headers=headers, tablefmt='grid'))
    
    # Display summary
    print(f"\nPrimary Timeline Summary:")
    print(f"  Total events: {len(sorted_entries)}")
    
    # Count by action type
    action_counts = {}
    for entry in sorted_entries:
        action = entry.get('action', 'UNKNOWN')
        action_counts[action] = action_counts.get(action, 0) + 1
    
    for action, count in action_counts.items():
        print(f"  {action} events: {count}")
    
    # Unique internal IDs
    unique_internal_ids = set(entry.get('internal_id', '') for entry in sorted_entries)
    unique_internal_ids.discard('')  # Remove empty strings
    print(f"  Unique internal IDs: {len(unique_internal_ids)}")
    
    # Time span
    if len(sorted_entries) > 1:
        first_timestamp = sorted_entries[0].get('timestamp', 0)
        last_timestamp = sorted_entries[-1].get('timestamp', 0)
        time_span_days = (last_timestamp - first_timestamp) / (1000 * 60 * 60 * 24)
        
        print(f"  First event: {format_timestamp(first_timestamp)}")
        print(f"  Last event: {format_timestamp(last_timestamp)}")
        print(f"  Time span: {time_span_days:.1f} days")


def display_faygo_audit_records(faygo_records: List[Dict], pipeline_arn: str, 
                               workflowModel: List[str] = None, summary_only: bool = False):
    """
    Display all FizzyFaygo audit records sorted by timestamp with parsed argumentsPassed fields.
    """
    if not faygo_records:
        print("No FizzyFaygo audit records found to display")
        return
    
    if workflowModel is None:
        workflowModel = []
    
    # Convert records
    faygo_records_dict = [dynamodb_to_dict(item) for item in faygo_records]
    
    # Sort records by timestamp
    sorted_records = sorted(faygo_records_dict, key=lambda x: x.get('requestTimestamp', 0))
    
    print(f"\n=== FIZZYFAYGO AUDIT RECORDS ===")
    print(f"Pipeline ARN: {pipeline_arn}")
    print("=" * 100)
    
    if not summary_only:
        # Prepare table headers - always include timestamp, action, and cluster ARN
        headers = ['Timestamp', 'Action', 'Cluster ARN']
        
        # Add user-requested fields from argumentsPassed
        for field in workflowModel:
            headers.append(field)
        
        # Prepare table data
        table_data = []
        
        for record in sorted_records:
            timestamp = record.get('requestTimestamp', 0)
            formatted_timestamp = format_timestamp(timestamp)
            
            # Parse argumentsPassed to extract action, cluster ARN and other requested fields
            arguments_passed = record.get('argumentsPassed', '')
            parsed_args = parse_arguments_passed_field(arguments_passed)
            
            # Get action
            action = parsed_args.get('action', 'N/A')
            
            # Get cluster ARN (try both clusterArn and cluster_arn)
            cluster_arn = parsed_args.get('clusterArn') or parsed_args.get('cluster_arn', 'N/A')
            
            row = [formatted_timestamp, action, cluster_arn]
            
            # Add requested workflow model fields by parsing argumentsPassed
            for field in workflowModel:
                # Look for the field in parsed arguments
                if field in parsed_args:
                    value = parsed_args[field]
                else:
                    value = 'N/A'
                
                # Truncate very long values for display
                if isinstance(value, str) and len(value) > 100:
                    value = value[:97] + "..."
                
                row.append(str(value) if value else 'N/A')
            
            table_data.append(row)
        
        # Display table
        if table_data:
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
        else:
            print("No records found")
    
    # Display summary
    print(f"\nFizzyFaygo Audit Summary:")
    print(f"  Total records: {len(sorted_records)}")
    if sorted_records:
        print(f"  Time span: {format_timestamp(sorted_records[0].get('requestTimestamp', 0))} to {format_timestamp(sorted_records[-1].get('requestTimestamp', 0))}")
    
    # Show unique actions and cluster ARNs
    actions = set()
    cluster_arns = set()
    for record in sorted_records:
        arguments_passed = record.get('argumentsPassed', '')
        parsed_args = parse_arguments_passed_field(arguments_passed)
        
        action = parsed_args.get('action')
        if action and action != 'N/A':
            actions.add(action)
            
        cluster_arn = parsed_args.get('clusterArn') or parsed_args.get('cluster_arn')
        if cluster_arn and cluster_arn != 'N/A':
            cluster_arns.add(cluster_arn)
    
    if actions:
        print(f"  Unique actions: {', '.join(sorted(actions))}")
    
    if cluster_arns:
        print(f"  Unique cluster ARNs: {len(cluster_arns)}")
        for arn in sorted(cluster_arns):
            print(f"    {arn}")
    else:
        print(f"  No cluster ARNs found in records")


def extract_internal_ids_from_faygo_records(faygo_records: List[Dict]) -> List[str]:
    """
    Extract all internal_ids from FizzyFaygo records to know which pipelines to query.
    """
    internal_ids = set()
    
    for record in faygo_records:
        record_dict = dynamodb_to_dict(record)
        arguments_passed = record_dict.get('argumentsPassed', '')
        
        if arguments_passed:
            parsed_args = parse_arguments_passed_field(arguments_passed)
            if parsed_args.get('internal_id'):
                internal_ids.add(parsed_args.get('internal_id'))
    
    return list(internal_ids)


def main(pipeline_arn: str, region: Optional[str] = None, workflowModel: List[str] = None, 
         config_fields: List[str] = None, summary_only: bool = False):
    """
    Main function to orchestrate the audit timeline generation.
    """
    if workflowModel is None:
        workflowModel = []
    if config_fields is None:
        config_fields = []
        
    try:
        # 1. Parse pipeline ARN → get accountId and pipelineName
        arn_components = validate_and_extract_arn_components(pipeline_arn)
        
        # Use region from ARN if not provided
        if not region:
            region = arn_components['region']
        
        print(f"Analyzing pipeline: {arn_components['pipeline_name']}")
        print(f"Account: {arn_components['account_id']}")
        print(f"Region: {region}")
        
        # Get control plane session
        session = get_control_plane_session(region)
        dynamodb_client = session.client('dynamodb', region_name=region)
        
        # 2. Query FizzyFaygoAuditLog with "accountId|pipelineName" key
        print(f"Querying FizzyFaygoAuditLog for all records...")
        faygo_raw_items = query_fizzy_faygo_audit_table(
            arn_components['account_id'],
            arn_components['pipeline_name'],
            dynamodb_client
        )
        
        # 3. Extract all internal_ids from FizzyFaygo records to know which pipelines to query
        internal_ids = extract_internal_ids_from_faygo_records(faygo_raw_items)
        
        if not internal_ids:
            print("Warning: Could not extract any internal_ids from FizzyFaygoAuditLog records")
            print("Cannot proceed without internal_ids to query PipelineConfigurationAuditTable")
            return 1
        
        # 4. Query PipelineConfigurationAuditTable with all internal_ids
        print(f"Found internal_ids: {', '.join(internal_ids)}")
        print(f"Querying PipelineConfigurationAuditTable for all internal_ids...")
        
        all_config_records = []
        for internal_id in internal_ids:
            print(f"  Querying for internal_id: {internal_id}")
            config_raw_items = query_pipeline_configuration_audit_table(
                internal_id,
                dynamodb_client
            )
            print(f"  Found {len(config_raw_items)} records for {internal_id}")
            all_config_records.extend(config_raw_items)
        
        print(f"Total PipelineConfiguration records: {len(all_config_records)}")
        
        # 5. Process pipeline configuration records
        primary_audit_entries = process_pipeline_config_records(all_config_records)
        
        # 6. Display primary timeline first (source of truth)
        display_primary_audit_timeline(primary_audit_entries, pipeline_arn, config_fields, summary_only)
        
        # 7. Display FizzyFaygo audit records
        display_faygo_audit_records(faygo_raw_items, pipeline_arn, workflowModel, summary_only)
        
    except Exception as e:
        print(f"Error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    args = parse_arguments()
    exit_code = main(args.pipeline_arn, args.region, args.workflowModel, args.pipelineConfigurationAuditTable_field, args.summary)
    exit(exit_code)