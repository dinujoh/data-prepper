import boto3
import argparse
import subprocess
import yaml
from arnparse import arnparse

"""
WARNING: THIS SCRIPT SHOULD ONLY BE USED IN PRODUCTION WITH AN MCM. EXERCISE CAUTION WHEN RUNNING IT.

This script can be used to fix a bug for existing DynamoDB pipelines by removing the ttl field from the read-only GLOBAL items in the coordination stores.

Bug overview: https://quip-amazon.com/cSotAszpYvuK/DDB-Global-State-Bug-Script-SOP
This script will not be needed after the bug fix is deployed (https://github.com/opensearch-project/data-prepper/pull/3711)

Run the script on all regions: 
python3 remove_ttl_from_source_coordination_items.py --mcm MCM-12345

Run the script on a specific stage or region: 
python3 remove_ttl_from_source_coordination_items.py --mcm MCM-12345 --region us-east-1 --stage prod
"""

PIPELINE_CONFIG_TABLE = "DataPrepperPipelineConfigurations"
PIPELINE_MAPPING_TABLE = "PipelineMapping"
SOURCE_COORDINATION_TABLE = "DataPrepperSourceCoordinationStore"
PIPELINE_CONFIGURATION_BODY = "pipelineConfigurationBody"
START_COUNT = "startCount"
GLOBAL_ITEM_FORMAT = "{}:{}:{}:{}|{}|GLOBAL"

REGION_CONFIG = {
    "beta:us-east-1": "867475104227",
    "beta:us-west-2": "614823704356",
    "gamma:us-east-1": "863319322517",
    "gamma:us-west-2": "264939667991",
    "prod:ap-northeast-1": "488939961373",
    "prod:ap-southeast-1": "250245820245",
    "prod:ap-southeast-2": "024043984225",
    "prod:eu-west-1": "321843683841",
    "prod:eu-central-1": "793214851599",
    "prod:eu-west-2": "494103247145",
    "prod:us-east-1": "650455509444",
    "prod:us-west-2": "647705926003",
    "prod:us-west-1": "110293142570",
    "prod:us-east-2": "642133779026",
    "prod:ap-northeast-2": "445771684395",
    "prod:ap-south-1": "008739254458",
    "prod:ca-central-1": "059841243330",
    "prod:eu-north-1": "766161543942",
    "prod:sa-east-1": "706976067050"
}


def main(stage, region, mcm):
    for stage_region, control_plane_account_id in REGION_CONFIG.items():
        actual_stage = stage_region.split(':')[0]
        actual_region = stage_region.split(':')[1]

        if (stage is not None and actual_stage != stage) or (region is not None and actual_region != region):
            continue
        seen_data_plane_cells = []
        get_credentials_for_account(control_plane_account_id, actual_stage, actual_region, mcm)

        session = boto3.Session()
        cp_ddb_client = session.client("dynamodb", region_name=actual_region)
        active_pipelines = get_active_pipelines(cp_ddb_client)

        for pipeline in active_pipelines:

            try:
                if PIPELINE_CONFIGURATION_BODY in pipeline and START_COUNT in pipeline:
                    pipeline_configuration = pipeline[PIPELINE_CONFIGURATION_BODY]['S']
                    parsed_yaml_config = yaml.safe_load(pipeline_configuration)
                    sub_pipeline_name = \
                        [key for key in parsed_yaml_config.keys() if
                         key != 'version' and key != 'extension'][
                            0]
                    source_name = [key for key in parsed_yaml_config[sub_pipeline_name]['source'].keys()][0]

                    if source_name != 'dynamodb':
                        continue

                    pipeline_mappings = query_pipeline_mappings(cp_ddb_client, pipeline['pipelineArn']['S'])
                    data_plane_cell_account_id = arnparse(pipeline_mappings['clusterArn']['S']).account_id
                    get_credentials_for_account(data_plane_cell_account_id, actual_region, actual_stage, mcm, 'Admin')

                    dp_session = boto3.Session()
                    dp_ddb_client = dp_session.client("dynamodb", region_name=actual_region)
                    pipeline_account_id = pipeline['accountId']['S']
                    pipeline_name = pipeline['pipelineName']['S']
                    internal_id = pipeline['internalId']['S']
                    start_count = pipeline['startCount']['N']
                    global_item_partition_key = GLOBAL_ITEM_FORMAT.format(pipeline_account_id,
                                                                          pipeline_name,
                                                                          internal_id,
                                                                          start_count,
                                                                          sub_pipeline_name)

                    update_coordination_items_for_pipeline(dp_ddb_client, global_item_partition_key)

            except Exception as e:
                print(
                    f'Received an exception while checking pipeline {pipeline["pipelineArn"]["S"]}. Skipping for this pipeline')
                print(e)
                continue


def get_credentials_for_account(account_id, stage, region, mcm, role_name=None):
    try:
        if stage == 'gamma' and region == 'us-east-1':
            setup_credentials(account_id, role_name='Admin-OneClick')
        elif role_name is not None:
            setup_credentials(account_id, mcm_id=mcm, role_name=role_name)
        elif stage == 'prod':
            setup_credentials(account_id, mcm_id=mcm, role_name='Ops-Oncall')
        else:
            setup_credentials(account_id)
        print(f'Successfully obtained credentials for account {account_id}.')
    except Exception:
        print(f'Failed to obtain credentials for account {account_id}.')
        return


def setup_credentials(account_id, role_name='Admin', mcm_id=None):
    command = [
        'ada', 'credentials', 'update',
        '--account', account_id,
        '--role', role_name,
        '--provider', 'isengard',
        '--once'
    ]
    if mcm_id is not None:
        command += ['--mcm', mcm_id]

    command += [' > /dev/null']
    subprocess.run(command, check=True)


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


def query_pipeline_mappings(dynamodb_client, pipeline_arn):
    response = dynamodb_client.query(
        TableName=PIPELINE_MAPPING_TABLE,
        KeyConditionExpression='#pk = :pk_val',
        ExpressionAttributeNames={
            '#pk': 'pipelineArn'
        },
        ExpressionAttributeValues={
            ':pk_val': {
                'S': pipeline_arn
            }
        }
    )

    return response['Items'][0]


def update_coordination_items_for_pipeline(data_plane_ddb_client, item_partition_key):
    global_items = query_global_items(data_plane_ddb_client, item_partition_key)

    items_fixed = 0
    for global_item in global_items:
        if 'expirationTime' in global_item:
            remove_expiration_time_from_item(data_plane_ddb_client, global_item)
            items_fixed += 1

    if items_fixed == 0:
        print(
            f"No global items with an expirationTime were found for pipeline {item_partition_key}. This pipeline may have already been fixed.")
    else:
        print(f"{items_fixed} global items for pipeline {item_partition_key} were found with an expirationTime, and "
              f"the expirationTime was removed")


def query_global_items(data_plane_ddb_client, partition_key):
    response = data_plane_ddb_client.query(
        TableName=SOURCE_COORDINATION_TABLE,
        KeyConditionExpression='#pk = :pk_val',
        ExpressionAttributeNames={
            '#pk': 'sourceIdentifier'
        },
        ExpressionAttributeValues={
            ':pk_val': {
                'S': partition_key
            }
        }
    )

    return response['Items']


def remove_expiration_time_from_item(data_plane_ddb_client, item):
    data_plane_ddb_client.update_item(
        TableName=SOURCE_COORDINATION_TABLE,
        Key={
            'sourceIdentifier': {'S': item['sourceIdentifier']['S']},
            'sourcePartitionKey': {'S': item['sourcePartitionKey']['S']}
        },
        UpdateExpression='REMOVE #expiration_time',
        ExpressionAttributeNames={
            '#expiration_time': 'expirationTime'
        }
    )


if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument("--stage", "-s", default=None,
                           help="The stage of the pipelines to remove the expirationTime from")
    argparser.add_argument("--region", "-r", default=None,
                           help="The region of the pipelines to remove the expirationTime from")
    argparser.add_argument("--mcm", default=None,
                           help="specifies the MCM to access prod accounts (Ex: --mcm MCM-12345)")
    args = argparser.parse_args()

    main(args.stage, args.region, args.mcm)
