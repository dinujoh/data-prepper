import boto3
import argparse

"""
1. Copy credentials for Data Plane account
2. Call script with region and ECS cluster ARN for account

Example usage: 
python3 get_orphaned_vpcs.py --region us-east-1 --ecs_cluster_arn "arn:aws:ecs:us-east-1:701817888140:cluster/FizzyDrPepper-EcsCluster-beta-us-east-1-cell1-ClusterEB0386A7-hrV96abetIpU"

Use the -v option to get the number of orphaned VPCs along with the pipelineIds
"""


def main(ecs_cluster, region, verbose):

	ecs_client = boto3.client('ecs', region_name=region)

	ec2_client = boto3.client('ec2', region_name=region)

	ecs_services = list_ecs_services(ecs_client, ecs_cluster)

	ecs_pipeline_ids = set([get_pipeline_id_from_ecs_service(ecs_service) for ecs_service in ecs_services])

	vpcs = list_vpcs(ec2_client)

	vpc_pipeline_ids = set([get_pipeline_id_from_vpc(vpc, verbose) for vpc in vpcs])

	pipeline_ids_for_orphaned_vpc = vpc_pipeline_ids - ecs_pipeline_ids

	for pipeline_id in pipeline_ids_for_orphaned_vpc:
		if pipeline_id:
			print(pipeline_id)

	if verbose:
		print(f"\n\nTotal number of VPCs: {len(vpc_pipeline_ids)}")
		print(f"Total number of ECS Services: {len(ecs_pipeline_ids)}")
		print(f"Total number of Orphaned VPCs: {len(pipeline_ids_for_orphaned_vpc)}")


def list_ecs_services(ecs_client, ecs_cluster):

	service_arns = []

	list_services_paginator = ecs_client.get_paginator('list_services')

	for response in list_services_paginator.paginate(cluster=ecs_cluster):
		service_arns += response["serviceArns"]
	
	return service_arns


def get_pipeline_id_from_ecs_service(ecs_service_arn):

	pipeline_name_account_id_start = ecs_service_arn.rindex("/")
	pipeline_name_account_id_separator = ecs_service_arn.rindex("-")

	pipeline_name = ecs_service_arn[pipeline_name_account_id_start + 1: pipeline_name_account_id_separator]
	account_id = ecs_service_arn[pipeline_name_account_id_separator + 1:]

	return f"{account_id}:{pipeline_name}"


def list_vpcs(ec2_client):
	vpcs = []

	list_vpcs_paginator = ec2_client.get_paginator('describe_vpcs')

	for response in list_vpcs_paginator.paginate():
		vpcs += response["Vpcs"]

	return vpcs


def get_pipeline_id_from_vpc(vpc, verbose):

	try:
		tags = vpc["Tags"]

		tag_map = {tag["Key"]: tag["Value"] for tag in tags}

		customer_account = tag_map["CustomerAccount"]
		pipeline_name = tag_map["PipelineName"]

		return f"{customer_account}:{pipeline_name}"
	except:
		if verbose:
			print(f"Failed to get pipelineId from VPC:\n{vpc}")

		return ""


if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument('--region', help="AWS region for data plane account ex: us-east-1")   
	parser.add_argument('--ecs_cluster_arn', help="ECS cluster arn for data plane account")  
	parser.add_argument('-v', '--verbose', default=False, action="store_true", help="Print additional information")  
	args = parser.parse_args()

	main(args.ecs_cluster_arn, args.region, args.verbose)

