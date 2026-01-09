#!/usr/bin/env python3
import boto3
import json
import subprocess
import sys
import argparse
from typing import Dict, Any, Optional

###Specify the New Data Prepper Tag
new_tag = "2.x.940"

def assume_role(account_id: str, role: str, region: str):
    """Use ada to assume role in target account."""
    import subprocess

    print(f"🔐 Assuming {role} role in account {account_id}...")

    try:
        cmd = ['ada', 'credentials', 'update', f'--account={account_id}',
               '--provider=isengard', f'--role={role}', '--profile=default', '--once']
        print(f"🔄 Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60
        )

        print(f"✅ Successfully assumed {role} role")
        if result.stdout:
            print(f"Ada output: {result.stdout}")

        # Return a session using the default credential chain
        return boto3.Session(region_name=region)

    except subprocess.TimeoutExpired:
        print(f"⏰ Ada command timed out after 60 seconds")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to assume {role} role using ada: {e}")
        print(f"Error output: {e.stderr}")
        sys.exit(1)


def parse_pipeline_arn(pipeline_arn: str) -> tuple[str, str]:
    """Extract account ID and pipeline name from pipeline ARN"""
    # arn:aws:osis:region:account-id:pipeline/pipeline-name
    parts = pipeline_arn.split(':')
    account_id = parts[4]
    pipeline_name = parts[5].split('/')[-1]
    return account_id, pipeline_name

def get_pipeline_config(dynamodb_client, account_id: str, pipeline_name: str) -> Dict[str, Any]:
    """Fetch pipeline configuration from DataPrepperPipelineConfigurations table"""
    try:
        response = dynamodb_client.get_item(
            TableName='DataPrepperPipelineConfigurations',
            Key={
                'accountId': {'S': account_id},
                'pipelineName': {'S': pipeline_name}
            }
        )

        if 'Item' not in response:
            raise Exception(f"Pipeline {account_id}:{pipeline_name} not found")

        item = response['Item']
        start_count = int(item.get('startCount', {'N': '0'})['N'])

        return {'startCount': start_count}
    except Exception as e:
        print(f"Error fetching pipeline config: {e}")
        sys.exit(1)

def get_pipeline_mapping(dynamodb_client, pipeline_arn: str) -> Dict[str, str]:
    """Fetch deployment info from PipelineMapping table"""
    try:
        response = dynamodb_client.get_item(
            TableName='PipelineMapping',
            Key={'pipelineArn': {'S': pipeline_arn}}
        )

        if 'Item' not in response:
            raise Exception(f"Pipeline mapping for {pipeline_arn} not found")

        item = response['Item']
        return {
            'deploymentGroupName': item['deploymentGroupName']['S'],
            'clusterArn': item['clusterArn']['S'],
            'ecsServiceArn': item['ecsServiceArn']['S']
        }
    except Exception as e:
        print(f"Error fetching pipeline mapping: {e}")
        sys.exit(1)

def get_service_info(ecs_client, cluster_arn: str, service_arn: str) -> Dict[str, Any]:
    """Get ECS service information"""
    try:
        response = ecs_client.describe_services(
            cluster=cluster_arn,
            services=[service_arn]
        )

        if not response['services']:
            raise Exception(f"Service {service_arn} not found")

        service = response['services'][0]
        return {
            'taskDefinition': service['taskDefinition'],
            'serviceName': service['serviceName'],
            'desiredCount': service['desiredCount']
        }
    except Exception as e:
        print(f"Error describing service: {e}")
        sys.exit(1)

def create_new_task_definition(ecs_client, task_def_arn: str, new_start_count: str) -> str:
    """Create new task definition with updated SOURCE_COORDINATION_PIPELINE_IDENTIFIER"""
    try:
        # Get current task definition
        response = ecs_client.describe_task_definition(taskDefinition=task_def_arn)
        task_def = response['taskDefinition']
        current_revision = task_def['revision']
        print(f"Current task definition revision: {current_revision}")

        print("\nStep 1: Retrieving tags from current task definition...")
        try:
            tags_response = ecs_client.list_tags_for_resource(resourceArn=task_def_arn)
            current_tags = tags_response.get('tags', [])
            if current_tags:
                print(f"Found {len(current_tags)} tags to copy:")
                for tag in current_tags:
                    print(f"  - {tag['key']}: {tag['value']}")
            else:
                print("No tags found on current task definition")
        except Exception as e:
            print(f"Warning: Could not retrieve tags: {e}")
            current_tags = []

        # Step 2: Remove fields that can't be registered
        print("\nStep 2: Cleaning task definition...")
        fields_to_remove = [
            'taskDefinitionArn', 'revision', 'status', 'requiresAttributes',
            'compatibilities', 'registeredAt', 'registeredBy'
        ]
        for field in fields_to_remove:
            task_def.pop(field, None)

        # Update environment variable
        for container in task_def['containerDefinitions']:
            if container['name'] == 'data-prepper-2-8':
                old_image_value = container['image']
                image_parts = old_image_value.split(':')
                if len(image_parts) > 1:
                    new_image_uri = f"{image_parts[0]}:{new_tag}"
                    container['image'] = new_image_uri
                    print(f"Updated image from {old_image_value} to {new_image_uri}")
                if 'environment' in container:
                    for env_var in container['environment']:
                        if env_var['name'] == 'SOURCE_COORDINATION_PIPELINE_IDENTIFIER':
                            current_value = env_var['value']
                            print(f"Current SOURCE_COORDINATION_PIPELINE_IDENTIFIER: {current_value}")
                            env_var['value'] = f"{current_value}:{new_start_count}"
                            print(f"Updated SOURCE_COORDINATION_PIPELINE_IDENTIFIER to: {current_value}:{new_start_count}")
                            break

        # Register new task definition
        response = ecs_client.register_task_definition(**task_def)
        new_task_def_arn = response['taskDefinition']['taskDefinitionArn']
        new_revision = response['taskDefinition']['revision']
        print(f"Created new task definition: {new_task_def_arn}")
        print(f"New Revision: {new_revision}")

        if current_tags:
            print("\nStep 5: Copying tags to new task definition...")
            try:
                ecs_client.tag_resource(
                    resourceArn=new_task_def_arn,
                    tags=current_tags
                )
                print(f"Successfully copied {len(current_tags)} tags to new revision")
            except Exception as e:
                print(f"Warning: Could not copy tags to new task definition: {e}")
                print("New task definition created but tags were not copied")
        else:
            print("\nStep 5: No tags to copy")

        return new_task_def_arn
    except Exception as e:
        print(f"Error creating new task definition: {e}")
        sys.exit(1)

def get_codedeploy_application(codedeploy_client) -> str:
    """Get CodeDeploy application name starting with FizzyDrPepper"""
    try:
        response = codedeploy_client.list_applications()
        for app_name in response['applications']:
            if app_name.startswith('FizzyDrPepper'):
                print(f"Found CodeDeploy application: {app_name}")
                return app_name

        raise Exception("No CodeDeploy application found starting with 'FizzyDrPepper'")
    except Exception as e:
        print(f"Error finding CodeDeploy application: {e}")
        sys.exit(1)

def trigger_codedeploy_deployment(codedeploy_client, application_name: str, deployment_group_name: str, new_task_def_arn: str) -> str:
    """Trigger CodeDeploy deployment with new task definition"""
    try:
        app_spec_content = {
            "version": 1,
            "Resources": [{
                "TargetService": {
                    "Type": "AWS::ECS::Service",
                    "Properties": {
                        "TaskDefinition": new_task_def_arn,
                        "LoadBalancerInfo": {
                            "ContainerName": "data-prepper-2-8",
                            "ContainerPort": 21890
                        },
                        "PlatformVersion": "1.4.0"
                    }
                }
            }],
            "Hooks": []
        }

        response = codedeploy_client.create_deployment(
            applicationName=application_name,
            deploymentGroupName=deployment_group_name,
            revision={
                "revisionType": "AppSpecContent",
                "appSpecContent": {
                    "content": json.dumps(app_spec_content)
                }
            },
            deploymentConfigName="CodeDeployDefault.ECSAllAtOnce"
        )

        deployment_id = response['deploymentId']
        print(f"Started CodeDeploy deployment: {deployment_id}")
        return deployment_id

    except Exception as e:
        print(f"Error creating CodeDeploy deployment: {e}")
        sys.exit(1)

def update_ecs_service(ecs_client, cluster_arn: str, service_arn: str, desired_count: int):
    """Update ECS service configuration after CodeDeploy deployment"""
    try:
        response = ecs_client.update_service(
            cluster=cluster_arn,
            service=service_arn,
            deploymentConfiguration={
                "maximumPercent": 200,
                "minimumHealthyPercent": 100,
                "strategy": "ROLLING"
            },
            desiredCount=desired_count,
            enableExecuteCommand=True,
            availabilityZoneRebalancing="ENABLED",
            placementConstraints=[],
            placementStrategy=[]
        )
        print(f"Updated ECS service configuration: {service_arn}")
        return response

    except Exception as e:
        print(f"Error updating ECS service: {e}")
        sys.exit(1)

def update_start_count(dynamodb_client, account_id: str, pipeline_name: str, new_start_count: int) -> None:
    """Update start count in DataPrepperPipelineConfigurations table"""
    try:
        dynamodb_client.update_item(
            TableName='DataPrepperPipelineConfigurations',
            Key={
                'accountId': {'S': account_id},
                'pipelineName': {'S': pipeline_name}
            },
            UpdateExpression='SET startCount = :val',
            ExpressionAttributeValues={
                ':val': {'N': str(new_start_count)}
            }
        )
        print(f"Updated start count to {new_start_count} for pipeline {account_id}:{pipeline_name}")
    except Exception as e:
        print(f"Error updating start count: {e}")
        sys.exit(1)

def update_billing_info(dynamodb_client, pipeline_arn: str, task_definition_arn: str) -> None:
    """Update task definition ARN in PipelineBillingInfo table"""
    try:
        dynamodb_client.update_item(
            TableName='PipelineBillingInfo',
            Key={'pipelineArn': {'S': pipeline_arn}},
            UpdateExpression='SET taskDefinitionArn = :val',
            ExpressionAttributeValues={
                ':val': {'S': task_definition_arn}
            }
        )
        print(f"Updated task definition ARN in billing info for pipeline {pipeline_arn}")
    except Exception as e:
        print(f"Error updating billing info: {e}")
        sys.exit(1)

def main():
    #
    # Usage: python force_update_pipeline_ecs_service.py --pipeline-arn <pipeline_arn> --region <region> --cp-account <cp_account> --dp-account <dp_account> --metering-account <metering_account>
    #

    parser = argparse.ArgumentParser(description='Update OSIS pipeline')
    parser.add_argument('--pipeline-arn', required=True, help='Pipeline ARN')
    parser.add_argument('--region', required=True, help='AWS region')
    parser.add_argument('--cp-account', required=True, help='Control Plane account ID')
    parser.add_argument('--dp-account', required=True, help='Data Plane account ID')
    parser.add_argument('--metering-account', required=True, help='Metering account ID')

    args = parser.parse_args()

    # Parse pipeline ARN
    account_id, pipeline_name = parse_pipeline_arn(args.pipeline_arn)
    print(f"Pipeline: {account_id}:{pipeline_name}")

    # Step 1: Ada into CP account
    print("\n=== Step 1: Accessing Control Plane ===")
    cp_session = assume_role(args.cp_account, "ReadOnly", args.region)
    # Create CP clients
    cp_dynamodb = cp_session.client('dynamodb')

    # Get pipeline configuration
    pipeline_config = get_pipeline_config(cp_dynamodb, account_id, pipeline_name)
    current_start_count = pipeline_config['startCount']
    new_start_count = current_start_count + 1

    print(f"Current start count: {current_start_count}")
    print(f"New start count: {new_start_count}")

    # Get pipeline mapping
    pipeline_mapping = get_pipeline_mapping(cp_dynamodb, args.pipeline_arn)
    print(f"Deployment group: {pipeline_mapping['deploymentGroupName']}")
    cluster_arn = pipeline_mapping['clusterArn']
    ecsService_arn = pipeline_mapping['ecsServiceArn']
    print(f"Cluster ARN: {pipeline_mapping['clusterArn']}")
    print(f"Service ARN: {pipeline_mapping['ecsServiceArn']}")

    # Step 2: Ada into DP account
    print("\n=== Step 2: Accessing Data Plane ===")
    dp_session = assume_role(args.dp_account, "Admin", args.region)
    dp_ecs = dp_session.client('ecs')
    dp_codedeploy = dp_session.client('codedeploy')

    # Get CodeDeploy application name
    application_name = get_codedeploy_application(dp_codedeploy)
    # Get service info
    service_info = get_service_info(dp_ecs, cluster_arn, ecsService_arn)

    # Create new task definition
    new_task_def_arn = create_new_task_definition(dp_ecs, service_info['taskDefinition'],
                                                  new_start_count)
    deployment_id = trigger_codedeploy_deployment(
        dp_codedeploy,
        application_name,
        pipeline_mapping['deploymentGroupName'],
        new_task_def_arn
    )
    # Update ECS service
    update_ecs_service(dp_ecs, cluster_arn, ecsService_arn, service_info['desiredCount'])

    # Step 3: Go back to CP account and update start count
    print("\n=== Step 3: Updating Control Plane ===")
    cp_admin_session = assume_role(args.cp_account, "Admin", args.region)
    cp_dynamodb_admin = cp_admin_session.client('dynamodb')
    update_start_count(cp_dynamodb_admin, account_id, pipeline_name, new_start_count)

    print("\n=== Update Complete ===")
    print(f"Successfully updated pipeline {args.pipeline_arn}")
    print(f"Start count updated from {current_start_count} to {new_start_count}")

    # Step 4: Ada into metering account and update billing info
    print("\n=== Step 4: Updating Metering Account ===")
    metering_admin_session = assume_role(args.metering_account, "Admin", args.region)

    metering_dynamodb = metering_admin_session.client('dynamodb')
    update_billing_info(metering_dynamodb, args.pipeline_arn, new_task_def_arn)

    print("\n=== Update Complete ===")
    print(f"Successfully updated pipeline {args.pipeline_arn}")
    print(f"Start count updated from {current_start_count} to {new_start_count}")
    print(f"Task definition ARN updated in billing info: {new_task_def_arn}")

if __name__ == "__main__":
    main()
