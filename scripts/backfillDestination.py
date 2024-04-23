import boto3
import argparse
import os
import subprocess
import json
import yaml
from jsonpath_ng import jsonpath, parse

"""
This script is only meant to be used for a one time backfill for a specifc region-stage combination to provide existing pipelines with information about the pipline's destinations

Example usage: 
python3 backfillDestination.py --region us-east-1 --stage beta --dryrun

Use the --sim option to provide a SIM for contingent authorization to the production accounts
"""
 
TABLE_NAME = "DataPrepperPipelineConfigurations"
REGION_CONFIG = """
beta,us-east-1,867475104227
beta,us-west-2,614823704356
gamma,us-east-1,863319322517
gamma,us-west-2,264939667991
prod,ap-northeast-1,488939961373
prod,ap-southeast-1,250245820245
prod,ap-southeast-2,024043984225
prod,eu-west-1,321843683841
prod,eu-central-1,793214851599
prod,eu-west-2,494103247145
prod,us-east-1,650455509444
prod,us-west-2,647705926003
prod,us-west-1,110293142570
prod,us-east-2,642133779026
prod,ap-northeast-2,445771684395
prod,ap-south-1,008739254458
prod,ca-central-1,059841243330
prod,eu-north-1,766161543942
prod,sa-east-1,706976067050
"""
 
def main(sim, dryrun, region_name, stage_name):
    cp_accounts = [line.split(',') for line in REGION_CONFIG.split()]

    account_id = [account[2] for account in cp_accounts if account[0] == stage_name and account[1] == region_name][0]

    if stage_name == 'gamma' and region_name == 'us-east-1':
        setup_credentials(account_id, role_name='Admin-OneClick')
    elif stage_name == 'prod':
        setup_credentials(account_id, sim_ticket_id=sim, role_name='Ops-Oncall')
    else:
        setup_credentials(account_id)
    print(f'Successfully obtained credentials for account {account_id}. Backfilling pipelines...')

    session = boto3.Session()
    ddb_client = session.client("dynamodb", region_name=region_name)
    backfill_pipelines(ddb_client, dryrun)
    print(f'Backfill completed for {stage_name} {region_name} {account_id}')
    print()
 
 
def setup_credentials(account_id, role_name='Admin', sim_ticket_id=None):
    command = [
        'ada', 'credentials', 'update',
        '--account', account_id,
        '--role', role_name,
        '--provider', 'isengard',
        '--once'
    ]
    if sim_ticket_id is not None:
        command += ['--sim', sim_ticket_id]
    subprocess.run(command, check=True)
 
 
def backfill_pipelines(ddb_client, dryrun):
    response = ddb_client.scan(TableName=TABLE_NAME)
    data = response['Items']
    
    while 'LastEvaluatedKey' in response:
        response = ddb_client.scan(TableName=TABLE_NAME, ExclusiveStartKey=response['LastEvaluatedKey'])
        data.extend(response['Items'])
    
    for item in data:
        backfill(ddb_client, item, dryrun)
 

def backfill(ddb_client, item, dryrun):
    destinations = parse_destinations(item)

    ddb_typed_destinations = convert_destinations_to_ddb_type(destinations)

    item["destinations"] = ddb_typed_destinations

    if dryrun:
        print(item)

    if not dryrun:
        ddb_client.put_item(TableName=TABLE_NAME, Item=item)


def convert_destinations_to_ddb_type(destinations):
    dynamodb_list = {'L': []}

    if not destinations:
        return dynamodb_list
    
    for destination in destinations:
        dynamodb_map = {'M': {}}
        for key, value in destination.items():
            dynamodb_map['M'][key] = {'S': value}
        dynamodb_list['L'].append(dynamodb_map)

    return dynamodb_list


def parse_destinations(item):
    json_path_expression = parse('$.*.sink[*]')
    if('pipelineConfigurationBody' in item):
        body_string = item['pipelineConfigurationBody']['S']
        config = json.loads(json.dumps(yaml.safe_load(body_string), indent=4, default=str))
        matches = json_path_expression.find(config)

        destinations = []   
        for match in matches:
            plugin_model = match.value
            destinations.extend(parse_destinations_for_plugin_model(plugin_model))

        unique_destinations = []
        for destination in destinations:
            if destination not in unique_destinations:
                unique_destinations.append(destination)

        return destinations


def parse_destinations_for_plugin_model(plugin_model):
    if plugin_model.get("opensearch"):
        return parse_opensearch_destinations(plugin_model.get("opensearch"))
    elif plugin_model.get("s3"):
        return parse_s3_destinations(plugin_model.get("s3"))
    # Ignore sub-pipelines
    elif plugin_model.get("pipeline"):
        return []
    else:
        print(f"Failed to parse plugin model {plugin_model}")
        return []


def parse_opensearch_destinations(plugin_settings):
    service_name = "AOSS" if is_serverless(plugin_settings) else "AOS"
    hosts = plugin_settings.get("hosts")

    if not hosts:
        return []

    return [{"endpoint": host, "serviceName": service_name} for host in hosts]


def is_serverless(plugin_settings):
    aws_settings = plugin_settings.get("aws")

    if aws_settings:
        return aws_settings.get("serverless")

    return False


def parse_s3_destinations(plugin_settings):
    endpoint = plugin_settings.get("bucket")
    return [{"endpoint": endpoint, "serviceName": "S3"}]
 
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill destination information for all pipelines in a given region/stage")
    parser.add_argument("--region", required=True, default=None, help="The region to backfill")
    parser.add_argument("--stage", required=True, default=None, help="The stage to backfill")
    parser.add_argument("--sim", default=None, help="specifies the sim ticket to access prod accounts")
    parser.add_argument('--dryrun', default=False, action="store_true", help="Print the changes but don't actually make them")  

 
    args = parser.parse_args()
    main(args.sim, args.dryrun, args.region, args.stage)