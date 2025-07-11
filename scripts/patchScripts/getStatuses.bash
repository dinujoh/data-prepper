#!/bin/bash

# This script gets the statuses of pipeline patching deployments

mainDID="$1"

if [ -z $REGION ]; then
    echo "export the REGION variable before running"
    exit 1
fi

if [ -z $STAGE ]; then
    echo "export the STAGE variable before running"
    exit 1
fi

endpoint="https://api.$STAGE.$REGION.fizzy-josta.searchservices.aws.dev/getBatchStatus"

# when stage is gamma, the endpoint needs to be slightly different
if [[ "$STAGE" == "gamma" && "$REGION" == "us-west-2" ]]; then
    endpoint="https://38jyc2dgw9.execute-api.us-west-2.amazonaws.com/gamma/getBatchStatus"
fi

# when stage is gamma, the endpoint needs to be slightly different
if [[ "$STAGE" == "gamma" && "$REGION" == "us-east-1" ]]; then
    endpoint="https://api.gamma.us-east-1.fizzy-josta.aos-internal.aws.dev/getBatchStatus"
fi

if [[ "$STAGE" == "beta" && "$REGION" == "us-east-1" ]]; then
    endpoint="https://api.beta.us-east-1.fizzy-josta.aos-internal.aws.dev/getBatchStatus"
fi


mainDIDStatus=$(awscurl -X GET --service execute-api --region $REGION $endpoint/$mainDID)
echo $mainDIDStatus | jq

subDeployments=$(echo $mainDIDStatus | jq -r '.deployments[]')



