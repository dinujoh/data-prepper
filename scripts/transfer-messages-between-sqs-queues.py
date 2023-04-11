#!/usr/bin/python3

import argparse
import boto3
import subprocess


def transfer_messages(boto3_session, source_queue_url, destination_queue_url):

    # Initialize the SQS client
    sqs = boto3.client('sqs')

    while True:
        # Receive messages from the source queue
        messages = sqs.receive_message(
            QueueUrl=source_queue_url,
            AttributeNames=['All'],
            MaxNumberOfMessages=10
        )

        # Check if there are any messages in the response
        if 'Messages' not in messages:
            print("No more messages in the source queue.")
            break

        # Iterate through the messages and send them to the destination queue
        for message in messages['Messages']:
            sqs.send_message(
                QueueUrl=destination_queue_url,
                MessageBody=message['Body'],
                MessageAttributes=message.get('MessageAttributes', {})
            )

            # Delete the message from the source queue
            sqs.delete_message(
                QueueUrl=source_queue_url,
                ReceiptHandle=message['ReceiptHandle']
            )

def setup_credentials(account_id, region, role_name, sim_ticket_id=None):
    command = [
        'ada', 'credentials', 'update',
        '--account', account_id,
        '--role', role_name,
        '--provider', 'isengard',
        '--sim', sim_ticket_id,
        '--once'
    ]
    subprocess.run(command, check=True)

    session = boto3.session.Session(region_name=region)
    return session

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Move messages from one sqs queue to another sqs queue")
    parser.add_argument("--account", default=None, help="specifies the account where the queues are")
    parser.add_argument("--region", default=None, help="specifies the region where the queues are")
    parser.add_argument("--role", default=None, help="specifies the role to assume")
    parser.add_argument("--sim", default=None, help="specifies the sim ticket to access prod accounts")
    parser.add_argument("--source-queue-url", default=None, help="specifies the source queue")
    parser.add_argument("--destination-queue-url", default=None, help="specifies the destination queue")
    args = parser.parse_args()

    session = setup_credentials(args.account, args.region, args.role, args.sim)
    transfer_messages(session, args.source_queue_url, args.destination_queue_url)
