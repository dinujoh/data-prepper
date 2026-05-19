#!/usr/bin/env python3
import boto3
import json
import csv
import subprocess
import sys
import argparse
import threading
import time
from typing import Dict, Any, Optional, List

ENV_VAR_NAME = "METERING_EVENT_BUS_ARN"
CONTAINER_NAME = "data-prepper-2-8"
EVENT_BUS_NAME = "FizzyDataPlaneCellECSEvents"

REGION_ACCOUNTS = {
    "ap-east-1": {"cp": "767000629049", "metering": "968641182439"},
    "ap-northeast-1": {"cp": "488939961373", "metering": "970690563945"},
    "ap-northeast-2": {"cp": "445771684395", "metering": "845721130457"},
    "ap-northeast-3": {"cp": "348165961838", "metering": "111315604593"},
    "ap-south-1": {"cp": "008739254458", "metering": "781312457882"},
    "ap-southeast-1": {"cp": "250245820245", "metering": "495600091677"},
    "ap-southeast-2": {"cp": "024043984225", "metering": "753671627822"},
    "ap-southeast-5": {"cp": "138114712285", "metering": "010659610977"},
    "ap-southeast-7": {"cp": "966376171602", "metering": "277519277715"},
    "ca-central-1": {"cp": "059841243330", "metering": "913721447047"},
    "eu-central-1": {"cp": "793214851599", "metering": "047378222093"},
    "eu-north-1": {"cp": "766161543942", "metering": "320355069084"},
    "eu-south-2": {"cp": "869935103098", "metering": "515966517369"},
    "eu-west-1": {"cp": "321843683841", "metering": "732090201319"},
    "eu-west-2": {"cp": "494103247145", "metering": "047062260915"},
    "eu-west-3": {"cp": "958100456306", "metering": "613047633052"},
    "sa-east-1": {"cp": "706976067050", "metering": "543736836406"},
    "us-east-1": {"cp": "650455509444", "metering": "807845704021"},
    "us-east-2": {"cp": "642133779026", "metering": "257203845144"},
    "us-west-1": {"cp": "110293142570", "metering": "897178461024"},
    "us-west-2": {"cp": "647705926003", "metering": "323102432571"},
}


class SessionManager:
    """Manages boto3 sessions with automatic credential refresh."""
    REFRESH_INTERVAL = 2700  # Refresh after 45 minutes (before 1hr expiry)

    def __init__(self):
        self._sessions = {}  # key: (account_id, role) -> (session, timestamp)

    def get_session(self, account_id: str, role: str, region: str):
        key = (account_id, role)
        now = time.time()
        if key in self._sessions:
            session, created_at = self._sessions[key]
            if now - created_at < self.REFRESH_INTERVAL:
                return session
            print(f"🔄 Refreshing {role} credentials for {account_id}...")

        session = self._assume(account_id, role, region)
        self._sessions[key] = (session, now)
        return session

    def _assume(self, account_id: str, role: str, region: str):
        print(f"🔐 Assuming {role} role in account {account_id}...")
        try:
            cmd = ['ada', 'credentials', 'update', f'--account={account_id}',
                   '--provider=isengard', f'--role={role}', '--profile=default', '--once']
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
            print(f"✅ Successfully assumed {role} role")
            return boto3.Session(region_name=region)
        except subprocess.TimeoutExpired:
            print(f"⏰ Ada command timed out")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to assume {role} role in {account_id}: {e.stderr}")
            sys.exit(1)


session_mgr = SessionManager()


def assume_role(account_id: str, role: str, region: str):
    """Get a boto3 session, refreshing credentials if needed."""
    return session_mgr.get_session(account_id, role, region)


def parse_pipeline_arn(pipeline_arn: str) -> tuple[str, str, str]:
    """Extract region, account ID and pipeline name from pipeline ARN."""
    parts = pipeline_arn.split(':')
    return parts[3], parts[4], parts[5].split('/')[-1]


def get_cell_account_id(dynamodb_client, pipeline_arn: str) -> Optional[str]:
    """Try PipelinePatchVersionMapping first, fallback to PipelineMapping."""
    try:
        response = dynamodb_client.get_item(
            TableName='PipelinePatchVersionMapping',
            Key={'pipelineArn': {'S': pipeline_arn}},
            ProjectionExpression='cellId'
        )
        if 'Item' in response and 'cellId' in response['Item']:
            return response['Item']['cellId']['S']
    except Exception:
        pass

    try:
        response = dynamodb_client.get_item(
            TableName='PipelineMapping',
            Key={'pipelineArn': {'S': pipeline_arn}}
        )
        if 'Item' in response:
            return response['Item']['clusterArn']['S'].split(':')[4]
    except Exception:
        pass

    return None


def get_pipeline_mapping(dynamodb_client, pipeline_arn: str) -> Optional[Dict[str, str]]:
    """Fetch deployment info from PipelineMapping table."""
    try:
        response = dynamodb_client.get_item(
            TableName='PipelineMapping',
            Key={'pipelineArn': {'S': pipeline_arn}}
        )
        if 'Item' not in response:
            return None
        item = response['Item']
        return {
            'deploymentGroupName': item['deploymentGroupName']['S'],
            'clusterArn': item['clusterArn']['S'],
            'ecsServiceArn': item['ecsServiceArn']['S']
        }
    except Exception as e:
        print(f"  Error fetching pipeline mapping: {e}")
        return None


def get_service_info(ecs_client, cluster_arn: str, service_arn: str) -> Optional[Dict[str, Any]]:
    """Get ECS service information."""
    try:
        response = ecs_client.describe_services(cluster=cluster_arn, services=[service_arn])
        if not response['services']:
            return None
        service = response['services'][0]
        return {
            'taskDefinition': service['taskDefinition'],
            'serviceName': service['serviceName'],
            'desiredCount': service['desiredCount']
        }
    except Exception as e:
        print(f"  Error describing service: {e}")
        return None


def create_new_task_definition_with_env_var(ecs_client, task_def_arn: str, region: str, metering_account_id: str) -> Optional[str]:
    """Create new task definition with METERING_EVENT_BUS_ARN added. Returns None if already present."""
    try:
        response = ecs_client.describe_task_definition(taskDefinition=task_def_arn)
        task_def = response['taskDefinition']

        # Check if env var already exists with correct value
        expected_value = f"arn:aws:events:{region}:{metering_account_id}:event-bus/{EVENT_BUS_NAME}"
        env_var_found = False
        for container in task_def['containerDefinitions']:
            if container['name'] == CONTAINER_NAME:
                for env_var in container.get('environment', []):
                    if env_var['name'] == ENV_VAR_NAME:
                        if env_var['value'] == expected_value:
                            print(f"  ⚠️  {ENV_VAR_NAME} already correct — skipping")
                            return None
                        print(f"  ⚠️  {ENV_VAR_NAME} has wrong value, replacing")
                        print(f"    Old: {env_var['value']}")
                        print(f"    New: {expected_value}")
                        env_var['value'] = expected_value
                        env_var_found = True
                        break
                break
        else:
            print(f"  ❌ Container '{CONTAINER_NAME}' not found")
            return None

        # Retrieve tags
        try:
            current_tags = ecs_client.list_tags_for_resource(resourceArn=task_def_arn).get('tags', [])
        except Exception:
            current_tags = []

        # Clean for re-registration
        for field in ['taskDefinitionArn', 'revision', 'status', 'requiresAttributes',
                      'compatibilities', 'registeredAt', 'registeredBy']:
            task_def.pop(field, None)

        # Add env var if not already replaced inline
        if not env_var_found:
            for container in task_def['containerDefinitions']:
                if container['name'] == CONTAINER_NAME:
                    container.setdefault('environment', []).append({
                        'name': ENV_VAR_NAME, 'value': expected_value
                    })
                    print(f"  Adding {ENV_VAR_NAME}={expected_value}")
                    break

        response = ecs_client.register_task_definition(**task_def)
        new_task_def_arn = response['taskDefinition']['taskDefinitionArn']
        print(f"  New task def: {new_task_def_arn}")

        if current_tags:
            try:
                ecs_client.tag_resource(resourceArn=new_task_def_arn, tags=current_tags)
            except Exception:
                pass

        return new_task_def_arn
    except Exception as e:
        print(f"  Error creating task definition: {e}")
        return None


def get_codedeploy_application(codedeploy_client) -> Optional[str]:
    """Get CodeDeploy application name starting with FizzyDrPepper."""
    try:
        for app_name in codedeploy_client.list_applications()['applications']:
            if app_name.startswith('FizzyDrPepper'):
                return app_name
    except Exception:
        pass
    return None


def trigger_codedeploy_deployment(codedeploy_client, application_name: str, deployment_group_name: str, new_task_def_arn: str) -> Optional[str]:
    """Trigger CodeDeploy deployment."""
    try:
        app_spec_content = {
            "version": 1,
            "Resources": [{"TargetService": {"Type": "AWS::ECS::Service", "Properties": {
                "TaskDefinition": new_task_def_arn,
                "LoadBalancerInfo": {"ContainerName": CONTAINER_NAME, "ContainerPort": 21890},
                "PlatformVersion": "1.4.0"
            }}}],
            "Hooks": []
        }
        response = codedeploy_client.create_deployment(
            applicationName=application_name,
            deploymentGroupName=deployment_group_name,
            revision={"revisionType": "AppSpecContent",
                      "appSpecContent": {"content": json.dumps(app_spec_content)}},
            deploymentConfigName="CodeDeployDefault.ECSAllAtOnce"
        )
        return response['deploymentId']
    except Exception as e:
        print(f"  Error creating deployment: {e}")
        return None


def update_ecs_service(ecs_client, cluster_arn: str, service_arn: str, desired_count: int):
    """Update ECS service configuration."""
    try:
        ecs_client.update_service(
            cluster=cluster_arn, service=service_arn,
            deploymentConfiguration={"maximumPercent": 200, "minimumHealthyPercent": 100, "strategy": "ROLLING"},
            desiredCount=desired_count, enableExecuteCommand=True,
            availabilityZoneRebalancing="ENABLED", placementConstraints=[], placementStrategy=[]
        )
    except Exception as e:
        print(f"  Error updating ECS service: {e}")


def update_billing_info(dynamodb_client, pipeline_arn: str, task_definition_arn: str):
    """Update task definition ARN in PipelineBillingInfo table."""
    try:
        dynamodb_client.update_item(
            TableName='PipelineBillingInfo',
            Key={'pipelineArn': {'S': pipeline_arn}},
            UpdateExpression='SET taskDefinitionArn = :val',
            ExpressionAttributeValues={':val': {'S': task_definition_arn}}
        )
        print(f"  ✅ Billing info updated")
    except Exception as e:
        print(f"  ❌ Error updating billing info: {e}")


def wait_for_deployment(codedeploy_client, deployment_id: str, poll_interval: int = 15, timeout: int = 900) -> str:
    """Poll CodeDeploy deployment until terminal state. Stops and rolls back on timeout."""
    elapsed = 0
    while elapsed < timeout:
        response = codedeploy_client.get_deployment(deploymentId=deployment_id)
        status = response['deploymentInfo']['status']
        if status in ('Succeeded', 'Failed', 'Stopped'):
            return status
        time.sleep(poll_interval)
        elapsed += poll_interval
    print(f"  ⏰ Deployment {deployment_id} timed out — stopping with rollback")
    try:
        codedeploy_client.stop_deployment(deploymentId=deployment_id, autoRollbackEnabled=True)
    except Exception as e:
        print(f"  Warning: Could not stop deployment: {e}")
    return "TimedOut"


_csv_lock = threading.Lock()


def update_csv_status(csv_file: str, pipeline_arn: str, status: str):
    """Update the status column for a pipeline in the CSV file."""
    with _csv_lock:
        rows = []
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                if row['pipeline_arn'] == pipeline_arn:
                    row['status'] = status
                rows.append(row)
        with open(csv_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def monitor_and_update_billing(pipeline_arn: str, codedeploy_client, deployment_id: str,
                                metering_account: str, region: str, new_task_def_arn: str,
                                csv_file: str = None):
    """Monitor deployment; update billing only on success."""
    status = wait_for_deployment(codedeploy_client, deployment_id)
    prefix = f"[{pipeline_arn.split('/')[-1]}]"

    if status == "Succeeded":
        print(f"{prefix} ✅ Deployment {deployment_id} succeeded — updating billing")
        metering_session = assume_role(metering_account, "Admin", region)
        metering_dynamodb = metering_session.client('dynamodb')
        update_billing_info(metering_dynamodb, pipeline_arn, new_task_def_arn)
        if csv_file:
            update_csv_status(csv_file, pipeline_arn, "succeeded")
    else:
        print(f"{prefix} ❌ Deployment {deployment_id} status: {status} — skipping billing update")
        if csv_file:
            update_csv_status(csv_file, pipeline_arn, "failed")


def process_pipeline(pipeline_arn: str, region: str, cp_dynamodb,
                     metering_account: str, csv_file: str = None) -> Optional[threading.Thread]:
    """Deploy env var for a single pipeline. Returns a monitor thread if deployment triggered."""
    _, account_id, pipeline_name = parse_pipeline_arn(pipeline_arn)
    prefix = f"[{pipeline_name}]"
    print(f"\n{prefix} Processing {pipeline_arn}")

    # Get cell account
    cell_account = get_cell_account_id(cp_dynamodb, pipeline_arn)
    if not cell_account:
        print(f"{prefix} ❌ Could not determine cell account — skipping")
        return None

    # Get pipeline mapping
    pipeline_mapping = get_pipeline_mapping(cp_dynamodb, pipeline_arn)
    if not pipeline_mapping:
        print(f"{prefix} ❌ Pipeline mapping not found — skipping")
        return None

    # Get or create DP session for this cell account
    dp_session = assume_role(cell_account, "Admin", region)
    dp_ecs = dp_session.client('ecs')
    dp_codedeploy = dp_session.client('codedeploy')

    # Get service info
    service_info = get_service_info(dp_ecs, pipeline_mapping['clusterArn'], pipeline_mapping['ecsServiceArn'])
    if not service_info:
        print(f"{prefix} ❌ Could not describe ECS service — skipping")
        return None

    # Create new task definition
    new_task_def_arn = create_new_task_definition_with_env_var(
        dp_ecs, service_info['taskDefinition'], region, metering_account
    )
    if not new_task_def_arn:
        return None

    # Deploy
    application_name = get_codedeploy_application(dp_codedeploy)
    if not application_name:
        print(f"{prefix} ❌ CodeDeploy application not found — skipping")
        return None

    deployment_id = trigger_codedeploy_deployment(
        dp_codedeploy, application_name, pipeline_mapping['deploymentGroupName'], new_task_def_arn
    )
    if not deployment_id:
        return None

    print(f"{prefix} 🚀 Deployment: {deployment_id}")

    # Update ECS service config
    update_ecs_service(dp_ecs, pipeline_mapping['clusterArn'],
                       pipeline_mapping['ecsServiceArn'], service_info['desiredCount'])

    # Return monitor thread (not started)
    return threading.Thread(
        target=monitor_and_update_billing,
        args=(pipeline_arn, dp_codedeploy, deployment_id, metering_account, region, new_task_def_arn, csv_file),
        daemon=True
    )


def verify_pipeline(pipeline_arn: str, region: str, cp_dynamodb,
                    metering_account: str, metering_dynamodb) -> dict:
    """Verify a pipeline has the env var and correct task def in billing table."""
    _, account_id, pipeline_name = parse_pipeline_arn(pipeline_arn)
    prefix = f"[{pipeline_name}]"
    result = {'env_var': False, 'billing_match': False}

    # Get cell account and pipeline mapping
    cell_account = get_cell_account_id(cp_dynamodb, pipeline_arn)
    if not cell_account:
        print(f"{prefix} ❌ Could not determine cell account")
        return result

    pipeline_mapping = get_pipeline_mapping(cp_dynamodb, pipeline_arn)
    if not pipeline_mapping:
        print(f"{prefix} ❌ Pipeline mapping not found")
        return result

    # Get DP session
    dp_ecs = assume_role(cell_account, "Admin", region).client('ecs')

    # Get current running task definition from service
    service_info = get_service_info(dp_ecs, pipeline_mapping['clusterArn'], pipeline_mapping['ecsServiceArn'])
    if not service_info:
        print(f"{prefix} ❌ Could not describe ECS service")
        return result

    current_task_def = service_info['taskDefinition']

    # Check env var on running task def
    try:
        response = dp_ecs.describe_task_definition(taskDefinition=current_task_def)
        task_def = response['taskDefinition']
        expected_value = f"arn:aws:events:{region}:{REGION_ACCOUNTS[region]['metering']}:event-bus/{EVENT_BUS_NAME}"
        for container in task_def['containerDefinitions']:
            if container['name'] == CONTAINER_NAME:
                for env_var in container.get('environment', []):
                    if env_var['name'] == ENV_VAR_NAME:
                        if env_var['value'] == expected_value:
                            result['env_var'] = True
                        else:
                            print(f"{prefix} ⚠️  Env var has wrong value: {env_var['value']}")
                        break
                break
    except Exception as e:
        print(f"{prefix} ❌ Could not describe task definition: {e}")
        return result

    # Check billing table has the current task def
    try:
        response = metering_dynamodb.get_item(
            TableName='PipelineBillingInfo',
            Key={'pipelineArn': {'S': pipeline_arn}},
            ProjectionExpression='taskDefinitionArn'
        )
        if 'Item' in response:
            billing_task_def = response['Item']['taskDefinitionArn']['S']
            result['billing_match'] = (billing_task_def == current_task_def)
            if not result['billing_match'] and result['env_var']:
                print(f"{prefix} ⚠️  Billing mismatch — updating to running task def")
                print(f"    Running:  {current_task_def}")
                print(f"    Billing:  {billing_task_def}")
                update_billing_info(metering_dynamodb, pipeline_arn, current_task_def)
                result['billing_match'] = True
            elif not result['billing_match']:
                print(f"{prefix} ⚠️  Billing mismatch (env var missing, not updating):")
                print(f"    Running:  {current_task_def}")
                print(f"    Billing:  {billing_task_def}")
        else:
            print(f"{prefix} ⚠️  No entry in PipelineBillingInfo")
    except Exception as e:
        print(f"{prefix} ❌ Could not query billing table: {e}")

    status = "✅" if (result['env_var'] and result['billing_match']) else "❌"
    print(f"{prefix} {status} env_var={'✅' if result['env_var'] else '❌'}  billing={'✅' if result['billing_match'] else '❌'}")
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Add METERING_EVENT_BUS_ARN env var to pipeline ECS task definitions.\n\n'
                    'Accepts a CSV file with columns: region,cell_account,pipeline_arn,status\n'
                    'Use --lines to specify which rows to process (1-indexed, excluding header).',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--pipeline-arns', nargs='+', help='One or more pipeline ARNs (same region)')
    input_group.add_argument('--csv-file', help='CSV file with columns: region,cell_account,pipeline_arn,status')
    parser.add_argument('--lines', help='Line range to process from CSV (e.g. "6-10" or "3,5,7" — 1-indexed, excludes header)')
    parser.add_argument('--region', help='AWS region (required with --pipeline-arns, inferred from CSV)')
    parser.add_argument('--cp-account', help='Control Plane account ID (required with --pipeline-arns, inferred from CSV)')
    parser.add_argument('--metering-account', help='Metering account ID (required with --pipeline-arns, inferred from CSV)')
    parser.add_argument('--no-wait', action='store_true', help='Skip deployment monitoring and billing update')
    parser.add_argument('--verify', action='store_true', help='Verify only — check env var and billing table without deploying')
    parser.add_argument('--rerun-succeeded', action='store_true', help='Only process rows with status=succeeded (for fixing env var values)')
    parser.add_argument('--rerun-failed', action='store_true', help='Only process rows with status=failed')
    parser.add_argument('--batch-size', type=int, help='Process pipelines in batches of this size, waiting for each batch to complete before starting the next')

    args = parser.parse_args()

    if args.csv_file:
        with open(args.csv_file) as f:
            reader = csv.DictReader(f)
            all_rows = list(reader)

        # Parse --lines
        if args.lines:
            indices = set()
            for part in args.lines.split(','):
                if '-' in part:
                    start, end = part.split('-')
                    indices.update(range(int(start) - 1, int(end)))
                else:
                    indices.add(int(part) - 1)
            rows = [all_rows[i] for i in sorted(indices) if i < len(all_rows)]
        elif args.verify:
            # Verify mode: check rows that already have a status
            rows = [r for r in all_rows if r.get('status')]
        elif args.rerun_succeeded:
            # Rerun only succeeded rows (e.g., to fix env var values)
            rows = [r for r in all_rows if r.get('status') == 'succeeded']
        elif args.rerun_failed:
            # Rerun all non-succeeded rows
            rows = [r for r in all_rows if r.get('status') != 'succeeded']
        else:
            # Deploy mode: process only rows without a status
            rows = [r for r in all_rows if not r.get('status')]

        if not rows:
            print("No pipelines to process (all have a status or lines out of range)")
            return

        region = rows[0]['region']
        pipeline_arns = [r['pipeline_arn'] for r in rows]

        accounts = REGION_ACCOUNTS.get(region)
        if not accounts:
            print(f"❌ No account config for region {region}")
            sys.exit(1)

        cp_account = accounts['cp']
        metering_account = accounts['metering']
        csv_file = args.csv_file

        print(f"CSV: {args.csv_file} | Lines: {len(pipeline_arns)} pipelines")
        print(f"Region: {region} | CP: {cp_account} | Metering: {metering_account}")
    else:
        if not args.region or not args.cp_account or not args.metering_account:
            parser.error("--region, --cp-account, and --metering-account are required with --pipeline-arns")
        pipeline_arns = args.pipeline_arns
        region = args.region
        cp_account = args.cp_account
        metering_account = args.metering_account
        csv_file = None

    # Step 1: Access CP
    print("\n=== Accessing Control Plane ===")
    cp_session = assume_role(cp_account, "ReadOnly", region)
    cp_dynamodb = cp_session.client('dynamodb')

    if args.verify:
        # Verify mode: check env var and billing table
        print(f"\n=== Verify Mode: checking {len(pipeline_arns)} pipelines ===")
        metering_session = assume_role(metering_account, "Admin", region)
        metering_dynamodb = metering_session.client('dynamodb')
        results = []
        passed = 0
        for pipeline_arn in pipeline_arns:
            result = verify_pipeline(pipeline_arn, region, cp_dynamodb, metering_account, metering_dynamodb)
            results.append({'pipeline_arn': pipeline_arn, 'env_var': result['env_var'], 'billing_match': result['billing_match']})
            if result['env_var'] and result['billing_match']:
                passed += 1

        output_file = f"verification_{region}.csv"
        with open(output_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['pipeline_arn', 'env_var', 'billing_match'])
            writer.writeheader()
            writer.writerows(results)

        failed = [r for r in results if not (r['env_var'] and r['billing_match'])]
        failed_file = f"verification_{region}_failed.csv"
        with open(failed_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['pipeline_arn', 'env_var', 'billing_match'])
            writer.writeheader()
            writer.writerows(failed)

        print(f"\n=== Verification: {passed}/{len(pipeline_arns)} fully correct ===")
        print(f"Results written to {output_file}")
        print(f"Failures written to {failed_file} ({len(failed)} pipelines)")
        return

    # Step 2: Process pipelines
    batch_size = args.batch_size or len(pipeline_arns)

    for batch_start in range(0, len(pipeline_arns), batch_size):
        batch = pipeline_arns[batch_start:batch_start + batch_size]
        batch_num = (batch_start // batch_size) + 1
        total_batches = (len(pipeline_arns) + batch_size - 1) // batch_size

        if args.batch_size:
            print(f"\n=== Batch {batch_num}/{total_batches} ({len(batch)} pipelines) ===")

        monitor_threads: List[threading.Thread] = []
        for pipeline_arn in batch:
            thread = process_pipeline(pipeline_arn, region, cp_dynamodb, metering_account, csv_file)
            if thread:
                monitor_threads.append(thread)

        print(f"\n=== Deployments triggered: {len(monitor_threads)} pipelines ===")

        if args.no_wait:
            print("--no-wait: skipping monitoring")
            continue

        if monitor_threads:
            print(f"=== Monitoring {len(monitor_threads)} deployments (billing updates on success) ===")
            for t in monitor_threads:
                t.start()
            for t in monitor_threads:
                t.join()

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
