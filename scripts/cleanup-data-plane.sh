#!/bin/bash

# Pre-req
# install https://w.amazon.com/bin/view/AmazonAwsCli/Cookbook#IsengardPlugin
# pip install --upgrade git+ssh://git.amazon.com/pkg/BenderLibIsengard

# beta
AWS_ACCOUNT="701817888140"
AWS_ROLE="Admin-OneClick"
CLUSTER_ARN="arn:aws:ecs:us-east-1:701817888140:cluster/FizzyDrPepper-EcsCluster-beta-us-east-1-cell1-ClusterEB0386A7-hrV96abetIpU"

# AWS_ACCOUNT="${SMBAYER_ACCOUNT}"
# AWS_ROLE="Admin"
# CLUSTER_ARN="arn:aws:ecs:us-east-1:166683070329:cluster/Turkey-166683070329"

DESIRED_COUNT="0"
SERVICE_QUERY='.service | .serviceName + " " + .status'

EXAMPLE_LIST=(
    "item a" \
    "item b"
)


DEPLOYMENT_GROUP_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" deploy list-deployment-groups --application-name "FizzyDrPepper-CodeDeploy-beta-us-east-1-cell1-CodeDeployApplicationE587C27C-1IHEUWX7XA7YG" | jq '.deploymentGroups[]' | tr -d '"'))
for DEPLOYMENT_GROUP in "${DEPLOYMENT_GROUP_LIST[@]}"
do
    echo "Deleting deployment group ${DEPLOYMENT_GROUP}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" deploy delete-deployment-group --application-name "FizzyDrPepper-CodeDeploy-beta-us-east-1-cell1-CodeDeployApplicationE587C27C-1IHEUWX7XA7YG" --deployment-group-name "${DEPLOYMENT_GROUP}"
done


VPCE_CONNECTION_MAP="$(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-vpc-endpoint-connections | jq '.VpcEndpointConnections | map({id: .VpcEndpointId, service: .ServiceId})')"
VPCE_SERVICE_LIST=($(echo "${VPCE_CONNECTION_MAP}" | jq '.[] | .service' | tr -d '"'))
for VPCE_SERVICE in "${VPCE_SERVICE_LIST[@]}"
do
    echo "Rejecting VPCE connection for service ${VPCE_SERVICE}"
    VPCE_ENDPOINT_LIST=($(echo "${VPCE_CONNECTION_MAP}" | jq ".[] | select(.service == \"${VPCE_SERVICE}\") | .id" |  tr -d '"'))
    for VPCE_ENDPOINT in "${VPCE_ENDPOINT_LIST[@]}"
    do
        echo "Connections to reject: ${VPCE_ENDPOINT}"
        isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 reject-vpc-endpoint-connections --service-id "${VPCE_SERVICE}" --vpc-endpoint-ids "${VPCE_ENDPOINT}" || true
    done

    echo "Deleting VPCE service service ${VPCE_SERVICE}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 delete-vpc-endpoint-service-configurations --service-ids "${VPCE_SERVICE}"
done


#NLB_LIST_FILE=$(mktemp)

# Step 1
NLB_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" elbv2 describe-load-balancers \
    | jq '.LoadBalancers[] | .LoadBalancerArn' \
    | tr -d '"' | tr "\n" ' '))

for NLB in "${NLB_LIST[@]}"
do
    echo "Processing NLB ${NLB}"

    LISTENER_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" elbv2 describe-listeners --load-balancer-arn "${NLB}" \
        | jq '.Listeners[] | .ListenerArn' \
        | tr -d '"'))

    for LISTENER in "${LISTENER_LIST[@]}"
    do
        echo "Deleting listener ${LISTENER}"
        isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" elbv2 delete-listener --listener-arn "${LISTENER}"
    done

    echo "Deleting load balancer ${NLB}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" elbv2 delete-load-balancer --load-balancer-arn "${NLB}"
    echo "---"
done

CLUSTER_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ecs list-clusters \
    | jq '.clusterArns | .[]' \
    | tr -d '"' | tr "\n" ' '))

for CLUSTER in "${CLUSTER_LIST[@]}"
do
    echo "Processing cluster ${CLUSTER}"

    SERVICE_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ecs list-services --cluster "${CLUSTER_ARN}" \
        | jq '.serviceArns | .[]' \
        | tr -d '"'))
    
    for SERVICE in "${SERVICE_LIST[@]}"
    do
        echo "Updating service ${SERVICE} to desired count ${DESIRED_COUNT}"
        isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ecs update-service --desired-count "${DESIRED_COUNT}" --cluster "${CLUSTER}" --service "${SERVICE}" \
            | jq "${SERVICE_QUERY}"

         echo "Deleting service ${SERVICE}"
         isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ecs delete-service --cluster "${CLUSTER}" --service "${SERVICE}" | jq "${SERVICE_QUERY}"
    done
done

TARGET_GROUP_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" elbv2 describe-target-groups \
    | jq '.TargetGroups | .[] | .TargetGroupArn' \
    | tr -d '"'))
for TARGET_GROUP in "${TARGET_GROUP_LIST[@]}"
do
    echo "Deleting target group ${TARGET_GROUP}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" elbv2 delete-target-group --target-group-arn "${TARGET_GROUP}"
done

SECURITY_GROUP_LIST=($(\
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-security-groups \
        | jq '.SecurityGroups | .[] | select(.IpPermissions[0].FromPort = 21891) | .GroupId' \
        | tr -d '"' \
))
for SECURITY_GROUP in "${SECURITY_GROUP_LIST[@]}"
do
    echo "Deleting security group ${SECURITY_GROUP}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 delete-security-group --group-id "${SECURITY_GROUP}"
done

IAM_ROLE_LIST=($(\
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" iam list-roles \
        | jq '.Roles | map(select(.RoleName | endswith("-cp-role")) | .RoleName) | .[]' \
        | tr -d '"' \
))
for IAM_ROLE in "${IAM_ROLE_LIST[@]}"
do
    POLICY_NAME_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" iam list-role-policies --role-name ${IAM_ROLE} |jq '.PolicyNames[]' | tr -d '"'))
    for POLICY_NAME in "${POLICY_NAME_LIST[@]}"
    do
        echo "Deleting Role Policy ${POLICY_NAME}"
        isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" iam delete-role-policy --role-name ${IAM_ROLE} --policy-name ${POLICY_NAME}
    done
    echo "Deleting IAM role ${IAM_ROLE}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" iam delete-role --role-name "${IAM_ROLE}"
done

ROUTE_TABLE_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-route-tables | jq '.RouteTables[] | .RouteTableId' | tr -d '"'))
for ROUTE_TABLE in "${ROUTE_TABLE_LIST[@]}"
do
    echo "Deleting route table ${ROUTE_TABLE} route 0.0.0.0/0"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 delete-route --route-table-id "${ROUTE_TABLE}" --destination-cidr-block "0.0.0.0/0"
done

NAT_GATEWAY_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-nat-gateways | jq '.NatGateways[] | .NatGatewayId' | tr -d '"'))
for NAT_GATEWAY in "${NAT_GATEWAY_LIST[@]}"
do
    echo "Deleting NAT gateway ${NAT_GATEWAY}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 delete-nat-gateway --nat-gateway-id "${NAT_GATEWAY}"
done

EIP_ASSOCIATION_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-addresses | jq '.Addresses[] | .AssociationId' | tr -d '"'))
for ASSOCIATION in "${EIP_ASSOCIATION_LIST[@]}"
do
    echo "Disassociate association ${ASSOCIATION}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 disassociate-address --association-id "${ASSOCIATION}"
done

EIP_ALLOCATION_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-addresses | jq '.Addresses[] | .AllocationId' | tr -d '"'))
for ALLOCATION in "${EIP_ALLOCATION_LIST[@]}"
do
    echo "Releasing allocation ${ALLOCATION}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 release-address --allocation-id "${ALLOCATION}"
done

INTERNET_GATEWAY_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-internet-gateways | jq '.InternetGateways[] | select((.Attachments[] | select(.VpcId != "vpc-4b0abe36")) != 0) | .InternetGatewayId' | tr -d '"'))
for INTERNET_GATEWAY in "${INTERNET_GATEWAY_LIST[@]}"
do
    VPC_ID=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-internet-gateways --internet-gateway-ids "${INTERNET_GATEWAY}" | jq '.InternetGateways[] | .Attachments[] | .VpcId' | tr -d '"'))
    echo "Detaching internet gateway ${INTERNET_GATEWAY} from vpc-id ${VPC_ID}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 detach-internet-gateway --internet-gateway-id "${INTERNET_GATEWAY}" --vpc-id  "${VPC_ID}"
    echo "Deleting internet gateway ${INTERNET_GATEWAY}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 delete-internet-gateway --internet-gateway-id "${INTERNET_GATEWAY}"
done

SUBNET_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-subnets | jq '.Subnets[] | select(.VpcId != "vpc-0c62aec1df1c7368b" and .VpcId != "vpc-4b0abe36") | .SubnetId' | tr -d '"'))
for SUBNET in "${SUBNET_LIST[@]}"
do
    echo "Deleting subnet ${SUBNET}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 delete-subnet --subnet-id "${SUBNET}"
done

VPC_LIST=($(isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 describe-vpcs | jq '.Vpcs[] | .VpcId' | tr -d '"' | grep -vE "vpc-0c62aec1df1c7368b|vpc-4b0abe36"))
for VPC in "${VPC_LIST[@]}"
do
    echo "Deleting vpc ${VPC}"
    isengard aws "${AWS_ACCOUNT}" "${AWS_ROLE}" ec2 delete-vpc --vpc-id "${VPC}"
done
