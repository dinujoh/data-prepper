#!/bin/bash

# This script is a helper script to update the pipeline tracking CSV during a patchAllPipelines run. 

# args check
pipeline_arn=$1
if [ -z $pipeline_arn ]; then
    echo "Give the pipeline arn as the first argument"
    exit 1
fi

update_type=$2
if [ -z $update_type ]; then
    echo "Give the update column (Trigger or Status) as the second argument, 't' or 's'"
    exit 1
fi
if [[ $update_type != "t" && $update_type != "s" ]]; then
    echo "Update type must be 't' or 's'"
    exit 1
fi
if [[ $update_type == "s" ]]; then
    status=$3
    if [ -z $status ]; then
        echo "Give the status as the third argument. \n s: skipped, p: provisioned, iP: inProgress, f: failed, c: complete, a: aborted"
        exit 1
    fi
    if [[ $status != "s" && $status != "p" && $status != "iP" && $status != "f" && $status != "c" && $status != "a" ]]; then
        echo "Status must be 's', 'p', 'iP', 'f', 'c' or 'a'"
        exit 1
    fi
fi


# Exported variables check
if [ -z $REGION ]; then
    echo "export the REGION variable before running"
    exit 1
fi
if [ -z $STAGE ]; then
    echo "export the STAGE variable before running"
    exit 1
fi

# Check if the CSV file exists
CSV_FILE="$REGION-$STAGE-patch-list.csv"
if [ ! -f "$CSV_FILE" ]; then
    echo "CSV file not found: $CSV_FILE"
    echo "Run the collectPipelinesToPatch.bash script first"
    exit 1
fi


# Variables
if [[ $update_type == "t" ]]; then
    column_number=3
    success_message="Labelled as triggered for pipeline: "
    flag_value="1"     # The new value to replace the column content
elif [[ $update_type == "s" ]]; then
    column_number=4
    success_message="Labelled patch status as $status for pipeline: "
    flag_value=$status     # The new value to replace the column content
fi


# Update the CSV file
awk -F, -v OFS=, -v id="$pipeline_arn" -v col="$column_number" -v value="$flag_value" -v succ_message="$success_message" '
BEGIN { 
    updated = 0 
}
{
    if ($1 == id) {
        $col = value
        updated = 1
    }
}
{ print }
END {
    if (updated == 0) {
        print "        Pipeline not found in csv: " id > "/dev/stderr"
    } 
}' "$CSV_FILE" > "tmp_file_for_$CSV_FILE" && mv "tmp_file_for_$CSV_FILE" "$CSV_FILE"