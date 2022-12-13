#!/bin/bash

# beta pdx
AWS_REGION="us-west-2"
AWS_ACCOUNT="568243693831"
AWS_ROLE="Admin"
CLUSTER_ARN="FizzyDrPepper-EcsCluster-beta-us-west-2-cell1-ClusterEB0386A7-ONeX6WBBCaOI"
CODE_DEPLOY_APPLICATION="FizzyDrPepper-CodeDeploy-beta-us-west-2-cell1-CodeDeployApplicationE587C27C-1N81S1EQ1THQJ"
DEFAULT_VPC="vpc-0101c717279e5c7d3"
BASE_VPC="vpc-0a1a6280e2983da95"

ada credentials update --account=$AWS_ACCOUNT --provider=isengard --role=$AWS_ROLE --once

AWS="aws --region $AWS_REGION "

DESIRED_COUNT="0"
SERVICE_QUERY='.service | .serviceName + " " + .status'

EXAMPLE_LIST=(
    "item a" \
    "item b"
)

DEPLOYMENT_GROUP_LIST=($($AWS deploy list-deployment-groups --application-name "$CODE_DEPLOY_APPLICATION" | jq '.deploymentGroups[]' | tr -d '"'))
for DEPLOYMENT_GROUP in "${DEPLOYMENT_GROUP_LIST[@]}"
do
    echo "Deleting deployment group ${DEPLOYMENT_GROUP}"
    $AWS deploy delete-deployment-group --application-name "$CODE_DEPLOY_APPLICATION" --deployment-group-name "${DEPLOYMENT_GROUP}"
done


VPCE_CONNECTION_MAP="$($AWS ec2 describe-vpc-endpoint-connections | jq '.VpcEndpointConnections | map({id: .VpcEndpointId, service: .ServiceId})')"
VPCE_SERVICE_LIST=($(echo "${VPCE_CONNECTION_MAP}" | jq '.[] | .service' | tr -d '"'))
for VPCE_SERVICE in "${VPCE_SERVICE_LIST[@]}"
do
    echo "Rejecting VPCE connection for service ${VPCE_SERVICE}"
    VPCE_ENDPOINT_LIST=($(echo "${VPCE_CONNECTION_MAP}" | jq ".[] | select(.service == \"${VPCE_SERVICE}\") | .id" |  tr -d '"'))
    for VPCE_ENDPOINT in "${VPCE_ENDPOINT_LIST[@]}"
    do
        echo "Connections to reject: ${VPCE_ENDPOINT}"
        $AWS ec2 reject-vpc-endpoint-connections --service-id "${VPCE_SERVICE}" --vpc-endpoint-ids "${VPCE_ENDPOINT}" || true
    done

    echo "Deleting VPCE service service ${VPCE_SERVICE}"
    $AWS ec2 delete-vpc-endpoint-service-configurations --service-ids "${VPCE_SERVICE}"
done


#NLB_LIST_FILE=$(mktemp)

# Step 1
NLB_LIST=($($AWS elbv2 describe-load-balancers \
    | jq '.LoadBalancers[] | .LoadBalancerArn' \
    | tr -d '"' | tr "\n" ' '))

for NLB in "${NLB_LIST[@]}"
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

CLUSTER_LIST=($($AWS ecs list-clusters \
    | jq '.clusterArns | .[]' \
    | tr -d '"' | tr "\n" ' '))

for CLUSTER in "${CLUSTER_LIST[@]}"
do
    echo "Processing cluster ${CLUSTER}"

    SERVICE_LIST=($($AWS ecs list-services --cluster "${CLUSTER_ARN}" \
        | jq '.serviceArns | .[]' \
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

TARGET_GROUP_LIST=($($AWS elbv2 describe-target-groups \
    | jq '.TargetGroups | .[] | .TargetGroupArn' \
    | tr -d '"'))
for TARGET_GROUP in "${TARGET_GROUP_LIST[@]}"
do
    echo "Deleting target group ${TARGET_GROUP}"
    $AWS elbv2 delete-target-group --target-group-arn "${TARGET_GROUP}"
done

SECURITY_GROUP_LIST=($(\
    $AWS ec2 describe-security-groups \
        | jq '.SecurityGroups | .[] | select(.IpPermissions[0].FromPort = 21891) | .GroupId' \
        | tr -d '"' \
))
for SECURITY_GROUP in "${SECURITY_GROUP_LIST[@]}"
do
    echo "Deleting security group ${SECURITY_GROUP}"
    $AWS ec2 delete-security-group --group-id "${SECURITY_GROUP}"
done

IAM_ROLE_LIST=($(\
    $AWS iam list-roles \
        | jq '.Roles | map(select(.RoleName | endswith("-cp-role")) | .RoleName) | .[]' \
        | tr -d '"' \
))
for IAM_ROLE in "${IAM_ROLE_LIST[@]}"
do
    POLICY_NAME_LIST=($($AWS iam list-role-policies --role-name ${IAM_ROLE} |jq '.PolicyNames[]' | tr -d '"'))
    for POLICY_NAME in "${POLICY_NAME_LIST[@]}"
    do
        echo "Deleting Role Policy ${POLICY_NAME}"
        $AWS iam delete-role-policy --role-name ${IAM_ROLE} --policy-name ${POLICY_NAME}
    done
    echo "Deleting IAM role ${IAM_ROLE}"
    $AWS iam delete-role --role-name "${IAM_ROLE}"
done

ROUTE_TABLE_LIST=($($AWS ec2 describe-route-tables | jq '.RouteTables[] | select(.VpcId != "${DEFAULT_VPC}" and .VpcId != "${BASE_VPC}") | .RouteTableId' | tr -d '"'))
for ROUTE_TABLE in "${ROUTE_TABLE_LIST[@]}"
do
    echo "Deleting route table ${ROUTE_TABLE} route 0.0.0.0/0"
    $AWS ec2 delete-route --route-table-id "${ROUTE_TABLE}" --destination-cidr-block "0.0.0.0/0"
done

NAT_GATEWAY_LIST=($($AWS ec2 describe-nat-gateways | jq '.NatGateways[] | select(.VpcId != "${DEFAULT_VPC}" and .VpcId != "${BASE_VPC}") | .NatGatewayId' | tr -d '"'))
for NAT_GATEWAY in "${NAT_GATEWAY_LIST[@]}"
do
    echo "Deleting NAT gateway ${NAT_GATEWAY}"
    $AWS ec2 delete-nat-gateway --nat-gateway-id "${NAT_GATEWAY}"
done

EIP_ASSOCIATION_LIST=($($AWS ec2 describe-addresses | jq '.Addresses[] | select((.Tags[] | select(.Key | contains("aws:cloudformation:stack-name"))) == 0) | .AssociationId' | tr -d '"'))
for ASSOCIATION in "${EIP_ASSOCIATION_LIST[@]}"
do
    echo "Disassociate association ${ASSOCIATION}"
    $AWS ec2 disassociate-address --association-id "${ASSOCIATION}"
done

EIP_ALLOCATION_LIST=($($AWS ec2 describe-addresses | jq '.Addresses[] | select((.Tags[] | select(.Key | contains("aws:cloudformation:stack-name"))) == 0) | .AssociationId' | tr -d '"'))
for ALLOCATION in "${EIP_ALLOCATION_LIST[@]}"
do
    echo "Releasing allocation ${ALLOCATION}"
    $AWS ec2 release-address --allocation-id "${ALLOCATION}"
done

INTERNET_GATEWAY_LIST=($($AWS ec2 describe-internet-gateways | jq '.InternetGateways[] | select((.Attachments[] | select(.VpcId != "${DEFAULT_VPC}" and .VpcId != "${BASE_VPC}")) != 0) | .InternetGatewayId' | tr -d '"'))
for INTERNET_GATEWAY in "${INTERNET_GATEWAY_LIST[@]}"
do
    VPC_ID=($($AWS ec2 describe-internet-gateways --internet-gateway-ids "${INTERNET_GATEWAY}" | jq '.InternetGateways[] | .Attachments[] | .VpcId' | tr -d '"'))
    echo "Detaching internet gateway ${INTERNET_GATEWAY} from vpc-id ${VPC_ID}"
    $AWS ec2 detach-internet-gateway --internet-gateway-id "${INTERNET_GATEWAY}" --vpc-id  "${VPC_ID}"
    echo "Deleting internet gateway ${INTERNET_GATEWAY}"
    $AWS ec2 delete-internet-gateway --internet-gateway-id "${INTERNET_GATEWAY}"
done

SUBNET_LIST=($($AWS ec2 describe-subnets | jq '.Subnets[] | select(.VpcId != "${DEFAULT_VPC}" and .VpcId != "${BASE_VPC}") | .SubnetId' | tr -d '"'))
for SUBNET in "${SUBNET_LIST[@]}"
do
    echo "Deleting subnet ${SUBNET}"
    $AWS ec2 delete-subnet --subnet-id "${SUBNET}"
done

ROUTE_TABLE_LIST=($($AWS ec2 describe-route-tables | jq '.RouteTables[] | select(.VpcId != "${DEFAULT_VPC}" and .VpcId != "${BASE_VPC}") | .RouteTableId' | tr -d '"'))
for ROUTE_TABLE in "${ROUTE_TABLE_LIST[@]}"
do
    echo "Deleting route table ${ROUTE_TABLE}"
    $AWS ec2 delete-route-table --route-table-id "$ROUTE_TABLE"
done

VPC_LIST=($($AWS ec2 describe-vpcs | jq '.Vpcs[] | .VpcId' | tr -d '"' | grep -vE "${DEFAULT_VPC}|${BASE_VPC}"))
for VPC in "${VPC_LIST[@]}"
do
    echo "Deleting vpc ${VPC}"
    $AWS ec2 delete-vpc --vpc-id "${VPC}"
done

SERVICE_DISCOVERY_LIST=$($(AWS servicediscovery list-services | jq '.Services[] | .Id' | tr -d '"'))
for SERVICE_DISCOVERY in "${SERVICE_DISCOVERY_LIST[@]}"
do
    echo "Deleting service discovery service ${SERVICE_DISCOVERY}"
    $AWS servicediscovery delete-service --id "$SERVICE_DISCOVERY"
done
