"""
This script can be used to dynamically generate a dashboard with metrics and lifecycle history for a pipeline.
Currently computeUnits is the only metric supported.

python3 generate_dashboard.py --internal_id "80169de5-0592-4b1b-9856-899657c99640" --region us-east-1
"""


import argparse
import boto3
import pandas as pd
import matplotlib.pyplot as plt
from tabulate import tabulate
from datetime import datetime, timedelta, timezone
import subprocess

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


DATA_PLANE_ACCOUNT_MAPPING = {
    'ap-northeast-1': ['332700493865', '434348002068'],
    'ap-northeast-2': ['463979154840', '053747763961'],
    'ap-south-1': ['961188132508', '119299501364'],
    'ap-southeast-1': ['974168036842', '780344102579'],
    'ap-southeast-2': ['660770330712', '216799886647'],
    'ca-central-1': ['982118959693', '716399342947'],
    'eu-central-1': ['994843801819', '589200122578'],
    'eu-north-1': ['097292218459', '067846619083'],
    'eu-south-1': ['355585272234', '542540229200'],
    'eu-west-1': ['937876418267', '894313991277'],
    'eu-west-2': ['894581090537', '145248543401'],
    'eu-west-3': ['378951213220', '662240664340'],
    'sa-east-1': ['131445043692', '453840224354'],
    'us-east-1': ['309881782262', '895835039025', '992382617983'],
    'us-east-2': ['897575397692', '890396593034'],
    'us-west-1': ['870574976409', '583497257897'],
    'us-west-2': ['977404047420', '005290930114', '767397748705']
}

def get_credentials(account):
    command = [
        'ada', 'credentials', 'update',
        '--account', account,
        '--role', 'ReadOnly',
        '--provider', 'isengard',
        '--once'
    ]
    print(f"Gettting credentials for {account}")
    subprocess.run(command, check=True)

# Query DynamoDB
def query_dynamodb(partition_key_value, dynamodb_client):
    response = dynamodb_client.query(
        TableName="PipelineConfigurationAuditTable",
        KeyConditionExpression='internalId = :id',
        ExpressionAttributeValues={
            ':id': {'S': partition_key_value}
        }
    )
    return response['Items']

def dynamodb_to_dict(item):
    data = {}
    for k, v in item.items():
        data[k] = list(v.values())[0]  # Extract the first value in the DynamoDB type dict
    return data

def process_data(items):
    data = [dynamodb_to_dict(item) for item in items]
    df = pd.DataFrame(data)
    df = df.sort_values(by='changedTimestamp')
    df = df[df['lifecycleStatus'] != df['lifecycleStatus'].shift()]
    return df

# Query CloudWatch
def get_metric_data(metric_name, namespace, dimensions, start_time, end_time, period, stat, cloudwatch_client):
    response = cloudwatch_client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start_time,
        EndTime=end_time,
        Period=period,
        Statistics=[stat]
    )
    return response


def query_all_metrics_for_account(metrics, start_time, end_time, cloudwatch_client):
    all_metrics_data = {}
    for metric in metrics:
       
        metric_name = metric['name']
        namespace = metric['namespace']
        period = metric['period']
        stat = metric['stat']
        dimensions = metric['dimensions'] if 'dimensions' in metric else []

        response = get_metric_data(metric_name, namespace, dimensions, start_time, end_time, period, stat, cloudwatch_client)
        timestamps = [point['Timestamp'] for point in response['Datapoints']]
        values = [point[stat] for point in response['Datapoints']]
        df = pd.DataFrame({'Timestamp': timestamps, metric_name: values})

        if 'Timestamp' in all_metrics_data:
            all_metrics_data = pd.merge(all_metrics_data, df, on='Timestamp', how='outer')
        else:
            all_metrics_data = df
    return all_metrics_data

def combine_metric_data_from_accounts(metrics, start_time, end_time, accounts, region):
    combined_df = pd.DataFrame()
    for account in accounts:

        get_credentials(account)
        session = boto3.Session()
        cloudwatch_client = session.client('cloudwatch', region_name=region)

        account_data = query_all_metrics_for_account(metrics, start_time, end_time, cloudwatch_client)
        combined_df = pd.concat([combined_df, account_data], ignore_index=True)

    combined_df = combined_df.groupby('Timestamp').mean().reset_index()
    return combined_df.sort_values(by='Timestamp')

def plot_combined_metric_data(df, metrics, ax):
    for metric in metrics:
        metric_name = metric['name']
        if metric_name in df:
            ax.plot(df['Timestamp'], df[metric_name], marker='o', linestyle='-', label=metric_name)
    ax.set_title(f'Metrics over Time')
    ax.set_xlabel('Timestamp')
    ax.set_ylabel('Metric Value')
    ax.legend()
    ax.grid(True)

# Display Table
def display_pretty_table(df, selected_columns, ax):
    table_data = df[selected_columns].values
    col_labels = df[selected_columns].columns
    ax.axis('tight')
    ax.axis('off')
    ax.table(cellText=table_data, colLabels=col_labels, cellLoc='center', loc='center')

def convert_epoch(epoch_str):
    return int(epoch_str) / 1000
    pass

def convert_epoch_to_readable(epoch_time):
    dt = datetime.fromtimestamp(int(epoch_time), tz=timezone.utc)
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')

def get_metrics(pipeline_name, account_id):

    return [
        {'name': 'computeUnits', 'namespace': 'DataPrepper', 'period': 3600, 'stat': 'Sum', 'dimensions': [
                {"Name": "accountId", "Value": account_id},
                {"Name": "serviceName", "Value": pipeline_name}
            ]
        }
    ]


# Main function
def main(internal_id, region):
    # Initialize DynamoDB client
    cp_account = CONTROL_PLANE_ACCOUNT_MAPPING[region]
    get_credentials(cp_account)
    session = boto3.Session()
    dynamodb_client = session.client('dynamodb', region_name=region)
    
    # Query DynamoDB
    items = query_dynamodb(internal_id, dynamodb_client)
    df = process_data(items)
    
    # Extract start and end times from the DynamoDB data

    df['changedTimestamp'] = df['changedTimestamp'].apply(convert_epoch)
    df = df.sort_values(by='changedTimestamp')

    start_time = df['changedTimestamp'].iloc[0]
    end_time = df['changedTimestamp'].iloc[-1]

    df['changedTimestamp'] = df['changedTimestamp'].apply(convert_epoch_to_readable)

    pipeline_account = df['accountId'].iloc[0]
    pipeline_name = df['pipelineName'].iloc[0]

    metrics = get_metrics(pipeline_name, pipeline_account)
    
    # Combine metric data from all accounts
    dp_accounts = DATA_PLANE_ACCOUNT_MAPPING[region]
    combined_metrics_df = combine_metric_data_from_accounts(metrics, start_time, end_time, dp_accounts, region)
    
    # Plotting
    fig, axs = plt.subplots(2, 1, figsize=(12, 8))
    fig.suptitle(f'Customer account: {pipeline_account} Pipeline name: {pipeline_name} Region: {region}', fontsize=16, fontweight='bold')

    axs[0].set_title("Metrics", fontsize=14)
    plot_combined_metric_data(combined_metrics_df, metrics, axs[0])

    axs[1].set_title('Status Changes', fontsize=14)
    display_pretty_table(df, ['changedTimestamp', 'lifecycleStatus'], axs[1])
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Query DynamoDB and CloudWatch metrics.')
    parser.add_argument('--internal_id', help='The internalId to query in DynamoDB.')
    parser.add_argument('--region', help='The AWS region for both DynamoDB and CloudWatch.')
    
    args = parser.parse_args()
    
    main(args.internal_id, args.region)
