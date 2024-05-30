import boto3
import argparse
import subprocess
import pandas as pd
from botocore.exceptions import NoCredentialsError, PartialCredentialsError

"""
This script can be used to change the status of pipelines that are stuck in a given status and have orphaned resources
by updating the DataPrepperPipelineConfigurations table

Update the STATUS_TO_CHANGE, STATUS_TO_CHANGE_TO, and BEFORE_CREATED_AT_TIMESTAMP variables to match your requirements.

Usage:

python3 change-pipeline-status.py --region us-east-1 --stage beta --pipeline-csv-path "/Users/tylgry/Downloads/Pipeline_Info_1717087009268.csv"
"""

STATUS_TO_CHANGE = "CREATING"
STATUS_TO_CHANGE_TO = "CREATE_ROLLBACK_COMPLETE"
BEFORE_CREATED_AT_TIMESTAMP = 1717088879827

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

# Function to extract accountId and pipeline name from arn
def extract_account_id_and_pipeline_name(arn):
    parts = arn.split(':')
    account_id = parts[4]
    pipeline_name = parts[5].split('/')[1]
    return account_id, pipeline_name

def auth(region, stage):
    account = CONTROL_PLANE_ACCOUNT_MAPPING[stage][region]
    command = [
        'ada', 'credentials', 'update',
        '--account', account,
        '--role', 'Admin',
        '--provider', 'isengard',
        '--once'
    ]
    subprocess.run(command, check=True)

# Function to update status in DynamoDB
def update_status(account_id, pipeline_name):
    try:
        response = table.get_item(
            Key={
                'accountId': account_id,
                'pipelineName': pipeline_name
            }
        )
        item = response.get('Item')

        if item and item.get('lifecycleStatus') == STATUS_TO_CHANGE and item.get('createdAt') < BEFORE_CREATED_AT_TIMESTAMP:
            table.update_item(
                Key={
                    'accountId': account_id,
                    'pipelineName': pipeline_name
                },
                UpdateExpression='SET #status = :new_status',
                ExpressionAttributeNames={'#status': 'lifecycleStatus'},
                ExpressionAttributeValues={':new_status': status_to_change_to}
            )
            print(f"Updated status {STATUS_TO_CHANGE} to {STATUS_TO_CHANGE_TO} for {account_id}, {pipeline_name}")
        else:
            print(f"No update needed for {account_id}, {pipeline_name}")
    except (NoCredentialsError, PartialCredentialsError) as e:
        print(f"Credentials error: {str(e)}")
    except Exception as e:
        print(f"Error updating item: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set status of pipelines in a region")
    parser.add_argument("--region", required=True, help="specifies the region where the pipelines are")
    parser.add_argument("--stage", default="prod", help="specifies the stage")
    parser.add_argument("--pipeline-csv-path", required=True, help="specifies the path to the CSV file containing the pipelines to change status for")
    args = parser.parse_args()

    auth(args.region, args.stage)

    csv_file_path = args.pipeline-csv-path
    df = pd.read_csv(csv_file_path)

    dynamodb = boto3.resource('dynamodb', region_name=args.region)
    table = dynamodb.Table('DataPrepperPipelineConfigurations')


    # Iterate through each row in the CSV
    for index, row in df.iterrows():
        arn = row['pipelinearn']
        account_id, pipeline_name = extract_account_id_and_pipeline_name(arn)
        update_status(account_id, pipeline_name)
