#!/bin/bash

# This scripts triggers a patch for a given list of pipelines and returns the deployment ID
# USE WITH CAUTION: CAN AFFECT CUSTOMER PIPELINES

args=("$@")

json_array="[\"$(printf '%s","' "${args[@]}" | sed 's/,"$//')]"
# echo "Updating pipelines: $json_array"

if [ -z $REGION ]; then
    echo "export the REGION variable before running"
    exit 1
fi

if [ -z $STAGE ]; then
    echo "export the STAGE variable before running"
    exit 1
fi

TABLE_NAME="DataPrepperPipelineConfigurations"

endpoint="https://api.$STAGE.$REGION.fizzy-josta.searchservices.aws.dev/deployPipelines"

# when stage is gamma, the endpoint needs to be slightly different
if [[ "$STAGE" == "gamma" && "$REGION" == "us-west-2" ]]; then
    endpoint="https://38jyc2dgw9.execute-api.us-west-2.amazonaws.com/gamma/deployPipelines"
fi

# when stage is gamma, the endpoint needs to be slightly different
if [[ "$STAGE" == "gamma" && "$REGION" == "us-east-1" ]]; then
    endpoint="https://api.gamma.us-east-1.fizzy-josta.aos-internal.aws.dev/deployPipelines"
fi

if [[ "$STAGE" == "beta" && "$REGION" == "us-east-1" ]]; then
    endpoint="https://api.beta.us-east-1.fizzy-josta.aos-internal.aws.dev/deployPipelines"
fi

deploy_result=$(awscurl -X POST --service execute-api --region $REGION $endpoint -d '{"pipelines": '${json_array}', "targetVersion": {"version":"2.8"}, "dataPlaneOnly": "true"}' | jq)
if [[ "$deploy_result" == "{}" ]]; then
    
    pipelines=$(echo $json_array | jq -r '.[]')
    new_array="["
    for pipeline in $pipelines; do
        pipeline_arn=$(echo $pipeline | tr -d '"')
        account_id=$(echo "$pipeline_arn" | cut -d':' -f5)
        pipeline_name=$(echo "$pipeline_arn" | cut -d'/' -f2)

        pipeline_row=$(aws dynamodb get-item \
            --table-name "$TABLE_NAME" \
            --key "{\"accountId\": {\"S\": \"$account_id\"}, \"pipelineName\": {\"S\": \"$pipeline_name\"}}" \
            --region "$REGION" \
            --output json | jq)

        if [[ ! -z $pipeline_row ]]; then
            lifecycle_status=$(echo $pipeline_row | jq -r '.Item.lifecycleStatus.S')
            if [[ "$lifecycle_status" == "ACTIVE" ]]; then
                new_array="$new_array\"$pipeline\","
            fi
        fi
    done

    new_array="${new_array%,}]"

    if [[ $new_array == "[]" ]]; then
        echo "No active pipelines to deploy"
    fi

    deploy_result=$(awscurl -X POST --service execute-api --region $REGION $endpoint -d '{"pipelines": '${new_array}', "targetVersion": {"version":"2.8"}, "dataPlaneOnly": "true"}' | jq)
    echo $deploy_result | jq
else 
    echo $deploy_result | jq
fi
