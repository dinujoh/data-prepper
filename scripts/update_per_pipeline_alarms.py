import boto3
import json
import argparse
import time

PIPELINE_CONFIG_TABLE = "DataPrepperPipelineConfigurations"
PIPELINE_MAPPING_TABLE = "PipelineMapping"
SETUP_MONITORING = "SetupMonitoring"
CLEANUP_MONITORING = "CleanupMonitoring"

"""
WARNING: THIS SCRIPT SHOULD ONLY BE USED IN PRODUCTION WITH AN MCM. EXERCISE CAUTION WHEN RUNNING IT.

This script can be used to delete and recreate/create per pipeline alarms for every active pipeline in a region.
This is useful when a bug is found in the alarms and the alarms need to be recreated or when a new alarm is added and 
we want to apply it to existing pipelines.

1. Copy credentials for the control plane account
2. Run the script: python3 update_per_pipeline_alarms.py --region R --verbose --wait-time N

This script can also be used in to delete all per pipeline alarms. 
This should only be done in extreme cases where there were issues with the alarm configuration.

1. Copy credentials for the control plane account
2. Run the script: python3 update_per_pipeline_alarms.py --delete-alarms --region R --verbose --wait-time N

Future extensions:
- Add support for creating/deleting alarms for specific pipelines
- Add support for creating/deleting alarms for pipelines that were created/updated before/after an inputted timestamp
"""


def main(region, delete_alarms, verbose, wait_time):

    lambda_client = boto3.client('lambda', region_name=region)
    dynamodb_client = boto3.client('dynamodb', region_name=region)

    lambda_name = get_alarm_management_lambda(lambda_client, delete_alarms)
    print(f"Found lambda: {lambda_name}")

    active_pipelines = get_active_pipelines(dynamodb_client)

    if verbose:
        print(f"Found {len(active_pipelines)} active pipelines")

    pipeline_table_entries = []
    for pipeline in active_pipelines:
        mapping_items = get_pipeline_mappings(dynamodb_client, pipeline['pipelineArn']['S'])
        if mapping_items:
            pipeline_table_entries.append((pipeline, mapping_items[0]))
        else:
            print(f"No mapping entry found for {pipeline['pipelineArn']['S']}")

    lambda_inputs = [build_lambda_input(pipeline_configuration, pipeline_mapping, delete_alarms)
                     for pipeline_configuration, pipeline_mapping in pipeline_table_entries]

    for lambda_input in lambda_inputs:
        if verbose:
            print(f"Invoking lambda: {lambda_name} with input: {lambda_input}")

        invoke_setup_monitoring_lambda(lambda_client, lambda_name, lambda_input)
        time.sleep(wait_time)


def get_alarm_management_lambda(lambda_client, delete_alarms):
    lambda_search_name = SETUP_MONITORING if not delete_alarms else CLEANUP_MONITORING

    list_functions_paginator = lambda_client.get_paginator('list_functions')
    list_functions_iterator = list_functions_paginator.paginate()

    all_functions = []
    for page in list_functions_iterator:
        all_functions.extend(page['Functions'])

    matching_functions = [function for function in all_functions if lambda_search_name in function['FunctionName']]

    if len(matching_functions) != 1:
        raise Exception(f"Found {len(matching_functions)} Lambda functions with {lambda_search_name} in the name")

    return matching_functions[0]['FunctionName']


def get_active_pipelines(dynamodb_client):
    scan_paginator = dynamodb_client.get_paginator('scan')
    scan_params = {
        'TableName': PIPELINE_CONFIG_TABLE,
        'FilterExpression': "lifecycleStatus = :status",
        'ExpressionAttributeValues': {":status": {"S": "ACTIVE"}}
    }
    scan_iterator = scan_paginator.paginate(**scan_params)

    result = []
    for page in scan_iterator:
        result.extend(page['Items'])
    return result


def get_pipeline_mappings(dynamodb_client, pipeline_arn):
    response = dynamodb_client.scan(
        TableName=PIPELINE_MAPPING_TABLE,
        FilterExpression="pipelineArn = :arn",
        ExpressionAttributeValues={":arn": {"S": pipeline_arn}}
    )

    return response['Items']


def build_lambda_input(pipeline_configuration, pipeline_mapping, delete_alarms):
    action = "DELETE" if delete_alarms else "CREATE"

    return {
        "action": action,
        "accountId": pipeline_configuration['accountId']['S'],
        "pipelineName": pipeline_configuration['pipelineName']['S'],
        "internalId": pipeline_configuration['internalId']['S'],
        "originalPipelineOptions": {
            "pipelineConfigurationBody": pipeline_configuration['pipelineConfigurationBody']['S'],

        },
        "updatedPipelineOptions": {
            "pipelineConfigurationBody": pipeline_configuration['pipelineConfigurationBody']['S'],
        },
        "pipelineResources": {
            "loadBalancerArn": pipeline_mapping['loadBalancerArn']['S'],
            "primaryTargetGroupArn": pipeline_mapping['primaryTargetGroupArn']['S'],
            "secondaryTargetGroupArn": pipeline_mapping['secondaryTargetGroupArn']['S'],
            "serviceName": pipeline_mapping['serviceName']['S'],
            "clusterInfo": {
                "clusterArn": pipeline_mapping['clusterArn']['S'],
                "iamRoleArn": pipeline_mapping['clusterServiceRoleArn']['S']
            }
        }
    }


def invoke_setup_monitoring_lambda(lambda_client, lambda_name, lambda_input):
    response = lambda_client.invoke(
        FunctionName=lambda_name,
        InvocationType='Event',
        Payload=json.dumps(lambda_input)
    )

    if response['StatusCode'] == 202:
        print(f"Lambda successfully invoked for {lambda_input['accountId']}:{lambda_input['pipelineName']}")
    else:
        print(f"Lambda invocation failed for {lambda_input['accountId']}:{lambda_input['pipelineName']}\n"
              f"Error: {response['FunctionError']}")


if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument("--region", "-r", help="The full name of the region ex: 'us-west-2'", type=str)
    argparser.add_argument("--delete-alarms", "-d", help="Delete all per pipeline alarms", default=False, action="store_true")
    argparser.add_argument("--verbose", "-v", help="Output additional logging information", default=False, action="store_true")
    argparser.add_argument("--wait-time", "-t", help="Time in seconds to wait between invocations of the Lambda", type=int, default=10)
    args = argparser.parse_args()

    main(args.region, args.delete_alarms, args.verbose, args.wait_time)

