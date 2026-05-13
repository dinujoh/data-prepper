#!/usr/bin/env python3
"""
Cleanup script for when add_metering_event_bus_env_var.py fails mid-run due to throttling.

For each pipeline without a status in the CSV:
- Checks the ECS service's current deployment state
- If CodeDeploy deployment is still in progress: stops it with rollback, marks as 'failed'
- If the running task def has the env var: marks as 'succeeded'
- Otherwise: marks as 'failed'

Usage:
    python3 cleanup_stuck_deployments.py --csv-file per_region/ap-northeast-1.csv
"""
import boto3
import csv
import subprocess
import sys
import argparse
import time

ENV_VAR_NAME = "METERING_EVENT_BUS_ARN"
CONTAINER_NAME = "data-prepper-2-8"

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


def assume_role(account_id, role, region):
    print(f"🔐 Assuming {role} in {account_id}...")
    try:
        subprocess.run(
            ['ada', 'credentials', 'update', f'--account={account_id}',
             '--provider=isengard', f'--role={role}', '--profile=default', '--once'],
            capture_output=True, text=True, check=True, timeout=60
        )
        return boto3.Session(region_name=region)
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)


def update_csv_status(csv_file, pipeline_arn, status):
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


def main():
    parser = argparse.ArgumentParser(description='Cleanup stuck deployments and update CSV statuses')
    parser.add_argument('--csv-file', required=True, help='CSV file with columns: region,cell_account,pipeline_arn,status')
    parser.add_argument('--lines', help='Line range (e.g. "6-10" or "3,5,7")')
    args = parser.parse_args()

    with open(args.csv_file) as f:
        all_rows = list(csv.DictReader(f))

    if args.lines:
        indices = set()
        for part in args.lines.split(','):
            if '-' in part:
                s, e = part.split('-')
                indices.update(range(int(s) - 1, int(e)))
            else:
                indices.add(int(part) - 1)
        rows = [all_rows[i] for i in sorted(indices) if i < len(all_rows)]
    else:
        rows = [r for r in all_rows if not r.get('status')]

    if not rows:
        print("No pipelines to clean up")
        return

    region = rows[0]['region']
    accounts = REGION_ACCOUNTS.get(region)
    if not accounts:
        print(f"❌ No config for region {region}")
        sys.exit(1)

    # Access CP
    cp_session = assume_role(accounts['cp'], "ReadOnly", region)
    cp_dynamodb = cp_session.client('dynamodb')

    # Group by cell account
    by_cell = {}
    for row in rows:
        by_cell.setdefault(row['cell_account'], []).append(row['pipeline_arn'])

    dp_sessions = {}
    succeeded = 0
    failed = 0

    for cell_account, pipeline_arns in by_cell.items():
        if cell_account not in dp_sessions:
            dp_sessions[cell_account] = assume_role(cell_account, "Admin", region)
        session = dp_sessions[cell_account]
        ecs = session.client('ecs')
        codedeploy = session.client('codedeploy')

        for pipeline_arn in pipeline_arns:
            name = pipeline_arn.split('/')[-1]
            prefix = f"[{name}]"

            # Get pipeline mapping
            try:
                resp = cp_dynamodb.get_item(
                    TableName='PipelineMapping',
                    Key={'pipelineArn': {'S': pipeline_arn}}
                )
                if 'Item' not in resp:
                    print(f"{prefix} ❌ No pipeline mapping — marking failed")
                    update_csv_status(args.csv_file, pipeline_arn, "failed")
                    failed += 1
                    continue
                mapping = resp['Item']
                cluster_arn = mapping['clusterArn']['S']
                service_arn = mapping['ecsServiceArn']['S']
                dg_name = mapping['deploymentGroupName']['S']
            except Exception as e:
                print(f"{prefix} ❌ Error getting mapping: {e}")
                update_csv_status(args.csv_file, pipeline_arn, "failed")
                failed += 1
                continue

            # Check for in-progress deployments on the deployment group
            try:
                # Find the CodeDeploy app
                apps = codedeploy.list_applications()['applications']
                app_name = next((a for a in apps if a.startswith('FizzyDrPepper')), None)

                if app_name:
                    deployments = codedeploy.list_deployments(
                        applicationName=app_name,
                        deploymentGroupName=dg_name,
                        includeOnlyStatuses=['InProgress', 'Queued', 'Ready']
                    ).get('deployments', [])

                    for dep_id in deployments:
                        print(f"{prefix} ⏹️  Stopping deployment {dep_id}")
                        try:
                            codedeploy.stop_deployment(deploymentId=dep_id, autoRollbackEnabled=True)
                        except Exception as e:
                            print(f"{prefix}   Warning stopping {dep_id}: {e}")
                        time.sleep(1)
            except Exception as e:
                print(f"{prefix} Warning checking deployments: {e}")

            # Check if the running task def has the env var
            try:
                svc_resp = ecs.describe_services(cluster=cluster_arn, services=[service_arn])
                if not svc_resp['services']:
                    print(f"{prefix} ❌ Service not found")
                    update_csv_status(args.csv_file, pipeline_arn, "failed")
                    failed += 1
                    continue

                task_def_arn = svc_resp['services'][0]['taskDefinition']
                td_resp = ecs.describe_task_definition(taskDefinition=task_def_arn)

                has_env_var = False
                for container in td_resp['taskDefinition']['containerDefinitions']:
                    if container['name'] == CONTAINER_NAME:
                        for env in container.get('environment', []):
                            if env['name'] == ENV_VAR_NAME:
                                has_env_var = True
                                break
                        break

                if has_env_var:
                    print(f"{prefix} ✅ Env var present — succeeded")
                    update_csv_status(args.csv_file, pipeline_arn, "succeeded")
                    succeeded += 1
                else:
                    print(f"{prefix} ❌ Env var missing — failed")
                    update_csv_status(args.csv_file, pipeline_arn, "failed")
                    failed += 1
            except Exception as e:
                print(f"{prefix} ❌ Error checking task def: {e}")
                update_csv_status(args.csv_file, pipeline_arn, "failed")
                failed += 1

    print(f"\n=== Cleanup complete: {succeeded} succeeded, {failed} failed ===")


if __name__ == "__main__":
    main()
