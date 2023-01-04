#!/bin/bash

# Example usage: ./delete_all_pipelines_for_account.sh -a 303581101768 \
#                                                      -r us-west-2 \
#                                                      -i Admin
#                                                      -s prod
#           -a -> The AWS account id that contains the pipelines to be deleted
#           -r -> The region that contains the pipelines to be deleted
#           -i -> The role from the AWS account that should be assumed for permissions to delete pipelines
#           -s -> The stage that the pipelines to be deleted exist in. Defaults to prod

# Note: To use the awscli for osis at this time, you must follow steps 2, 3, and 4 from this quip (https://quip-amazon.com/9MKHAW0S6CSi/OSIS-AWS-CLI-Testing-Setup)


getEndpointUrl() {
  stage=$1
  region=$2

  if [[ $stage == "prod" ]]; then
    echo "https://osis.${region}.amazonaws.com"
  else
    echo "https://osis-${stage}.${region}.amazonaws.com"
  fi
}

# Default stage is prod
STAGE="prod"

while getopts a:r:i:s: flag
do
    case "${flag}" in
        a) AWS_ACCOUNT=${OPTARG};;
        r) AWS_REGION=${OPTARG};;
        i) AWS_ROLE=${OPTARG};;
        s) STAGE=${OPTARG};;
        *) exit 1
    esac
done

ada credentials update --account=$AWS_ACCOUNT --provider=isengard --role=$AWS_ROLE --once

ENDPOINT_URL=$(getEndpointUrl "$STAGE" "$AWS_REGION")

PIPELINE_NAMES=($(aws osis list-pipelines --region $AWS_REGION --endpoint-url $ENDPOINT_URL | jq '.Pipelines[] | .PipelineName' | tr -d '"'))

for PIPELINE_NAME in "${PIPELINE_NAMES[@]}"
do
    if aws --region $AWS_REGION osis delete-pipeline  --endpoint-url $ENDPOINT_URL --pipeline-name ${PIPELINE_NAME}; then
    	echo "Successfully deleted pipeline ${PIPELINE_NAME}"
    else 
    	echo "Failed to delete pipeline ${PIPELINE_NAME}"
    fi	
    sleep 20
done