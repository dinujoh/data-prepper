#!/bin/bash

# This scripts collects all of the pipelines that are in ACTIVE status and have a version 2.x
# It then writes them to a CSV file for the patchAllInteractive script to use
# USE WITH CAUTION: SCANS A WHOLE DYNAMO DB TABLE!

# Usage: bash collectPipelinesToPatch.bash

if [ -z $REGION ]; then
    echo "export the REGION variable before running"
    exit 1
fi

if [ -z $STAGE ]; then
    echo "export the STAGE variable before running"
    exit 1
fi



TABLE_NAME="DataPrepperPipelineConfigurations"
OUTPUT_FILE="$REGION-$STAGE-patch-list.csv"
LIMIT=300

# Initialize variables
LAST_EVALUATED_KEY=""
HEADER_WRITTEN=false

echo "Starting DynamoDB scan..."

# Ensure the output file doesn't exist
if [ -f "$OUTPUT_FILE" ]; then
    rm "$OUTPUT_FILE"
fi

FILTER_EXPRESSION="lifecycleStatus IN (:active, :urc) AND begins_with(version, :prefix)"
PROJECTION_EXPRESSION="#PA, #V, #CB"
EXPRESSION_ATTRIBUTE_NAMES='{"#PA": "pipelineArn", "#V": "version", "#CB": "pipelineConfigurationBody"}'
EXPRESSION_ATTRIBUTE_VALUES='{":active": {"S": "ACTIVE"}, ":urc": {"S": "UPDATE_ROLLBACK_COMPLETE"}, ":prefix": {"S":"2."}}'


# Loop to scan DynamoDB
while :; do
    if [ -z "$LAST_EVALUATED_KEY" ]; then
        RESPONSE=$(aws dynamodb scan \
            --table-name "$TABLE_NAME" \
            --filter-expression "$FILTER_EXPRESSION" \
            --projection-expression "$PROJECTION_EXPRESSION" \
            --expression-attribute-names "$EXPRESSION_ATTRIBUTE_NAMES" \
            --expression-attribute-values "$EXPRESSION_ATTRIBUTE_VALUES" \
            --region "$REGION" \
            --limit "$LIMIT")
    else
        RESPONSE=$(aws dynamodb scan \
            --table-name "$TABLE_NAME" \
            --filter-expression "$FILTER_EXPRESSION" \
            --projection-expression "$PROJECTION_EXPRESSION" \
            --expression-attribute-names "$EXPRESSION_ATTRIBUTE_NAMES" \
            --expression-attribute-values "$EXPRESSION_ATTRIBUTE_VALUES" \
            --region "$REGION" \
            --limit "$LIMIT" \
            --exclusive-start-key "$LAST_EVALUATED_KEY")
    fi

    # Check for errors
    if [ $? -ne 0 ]; then
        echo "Error occurred during the DynamoDB scan."
        exit 1
    fi

    # Extract Items
    ITEMS=$(echo "$RESPONSE" | jq -r '.Items')

    # Check if ITEMS is an array
    if [[ "$ITEMS" == "null" || -z "$ITEMS" ]]; then
        echo "No items found in the response."
        exit 1
    fi

    # Write header (only once)
    if ! $HEADER_WRITTEN; then
        echo "PipelineArn,Version,IsPatchTriggered,PatchStatus" > "$OUTPUT_FILE"
        HEADER_WRITTEN=true
    fi

    # Write rows to the CSV file
    echo "$ITEMS" | jq -r '.[] | [.pipelineArn.S, .version.S, 0, 0] | @csv' | tr -d '"' >> "$OUTPUT_FILE"


    # Check if there is a LastEvaluatedKey
    LAST_EVALUATED_KEY=$(echo "$RESPONSE" | jq -r '.LastEvaluatedKey | @json')

    if [ "$LAST_EVALUATED_KEY" == "null" ] || [ -z "$LAST_EVALUATED_KEY" ]; then
        break
    fi

done

rm -rf temp_items.json

echo "DynamoDB scan completed. Results saved to $OUTPUT_FILE"