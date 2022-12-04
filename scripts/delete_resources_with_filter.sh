# Example usage: ./delete_resources_with_filter.sh -a 303581101768 \
#                                                  -r us-west-2 \
#                                                  -c arn:aws:ecs:us-west-2:303581101768:cluster/FizzyDrPepper-EcsCluster-gamma-us-west-2-cell1-ClusterEB0386A7-sg4eY8iLeYJ3 \
#                                                  -d FizzyDrPepper-CodeDeploy-gamma-us-west-2-cell1-CodeDeployApplicationE587C27C-190RYIP548DMT \
#                                                  -f giza*
#           -a -> The AWS account id that contains the resources to be deleted. Often will be the data plane service account
#           -r -> The region that contains the resources to be deleted
#           -c -> The ECS cluster ARN that contains the ECS services to be deleted
#           -d -> The CodeDeploy application name that contains the CodeDeploy deployments to be deleted
#           -f -> The pipeline name tag filter which will only delete resources that are tagged or contain this pattern

while getopts a:c:r:d:f: flag
do
    case "${flag}" in
        a) AWS_ACCOUNT=${OPTARG};;
        r) AWS_REGION=${OPTARG};;
        c) CLUSTER_ARN=${OPTARG};;
        d) CODE_DEPLOY_APPLICATION=${OPTARG};;
        f) PIPELINE_NAME_TAG_FILTER=${OPTARG};;
        *) exit 1
    esac
done

AWS_ROLE="Admin"

DESIRED_COUNT="0"
SERVICE_QUERY='.service | .serviceName + " " + .status'
AWS="aws --region $AWS_REGION "

ada credentials update --account=$AWS_ACCOUNT --provider=isengard --role=$AWS_ROLE --once

# CodeDeploy Deployments
DEPLOYMENT_GROUP_LIST=($($AWS deploy list-deployment-groups --no-paginate --application-name "$CODE_DEPLOY_APPLICATION" | jq '.deploymentGroups[]' | grep ${PIPELINE_NAME_TAG_FILTER} | tr -d '"'))
for DEPLOYMENT_GROUP in "${DEPLOYMENT_GROUP_LIST[@]}"
do
    echo "Deleting deployment group ${DEPLOYMENT_GROUP}"
    $AWS deploy delete-deployment-group --application-name "$CODE_DEPLOY_APPLICATION" --deployment-group-name "${DEPLOYMENT_GROUP}"
done

#NLBs
NLB_LIST=($($AWS elbv2 --no-paginate describe-load-balancers \
    | jq '.LoadBalancers[] | .LoadBalancerArn' \
    | tr -d '"' | tr "\n" ' '))

declare -a NLB_LIST_WITH_TAG_PREFIX=()

NLB_LIST_LENGTH=${#NLB_LIST[@]}
START_INDEX=0
RESOURCE_COUNT=20
while [ $START_INDEX -lt $NLB_LIST_LENGTH ] 
do
	if (( START_INDEX + RESOURCE_COUNT < "$NLB_LIST_LENGTH" )); then
		RESOURCE_COUNT=20
	else
	    RESOURCE_COUNT=$(expr $NLB_LIST_LENGTH - $START_INDEX)
	fi
	NLB_LIST_BATCH=${NLB_LIST[@]:${START_INDEX}:${RESOURCE_COUNT}}
	NLB_LIST_BATCH_WITH_TAG_PREFIX=($($AWS elbv2 describe-tags --resource-arns $NLB_LIST_BATCH | jq -r '.TagDescriptions[] | select(.Tags[].Value | match("'"${PIPELINE_NAME_TAG_FILTER}"'")) | .ResourceArn' | tr -d '"'))
	NLB_LIST_WITH_TAG_PREFIX+=(${NLB_LIST_BATCH_WITH_TAG_PREFIX[@]})
	START_INDEX=$(expr $START_INDEX + $RESOURCE_COUNT)
done


for NLB in "${NLB_LIST_WITH_TAG_PREFIX[@]}"
do
    echo "Processing NLB ${NLB}"

    LISTENER_LIST=($($AWS elbv2 describe-listeners --load-balancer-arn "${NLB}" \
        | jq '.Listeners[] | .ListenerArn' \
        | tr -d '"'))

    for LISTENER in "${LISTENER_LIST[@]}"
    do
        echo "Deleting listener ${LISTENER}"
        $AWS elbv2 delete-listener --listener-arn "${LISTENER}"
    done

    echo "Deleting load balancer ${NLB}"
    $AWS elbv2 delete-load-balancer --load-balancer-arn "${NLB}"
    echo "---"
done

# ECS Services
CLUSTER_LIST=($($AWS ecs list-clusters \
    | jq '.clusterArns | .[]' \
    | tr -d '"' | tr "\n" ' '))

for CLUSTER in "${CLUSTER_LIST[@]}"
do
    echo "Processing cluster ${CLUSTER}"

    SERVICE_LIST=($($AWS ecs list-services --cluster "${CLUSTER_ARN}" \
        | jq '.serviceArns | .[]' | grep ${PIPELINE_NAME_TAG_FILTER} \
        | tr -d '"'))
    
    for SERVICE in "${SERVICE_LIST[@]}"
    do
        echo "Updating service ${SERVICE} to desired count ${DESIRED_COUNT}"
        $AWS ecs update-service --desired-count "${DESIRED_COUNT}" --cluster "${CLUSTER}" --service "${SERVICE}" \
            | jq "${SERVICE_QUERY}"

         echo "Deleting service ${SERVICE}"
         $AWS ecs delete-service --cluster "${CLUSTER}" --service "${SERVICE}" | jq "${SERVICE_QUERY}"
    done
done

#Target groups
TARGET_GROUP_LIST=($($AWS elbv2 describe-target-groups \
    | jq '.TargetGroups[] | .TargetGroupArn' \
    | tr -d '"'))

declare -a TARGET_GROUP_LIST_WITH_TAG_PREFIX=()

TARGET_GROUP_LIST_LENGTH=${#TARGET_GROUP_LIST[@]}
START_INDEX=0
RESOURCE_COUNT=20
while [ $START_INDEX -lt $TARGET_GROUP_LIST_LENGTH ] 
do
	if (( START_INDEX + RESOURCE_COUNT < "$TARGET_GROUP_LIST_LENGTH" )); then
		RESOURCE_COUNT=20
	else
	    RESOURCE_COUNT=$(expr $TARGET_GROUP_LIST_LENGTH - $START_INDEX)
	fi
	TARGET_GROUP_LIST_BATCH=${TARGET_GROUP_LIST[@]:${START_INDEX}:${RESOURCE_COUNT}}
	TARGET_GROUP_LIST_BATCH_WITH_TAG_PREFIX=($($AWS elbv2 describe-tags --resource-arns $TARGET_GROUP_LIST_BATCH | jq -r '.TagDescriptions[] | select(.Tags[].Value | match("'"${PIPELINE_NAME_TAG_FILTER}"'")) | .ResourceArn' | tr -d '"'))
	TARGET_GROUP_LIST_WITH_TAG_PREFIX+=(${TARGET_GROUP_LIST_BATCH_WITH_TAG_PREFIX[@]})
	START_INDEX=$(expr $START_INDEX + $RESOURCE_COUNT)
done

for TARGET_GROUP in "${TARGET_GROUP_LIST_WITH_TAG_PREFIX[@]}"
do
    echo "Deleting target group ${TARGET_GROUP}"
    $AWS elbv2 delete-target-group --target-group-arn "${TARGET_GROUP}"
done

# Security Groups
SECURITY_GROUP_LIST=($(\
    $AWS ec2 describe-security-groups --no-paginate --filters 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' \
        | jq '.SecurityGroups | .[] | .GroupId' \
        | tr -d '"' \
))
for SECURITY_GROUP in "${SECURITY_GROUP_LIST[@]}"
do
    echo "Deleting security group ${SECURITY_GROUP}"
    $AWS ec2 delete-security-group --group-id "${SECURITY_GROUP}"
done

#Route Tables
ROUTE_TABLE_LIST=($($AWS ec2 describe-route-tables --no-paginate --filters 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' | jq '.RouteTables[] | .RouteTableId' | tr -d '"'))
for ROUTE_TABLE in "${ROUTE_TABLE_LIST[@]}"
do
    echo "Deleting route table ${ROUTE_TABLE} route 0.0.0.0/0"
    $AWS ec2 delete-route --route-table-id "${ROUTE_TABLE}" --destination-cidr-block "0.0.0.0/0"
done

#Nat Gateways
NAT_GATEWAY_LIST=($($AWS ec2 describe-nat-gateways --no-paginate --filter 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' | jq '.NatGateways[] | .NatGatewayId' | tr -d '"'))
for NAT_GATEWAY in "${NAT_GATEWAY_LIST[@]}"
do
    echo "Deleting NAT gateway ${NAT_GATEWAY}"
    $AWS ec2 delete-nat-gateway --nat-gateway-id "${NAT_GATEWAY}"
done

EIP_ASSOCIATION_LIST=($($AWS ec2 describe-addresses --no-paginate --filters 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' | jq '.Addresses[] | .AssociationId' | tr -d '"'))
for ASSOCIATION in "${EIP_ASSOCIATION_LIST[@]}"
do
    echo "Disassociate association ${ASSOCIATION}"
    $AWS ec2 disassociate-address --association-id "${ASSOCIATION}"
done

EIP_ALLOCATION_LIST=($($AWS ec2 describe-addresses --no-paginate --filters 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' | jq '.Addresses[] | .AllocationId' | tr -d '"'))
for ALLOCATION in "${EIP_ALLOCATION_LIST[@]}"
do
    echo "Releasing allocation ${ALLOCATION}"
    $AWS ec2 release-address --allocation-id "${ALLOCATION}"
done

INTERNET_GATEWAY_LIST=($($AWS ec2 describe-internet-gateways --no-paginate --filters 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' | jq '.InternetGateways[] | select(.Attachments[]) | .InternetGatewayId' | tr -d '"'))
for INTERNET_GATEWAY in "${INTERNET_GATEWAY_LIST[@]}"
do
    VPC_ID=($($AWS ec2 describe-internet-gateways --internet-gateway-ids "${INTERNET_GATEWAY}" | jq '.InternetGateways[] | .Attachments[] | .VpcId' | tr -d '"'))
    echo "Detaching internet gateway ${INTERNET_GATEWAY} from vpc-id ${VPC_ID}"
    $AWS ec2 detach-internet-gateway --internet-gateway-id "${INTERNET_GATEWAY}" --vpc-id  "${VPC_ID}"
    echo "Deleting internet gateway ${INTERNET_GATEWAY}"
    $AWS ec2 delete-internet-gateway --internet-gateway-id "${INTERNET_GATEWAY}"
done

SUBNET_LIST=($($AWS ec2 describe-subnets --no-paginate --filters 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' | jq '.Subnets[] | .SubnetId' | tr -d '"'))
for SUBNET in "${SUBNET_LIST[@]}"
do
    echo "Deleting subnet ${SUBNET}"
    $AWS ec2 delete-subnet --subnet-id "${SUBNET}"
done

ROUTE_TABLE_LIST=($($AWS ec2 describe-route-tables --no-paginate --filters 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' | jq '.RouteTables[] | .RouteTableId' | tr -d '"'))
for ROUTE_TABLE in "${ROUTE_TABLE_LIST[@]}"
do
    echo "Deleting route table ${ROUTE_TABLE}"
    $AWS ec2 delete-route-table --route-table-id "$ROUTE_TABLE"
done

VPC_LIST=($($AWS ec2 describe-vpcs --no-paginate --filters 'Name=tag:PipelineName,Values='"${PIPELINE_NAME_TAG_FILTER}"'' | jq '.Vpcs[] | .VpcId' | tr -d '"'))
for VPC in "${VPC_LIST[@]}"
do
    echo "Deleting vpc ${VPC}"
    $AWS ec2 delete-vpc --vpc-id "${VPC}"
done