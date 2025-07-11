#!/bin/bash

# This script is used to patch all pipelines in a given region and stage, given that the relvant CSV file exists. 
# It also excludes certain accounts from the patching process. This script requires user input to continue after each batch.
# USE WITH CAUTION: Can affect a large amount of pipelines. 

set +x

set -e

if [ -z $REGION ]; then
    echo "export the REGION variable before running"
    exit 1
fi

if [ -z $STAGE ]; then
    echo "export the STAGE variable before running"
    exit 1
fi

args=("$@")
BATCH_SIZE=10
nextArg=0
while [[ $nextArg -lt $# ]]; do
    arg=${args[$nextArg]}
    if [[ $arg == "--batch-size" ]]; then
        nextArg=$nextArg+1
        BATCH_SIZE=${args[$nextArg]}
    fi
    nextArg=$nextArg+1
done

CSV_FILE="$REGION-$STAGE-patch-list.csv"

# Check if the CSV file exists
if [ ! -f "$CSV_FILE" ]; then
    echo "CSV file not found: $CSV_FILE"
    echo "Run the collectPipelinesToPatch.bash script first"
    exit 1
fi

# Exclude large accounts when patching all -- adjust based on necessity / severity of patch
EXCLUDED=("734288611257" "260923687808" "149815134901" "721467712305" "620939711707" "446163835440" \
          "691504454456" "744614460006" "763284681916" "695141026374" "861276102679" "361769597299")

# Initialize tracking variables
current_line=2
total_lines=$(wc -l < "$CSV_FILE")

while [[ $current_line -le $total_lines ]]; do
    echo "Reading batch starting from line $current_line..."

    # Read the next batch of lines
    batch=()
    skipped=0  # Track how many lines we skip

    while [[ ${#batch[@]} -lt $BATCH_SIZE ]] && IFS= read -r line; do
        pipeline_arn=$(echo "$line" | cut -d ',' -f 1 | tr -d '"')
        account_id=$(echo "$pipeline_arn" | cut -d':' -f5)  # Extract account number

        # Check if the account is in the exclusion list
        if [[ " ${EXCLUDED[*]} " =~ " ${account_id} " ]]; then
            echo "Skipping excluded account for pipeline: $pipeline_arn"
            skipped=$((skipped+1))
            continue  # Skip this entry, but keep reading more lines
        fi

        batch+=("$line")  # Add to batch only if not excluded
    done < <(tail -n +"$current_line" "$CSV_FILE") 

    # Print the batch for verification
    for line in "${batch[@]}"; do
        echo "$line"
    done

    choice="null"
    while [[ $choice != "y" && $choice != "n" ]]; do
        read -p "Would you like to process this batch? (y/n): " choice
    done

    pipeline_args_list=""
    if [[ $choice == "y" ]]; then
        first_batch_element=1
        for line in "${batch[@]}"; do
            # Extract the pipeline name from the line
            pipeline_name=$(echo "$line" | cut -d ',' -f 1 | tr -d '"')
            if [[ $first_batch_element -eq 1 ]]; then
                pipeline_args_list=$pipeline_name
                first_batch_element=0
            else
                pipeline_args_list="$pipeline_args_list $pipeline_name"
            fi
        done

        pipeline_args_list=$(echo $pipeline_args_list | tr -d '"')

        echo "================================================"
        echo "Processing batch"
        
        # Track deployment
        echo "    Deploying batch: $pipeline_args_list"
        deployResponse=$(bash deployPipelinesPatchErrorHandling.bash $pipeline_args_list)
        echo "    Deployment response: $deployResponse"
        deployment_id=$(echo $deployResponse | jq -r ".deploymentId")
        echo "    Tracking deployment: $deployment_id"
        if [[ $deployment_id == "null" ]]; then
            echo "    Deployment failed for batch."
        else
            for pipeline_arn in $pipeline_args_list; do
                bash updateDeploymentStatusCsv.bash $pipeline_arn t
            done

            bash monitorDeployment.bash $deployment_id
        fi

    else
        echo "Skipping this batch."
    fi

    # Wait for user to press Enter to continue
    read -p "Press Enter to read the next batch..."
    echo "================================================"

    # Update the line counter
    current_line=$((current_line + ${#batch[@]} + skipped))
done

echo "All lines have been read."

