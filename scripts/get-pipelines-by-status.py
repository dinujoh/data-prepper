#!/usr/bin/python3

import argparse
import boto3
import subprocess
from tabulate import tabulate
from datetime import datetime

CONTROL_PLANE_ACCOUNT_MAPPING = {
    "beta": {
        "us-east-1": "867475104227",
        "us-west-2": "614823704356"
    },
    "gamma": {
        "us-east-1": "863319322517",
        "us-west-2": "264939667991"
    },
    "prod": {
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
    },
}

def auth(region, stage):
    account = CONTROL_PLANE_ACCOUNT_MAPPING[stage][region];
    command = [
        'ada', 'credentials', 'update',
        '--account', account,
        '--role', 'ReadOnly',
        '--provider', 'isengard',
        '--once'
    ]
    subprocess.run(command, check=True)

def get_dynamo_items(region, status):
    ddb = boto3.client('dynamodb', region_name=region)
    paginator = ddb.get_paginator("scan")

    items = []
    operation_parameters = {
        'TableName': 'DataPrepperPipelineConfigurations',
        'FilterExpression': 'lifecycleStatus = :status',
        'ExpressionAttributeValues': {
            ':status': {'S': status},
        },
        'ProjectionExpression': 'accountId, pipelineName, lifecycleStatus, lastUpdatedAt, createdAt'
    }

    try:
        for page in paginator.paginate(**operation_parameters):
            for item in page['Items']:
                items.append({
                    "accountId": item.get("accountId", {}).get("S", {}),
                    "pipelineName": item.get("pipelineName", {}).get("S", {}),
                    "lifecycleStatus": item.get("lifecycleStatus", {}).get("S", {}),
                    "lastUpdatedAt": int(item.get("lastUpdatedAt", {}).get("N", {})),
                    "createdAt": int(item.get("createdAt", {}).get("N", {}))
                })
    except Exception as e:
        print(f"Error fetching pipeline information from DDB: {e}")

    return items

def convert_epoch_to_datetime(epoch):
    return datetime.fromtimestamp(epoch/1000)

def print_items(items):
    header_labels = ('AccountId', 'PipelineName', 'Status', 'CreationTime', 'LastUpdateTime')
    sorted_items = sorted(items, key=lambda x: x['lastUpdatedAt'], reverse=True)
    rows = [[x['accountId'], x['pipelineName'], x['lifecycleStatus'], convert_epoch_to_datetime(x['createdAt']),
             convert_epoch_to_datetime(x['lastUpdatedAt'])] for x in sorted_items]
    print(tabulate(rows, headers=header_labels, tablefmt='grid'))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get all pipelines in a region by status")
    parser.add_argument("--region", required=True, help="specifies the region where the pipelines are")
    parser.add_argument("--stage", default="prod", help="specifies the stage")
    parser.add_argument("--status", required=True, help="specifies the lifecycleStatus to filter on")
    args = parser.parse_args()

    auth(args.region, args.stage)
    items = get_dynamo_items(args.region, args.status)

    print_items(items)