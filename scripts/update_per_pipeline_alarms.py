import boto3
import json
import argparse

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
2. Run the script: python3 update_per_pipeline_alarms.py

This script can also be used in to delete all per pipeline alarms. 
This should only be done in extreme cases where there were issues with the alarm configuration.

1. Copy credentials for the control plane account
2. Run the script: python3 update_per_pipeline_alarms.py --delete-alarms

Future extensions:
- Add support for creating/deleting alarms for specific pipelines
- Add support for creating/deleting alarms for pipelines that were created/updated before/after an inputted timestamp
"""


def main(delete_alarms):

    lambda_client = boto3.client('lambda')
    dynamodb_client = boto3.client('dynamodb')

    lambda_name = get_alarm_management_lambda(lambda_client, delete_alarms)
    print(f"Found lambda: {lambda_name}")

    active_pipelines = get_active_pipelines(dynamodb_client)

    pipeline_table_entries = [(pipeline, get_pipeline_mappings(dynamodb_client, pipeline['pipelineArn']['S']))
                              for pipeline in active_pipelines]

    lambda_inputs = [build_lambda_input(pipeline_configuration, pipeline_mapping)
                     for pipeline_configuration, pipeline_mapping in pipeline_table_entries]

    for lambda_input in lambda_inputs:
        invoke_setup_monitoring_lambda(lambda_client, lambda_name, lambda_input)


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

    return response['Items'][0]


def build_lambda_input(pipeline_configuration, pipeline_mapping):
    return {
        "action": "CREATE",
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
    argparser.add_argument("--delete-alarms", "-d", help="Delete all per pipeline alarms", default=False, action="store_true")
    args = argparser.parse_args()

    main(args.delete_alarms)

