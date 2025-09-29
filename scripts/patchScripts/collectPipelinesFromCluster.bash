#!/bin/bash

while getopts ":c:" opt; do
  case $opt in
    c)
      ACCOUNT_ID=$OPTARG
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      exit 1
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      exit 1
      ;;
  esac
done

# This scripts collects all of the pipelines that are in ACTIVE status and have a version 2.x
# It then writes them to a CSV file for the patchAllInteractive script to use
# USE WITH CAUTION: SCANS A WHOLE DYNAMO DB TABLE!

# Usage: bash collectPipelinesToPatch.bash -c <ACCOUNT_ID>


if [ -z "$ACCOUNT_ID" ]; then
    echo "Please provide account ID using -c option"
    exit 1
fi

if [ -z $REGION ]; then
    echo "export the REGION variable before running"
    exit 1
fi

if [ -z $STAGE ]; then
    echo "export the STAGE variable before running"
    exit 1
fi

OUTPUT_FILE="$REGION-$STAGE-cell-$ACCOUNT_ID-patch-list.csv"
HEADER_WRITTEN=false

# Make an ada call to get credentials to readonly role of the provided account
ada credentials update --account $ACCOUNT_ID --role ReadOnly --provider isengard --once

# Make an aws call to get the ECS Clusters currently available (use in memory variables)
temp_clusters=$(aws ecs list-clusters --region $REGION --output json --query 'clusterArns[]')

# Find the first cluster that starts with "FizzyDrPepper-EcsCluster-prod"
first_cluster=$(echo "$temp_clusters" | jq -r '.[]' | grep -m 1 "^arn:aws:ecs:$REGION:$ACCOUNT_ID:cluster/FizzyDrPepper-EcsCluster-$STAGE")

# List all of the active service names in the first_cluster and save them to an array
services=$(aws ecs list-services --cluster $first_cluster --region $REGION --output json --query 'serviceArns[]' | jq -r '.[]' | sort)

# Get onoly the service name from the service arn (this is the last piece split by the '/' array)
services=$(echo "$services" | awk -F '/' '{print $NF}' | sort)

for service in $services; do
    # Get the index of the rightmost '-' character
    index=-1
    service_string_length=${#service}

    for (( i=0; i<service_string_length; i++ )); do
        char="${service:i:1}"
        if [ "$char" == "-" ]; then
            index=$i
        fi
    done

    # if index is -1, then the service name is not in the correct format
    if [ $index -eq -1 ]; then
        echo "Service name $service is not in the correct format"
        continue
    fi

    # Split the service by that index, making the left side the service_name and the right side the service_account
    service_name=${service:0:$index}

    # Get account id from index to end
    service_account=${service:$index + 1}

    pipeline_arn="arn:aws:osis:$REGION:$service_account:pipeline/$service_name"
    
    # Write header (only once)
    if ! $HEADER_WRITTEN; then
        echo "PipelineArn,Version,IsPatchTriggered,PatchStatus" > "$OUTPUT_FILE"
        HEADER_WRITTEN=true
    fi

    # Write rows to the CSV file, leave version empty
    echo "$pipeline_arn,na,0,0" >> "$OUTPUT_FILE"


done