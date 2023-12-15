import argparse
import datetime
import logging
import subprocess
import time

import boto3

ONE_MONTH = 30
THREE_MONTHS = 90
ONE_YEAR = 365
TEN_YEARS = 3653
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


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console handler
        logging.FileHandler(f'set-log-retention-{datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")}.log')  # File handler
    ]
)


def main(page_size=50, target_retention=TEN_YEARS, mcm_id=None, sim_id=None):
    accounts = [line.split(',') for line in REGION_CONFIG.split()]
    for stage_name, region_name, account_id in accounts:
        try:
            get_credentials_for_account(account_id, stage_name, mcm_id, sim_id)
            logging.info(f'Successfully obtained credentials for account {account_id}.')
        except Exception:
            logging.info(f'Failed to obtain credentials for account {account_id}. Skip setting log retention for this account.')
            continue

        set_log_retention(account_id=account_id, region_name=region_name, page_size=page_size, target_retention=target_retention)


def set_log_retention(account_id, region_name, page_size, target_retention):

    session = boto3.Session()
    if region_name is None:
        logs_client = session.client('logs')
    else:
        logs_client = session.client('logs', region_name=region_name)

    paginator = logs_client.get_paginator('describe_log_groups')
    page_iterator = paginator.paginate(PaginationConfig={'PageSize': page_size})

    logging.info(f"Setting log retention to {target_retention} days for account {account_id} in region {region_name}.")

    failed_log_groups = []
    for i, page in enumerate(page_iterator):
        logging.info(f"Processing Page {i + 1} ...")
        for log_group_attrs in page['logGroups']:
            log_group_name = log_group_attrs['logGroupName']
            current_retention = log_group_attrs.get('retentionInDays')

            if current_retention is None or current_retention > target_retention:
                logging.debug(f"Setting retention for log group: {log_group_name}")
                logs_client.put_retention_policy(logGroupName=log_group_name, retentionInDays=target_retention)

                # Rate limit is 5 TPS for both PutRetentionPolicy and DescribeLogGroups API
                time.sleep(0.5)

                if not validate_retention(logs_client, log_group_name, target_retention):
                    logging.warning(f"Failed to set retention policy for log group: {log_group_name}")
                    failed_log_groups.append(log_group_name)

    if failed_log_groups:
        filepath = f"{account_id}-{region_name}-failed-records.txt"
        record_failed_log_groups(failed_log_groups, filepath)
    logging.info("Completed!\n")


def validate_retention(logs_client, log_group_name, target_retention):
    for log_group in logs_client.describe_log_groups(logGroupNamePrefix=log_group_name)['logGroups']:
        if log_group['logGroupName'] == log_group_name and log_group['retentionInDays'] == target_retention:
            return True
    return False


def record_failed_log_groups(failed_log_groups, failed_log_groups_path):
    with open(failed_log_groups_path, 'w') as f:
        for log_group in failed_log_groups:
            f.write(f"{log_group}\n")


def get_credentials_for_account(account_id, stage, mcm_id, sim_id):
    if stage == 'prod':
        setup_credentials(account_id, role_name='Ops-Oncall', mcm_id=mcm_id, sim_id=sim_id)
    else:
        setup_credentials(account_id)


def setup_credentials(account_id, role_name='Admin', mcm_id=None, sim_id=None):
    command = [
        'ada', 'credentials', 'update',
        '--account', account_id,
        '--role', role_name,
        '--provider', 'isengard',
        '--once'
    ]
    if mcm_id is not None:
        command += ['--mcm', mcm_id]
    elif sim_id is not None:
        command += ['--sim', sim_id]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Change CloudWatch log group retention")

    parser.add_argument("--page-size", default=50, type=int,
                        help="specifies the page size for DescribeLogGroups API call. Must be less than or equal to 50. ")
    parser.add_argument("--target-retention", default=3653, type=int, help="specifies the target retention in days")
    parser.add_argument("--mcm", default=None, help="specifies the mcm ticket to access prod accounts")
    parser.add_argument("--sim", default=None, help="specifies the sim ticket to access prod accounts")

    args = parser.parse_args()
    main(page_size=args.page_size, target_retention=args.target_retention, mcm_id=args.mcm, sim_id=args.sim)
